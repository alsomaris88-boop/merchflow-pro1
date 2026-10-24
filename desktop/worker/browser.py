"""
MerchFlow Pro — "+ Add Gemini Account" login flow (isolated Chrome profile).

  spawn_login_flow("chrome")
    └─ real, visible Chrome window, driven over CDP:
         chrome --user-data-dir="desktop/profiles/account_<id>"
                --app="https://gemini.google.com/app"
                --remote-debugging-port=<login port>
    └─ the human signs in manually — password, 2FA, passkey all work because
       it is a genuine browser window, never an embedded/automated form
    └─ sensor polls every 1.5 s over CDP
         ├─ URL matches gemini.google.com/app AND the session is live
         │    → record the account in local SQLite + the provider pool
         │    → Browser.close (releases the profile lock) → state=authenticated
         └─ user closed the window first → state=closed

  Every later generation reopens the SAME profile with --headless=new
  (gemini_cdp.CdpGeminiClient), so credentials are never requested again.
  No cookies are copied out of the profile.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from . import gemini_cdp
from .gemini_cdp import PROVIDER_LABELS, CDPSession

POLL_SECONDS = 1.5
FLOW_TIMEOUT = 15 * 60  # generous window for 2FA, passkeys and recovery prompts
CLOSED_EARLY_MSG = "The sign-in window was closed before sign-in finished."


def resolve_exe(provider: str) -> str:  # kept for callers of the old module API
    return gemini_cdp.resolve_exe(provider)


class LoginFlow:
    def __init__(self, provider: str, slot: int, port: int, user_data_dir: Path) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.provider = provider
        self.slot = slot
        self.port = port
        self.user_data_dir = user_data_dir
        self.pid: int | None = None
        self.state = "pending"          # pending | authenticated | closed | failed
        self.email: str | None = None
        self.error: str | None = None
        self.label = f"Profile {slot:02d}"
        self.started = time.time()

    def public(self) -> dict:
        return {
            # ok=False only for terminal failures so the UI keeps polling while pending
            "ok": self.state in {"pending", "authenticated"},
            "flowId": self.id,
            "state": self.state,
            "email": self.email,
            "label": self.label,
            "provider": PROVIDER_LABELS.get(self.provider, self.provider),
            "port": self.port,
            "slot": self.slot,
            "profileDir": str(self.user_data_dir),
            "error": self.error,
            "elapsed": round(time.time() - self.started, 1),
        }


FLOWS: dict[str, LoginFlow] = {}
_flow_lock = threading.Lock()


def _record(flow: LoginFlow) -> dict:
    """Persist the authenticated profile: SQLite row + headless provider client."""
    from . import db, gemini_pool

    db.upsert_gemini_account(flow.slot, flow.label, flow.email or "", flow.provider, str(flow.user_data_dir))
    return gemini_pool.POOL.register_cdp_profile(flow.slot, flow.email or "", flow.provider, str(flow.user_data_dir))


def _close_window(flow: LoginFlow, proc) -> None:
    info = gemini_cdp.devtools_version(flow.port) or {}
    if info.get("webSocketDebuggerUrl"):
        try:
            s = CDPSession(info["webSocketDebuggerUrl"], timeout=4)
            s.send("Browser.close", timeout=5)
            s.close()
        except Exception:
            pass
    try:  # the headless worker cannot open the profile until this process is gone
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def probe_session(port: int) -> tuple[bool, dict | None]:
    """One sensor tick: is any Gemini chat tab on this DevTools port signed in?"""
    for target in gemini_cdp.list_pages(port):
        if not gemini_cdp.is_app_url(target.get("url", "")) or not target.get("webSocketDebuggerUrl"):
            continue
        try:
            s = CDPSession(target["webSocketDebuggerUrl"], timeout=4)
            try:
                info = s.evaluate(gemini_cdp.SESSION_JS, timeout=8)
                cookies = gemini_cdp.session_cookies(s)
            finally:
                s.close()
        except Exception:
            continue
        if gemini_cdp.signed_in(info, cookies):
            return True, info
    return False, None


def _sensor(flow: LoginFlow, proc) -> None:
    deadline = flow.started + FLOW_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        if flow.state != "pending":       # cancelled from the UI
            return
        if proc.poll() is not None and not gemini_cdp.devtools_version(flow.port, timeout=0.5):
            flow.state, flow.error = "closed", CLOSED_EARLY_MSG
            return
        ok, info = probe_session(flow.port)
        if not ok:
            continue
        account = (info or {}).get("account") or ""
        flow.email = account if "@" in account else None
        try:
            result = _record(flow)
        except Exception as exc:  # pragma: no cover - disk/db failure
            flow.state, flow.error = "failed", f"Signed in, but the account could not be saved: {exc}"
            return
        _close_window(flow, proc)
        if not result.get("ok"):
            flow.state, flow.error = "failed", result.get("reason") or "Account could not be registered."
            return
        flow.state = "authenticated"
        return
    flow.state, flow.error = "closed", "Sign-in timed out. Please try again."
    try:
        _close_window(flow, proc)
    except Exception:
        pass


def spawn_login_flow(provider: str = "chrome", slot: int | None = None) -> dict:
    """Open the isolated one-time sign-in window and start the CDP sensor."""
    provider = provider if provider in PROVIDER_LABELS else "chrome"
    exe = gemini_cdp.resolve_exe(provider)

    from . import gemini_pool

    if slot is None:
        slot = gemini_pool.POOL.next_free_slot()
    gemini_pool.POOL.release_profile(slot)  # re-login: stop that profile's headless Chrome first

    udir = gemini_cdp.profile_dir(slot)
    udir.mkdir(parents=True, exist_ok=True)
    port = gemini_cdp.LOGIN_BASE_PORT + slot
    while not gemini_cdp.port_free(port):
        port += 1

    flow = LoginFlow(provider, slot, port, udir)
    proc = gemini_cdp.popen(gemini_cdp.chrome_args(exe, udir, port, headless=False), visible=True)
    flow.pid = proc.pid
    with _flow_lock:
        FLOWS[flow.id] = flow
    threading.Thread(target=_sensor, args=(flow, proc), daemon=True).start()
    return flow.public()


def flow_status(flow_id: str) -> dict | None:
    with _flow_lock:
        f = FLOWS.get(flow_id)
    return f.public() if f else None


def cancel_flow(flow_id: str) -> dict | None:
    """User dismissed the modal — stop the sensor, leave the window to the user."""
    with _flow_lock:
        f = FLOWS.get(flow_id)
    if not f:
        return None
    if f.state == "pending":
        f.state, f.error = "closed", CLOSED_EARLY_MSG
    return f.public()
