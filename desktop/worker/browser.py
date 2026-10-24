"""Real, isolated Google/Gemini browser sign-in over CDP.

Pending windows are reservations, not accounts. Only an observed authenticated
chat session plus a successful headless probe may commit a SQLite account.
Cookies remain in Chrome's persistent local profile, never in JSON or the UI.
"""
from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import gemini_cdp
from .gemini_cdp import PROVIDER_LABELS, CDPSession

POLL_SECONDS = 1.5
FLOW_TIMEOUT = 15 * 60
CLOSED_EARLY_MSG = "The sign-in window was closed before sign-in finished."


def resolve_exe(provider: str) -> str:
    return gemini_cdp.resolve_exe(provider)


class LoginFlow:
    def __init__(self, provider: str, slot: int, port: int | None = None, user_data_dir: Path | None = None) -> None:
        self.id = uuid.uuid4().hex
        self.provider = provider
        self.slot = slot
        self.reserved_slot = slot
        self.port = port if port is not None else gemini_cdp.LOGIN_BASE_PORT + slot
        self.user_data_dir = user_data_dir if user_data_dir is not None else gemini_cdp.profile_dir(slot)
        self.pid: int | None = None
        self.state = "pending"
        self.email: str | None = None
        self.error: str | None = None
        self.label = "Google/Gemini sign-in"
        self.started = time.time()
        self.authentication_observed = False
        self.new_profile = False
        self.lock = threading.RLock()

    def public(self) -> dict:
        with self.lock:
            return {
                "ok": self.state in {"pending", "authenticated"},
                "flowId": self.id, "state": self.state, "email": self.email,
                "label": self.label, "provider": PROVIDER_LABELS.get(self.provider, self.provider),
                "port": self.port, "slot": self.slot, "profileDir": str(self.user_data_dir),
                "error": self.error, "elapsed": round(time.time() - self.started, 1),
            }


FLOWS: dict[str, LoginFlow] = {}
_flow_lock = threading.RLock()


def _record(flow: LoginFlow) -> dict:
    from . import gemini_pool
    return gemini_pool.POOL.register_cdp_profile(flow)


def _close_window(flow: LoginFlow, proc) -> None:
    if proc.poll() is not None:
        return
    info = gemini_cdp.devtools_version(flow.port) or {}
    if info.get("webSocketDebuggerUrl"):
        try:
            session = CDPSession(info["webSocketDebuggerUrl"], timeout=4)
            try:
                session.send("Browser.close", timeout=5)
            finally:
                session.close()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def probe_session(port: int) -> tuple[bool, dict | None]:
    for target in gemini_cdp.list_pages(port):
        if not gemini_cdp.is_app_url(target.get("url", "")) or not target.get("webSocketDebuggerUrl"):
            continue
        try:
            session = CDPSession(target["webSocketDebuggerUrl"], timeout=4)
            try:
                info = session.evaluate(gemini_cdp.SESSION_JS, timeout=8)
                cookies = gemini_cdp.session_cookies(session)
            finally:
                session.close()
        except Exception:
            continue
        if gemini_cdp.signed_in(info, cookies):
            return True, info
    return False, None


def _sensor(flow: LoginFlow, proc) -> None:
    from . import gemini_pool
    try:
        deadline = flow.started + FLOW_TIMEOUT
        while time.time() < deadline:
            time.sleep(POLL_SECONDS)
            with flow.lock:
                if flow.state != "pending":
                    return
            if proc.poll() is not None and not gemini_cdp.devtools_version(flow.port, timeout=0.5):
                with flow.lock:
                    if flow.state == "pending":
                        flow.state, flow.error = "closed", CLOSED_EARLY_MSG
                return
            ok, info = probe_session(flow.port)
            if not ok:
                continue
            with flow.lock:
                if flow.state != "pending":
                    return
                email = re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", str((info or {}).get("account") or ""))
                flow.email = email.group(0) if email else None
                flow.authentication_observed = True
            # Flush the visible profile before a separate headless Chrome probes
            # it. Never launch two processes concurrently on the same profile.
            _close_window(flow, proc)
            result = _record(flow)
            with flow.lock:
                if not result.get("ok") and flow.state == "pending":
                    flow.state, flow.error = "failed", result.get("reason") or "Account could not be registered."
            return
        with flow.lock:
            if flow.state == "pending":
                flow.state, flow.error = "closed", "Sign-in timed out. Please try again."
    except Exception as exc:
        with flow.lock:
            if flow.state == "pending":
                flow.state, flow.error = "failed", f"Sign-in could not be verified: {exc}"
    finally:
        try:
            _close_window(flow, proc)
        finally:
            try:
                if flow.new_profile and flow.state != "authenticated" and flow.user_data_dir.exists():
                    # A cancelled/failed NEW sign-in must not leave a captured
                    # Google session behind. Existing reconnect profiles stay intact.
                    shutil.rmtree(flow.user_data_dir)
            except OSError:
                with flow.lock:
                    flow.error = (flow.error or "Sign-in failed") + "; close the sign-in browser to clean up its unlinked profile."
            finally:
                gemini_pool.POOL.finish_login(flow.reserved_slot)


def spawn_login_flow(provider: str = "chrome", slot: int | None = None) -> dict:
    from . import gemini_pool
    if provider not in PROVIDER_LABELS:
        raise ValueError("Choose an installed supported browser")
    exe = resolve_exe(provider)
    with _flow_lock:
        pending = {f.reserved_slot for f in FLOWS.values() if f.state == "pending"}
        if slot is None:
            slot = gemini_pool.POOL.next_free_slot()
            while slot in pending:
                slot += 1
        if slot < 1 or slot > min(65535 - gemini_cdp.LOGIN_BASE_PORT, 65535 - gemini_cdp.CDP_BASE_PORT):
            raise ValueError("Account slot exceeds the local browser port range")
        if slot in pending:
            raise ValueError("A sign-in window for this account is already open")
        folder = gemini_cdp.profile_dir(slot)
        port = gemini_cdp.LOGIN_BASE_PORT + slot
        while port <= 65535 and not gemini_cdp.port_free(port):
            port += 1
        if port > 65535:
            raise RuntimeError("No local DevTools port is available")
        gemini_pool.POOL.reserve_login(slot)
        flow = LoginFlow(provider, slot, port, folder)
        FLOWS[flow.id] = flow
    try:
        flow.new_profile = not folder.exists()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        gemini_pool.POOL.release_profile(slot)
        proc = gemini_cdp.popen(gemini_cdp.chrome_args(exe, folder, port, headless=False), visible=True)
        flow.pid = proc.pid
        threading.Thread(target=_sensor, args=(flow, proc), daemon=True).start()
        return flow.public()
    except Exception:
        with flow.lock:
            flow.state = "failed"
        gemini_pool.POOL.finish_login(flow.reserved_slot)
        raise


def flow_status(flow_id: str) -> dict | None:
    with _flow_lock:
        flow = FLOWS.get(flow_id)
    return flow.public() if flow else None


def cancel_flow(flow_id: str) -> dict | None:
    with _flow_lock:
        flow = FLOWS.get(flow_id)
    if not flow:
        return None
    with flow.lock:
        if flow.state == "pending":
            flow.state, flow.error = "closed", CLOSED_EARLY_MSG
    # The sensor closes the window/verification client before releasing the
    # reservation, so cancelling cannot race a render on the same profile.
    return flow.public()
