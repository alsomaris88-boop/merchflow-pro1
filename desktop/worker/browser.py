"""
MerchFlow Pro — one-time interactive login spawner (Donepixel UX).

IMPORTANT ARCHITECTURE
  The browser window is used ONLY to let a human sign in once. The moment the
  sensor sees an authenticated gemini.google.com/app page it harvests the
  session cookies + account email, PERSISTS them, and closes the window.

  Every subsequent generation is 100% headless HTTP (gemini_client.py /
  batchexecute). No browser window is ever opened during generation.

Flow
  spawn_login_flow("brave")
    └─ isolated window → https://accounts.google.com
    └─ sensor polls every 1.5s
         ├─ user finished → extract email + __Secure-1PSID(/TS) → verify
         │   SNlM0e → persist to pool → Browser.close() → state=authenticated
         └─ user closed window early → state=closed
              "The sign-in window was closed before sign-in finished."
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit
from pathlib import Path

import httpx

IS_WIN = sys.platform.startswith("win")
APPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".merchflow"))
PROFILE_ROOT = APPDATA / "MerchFlow" / "Profiles"
APP_URL = "https://gemini.google.com/app"
# Gemini redirects to Google sign-in with the correct return URL.
LOGIN_URL = APP_URL
POLL_SECONDS = 1.5
FLOW_TIMEOUT = 15 * 60  # generous window for 2FA / password recovery

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/57.36"
)

PROVIDER_LABELS = {
    "brave": "Brave",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
}

EXE_CANDIDATES = {
    "brave": [
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        "brave", "brave.exe",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "chrome", "chrome.exe", "google-chrome", "chromium",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "msedge", "msedge.exe",
    ],
}

CLOSED_EARLY_MSG = "The sign-in window was closed before sign-in finished."

EMAIL_JS = """(() => {
  const rx = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g;
  const hits = [];
  for (const i of document.querySelectorAll('img[alt]')) {
    const m = (i.getAttribute('alt') || '').match(rx);
    if (m) hits.push(m[0]);
  }
  for (const a of document.querySelectorAll('a[aria-label],button[aria-label]')) {
    const m = (a.getAttribute('aria-label') || '').match(rx);
    if (m) hits.push(m[0]);
  }
  if (!hits.length) {
    const m = document.documentElement.innerHTML.match(rx);
    if (m) hits.push(...m.slice(0, 3));
  }
  return hits[0] || null;
})()"""

HTML_JS = "document.documentElement.outerHTML"


def resolve_exe(provider: str) -> str:
    for cand in EXE_CANDIDATES.get(provider, EXE_CANDIDATES["chrome"]):
        if os.path.isabs(cand) and Path(cand).exists():
            return cand
        found = None
        try:
            from shutil import which

            found = which(cand)
        except Exception:
            found = None
        if found:
            return found
    raise FileNotFoundError(f"Could not find a {PROVIDER_LABELS.get(provider, provider)} installation on this computer.")


def _targets(port: int) -> list[dict]:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json", timeout=2.0,
                      headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return []
        return [t for t in r.json() if t.get("type") == "page"]
    except Exception:
        return []


def _cdp_eval(ws_url: str, expression: str, timeout: float = 20.0):
    from websockets.sync.client import connect

    with connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=6) as ws:
        payload = json.dumps(
            {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "awaitPromise": False, "returnByValue": True},
            }
        )
        ws.send(payload)
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv(timeout=max(0.2, deadline - time.time())))
            if msg.get("id") == 1:
                return msg.get("result", {}).get("result", {}).get("value")
        raise TimeoutError("CDP evaluate timed out")


def _cdp_cookies(ws_url: str) -> list[dict]:
    """Network.getCookies scoped to gemini.google.com."""
    from websockets.sync.client import connect

    with connect(ws_url, max_size=16 * 1024 * 1024, open_timeout=6) as ws:
        ws.send(json.dumps(
            {"id": 1, "method": "Network.enable", "params": {}}
        ))
        ws.send(json.dumps(
            {"id": 2, "method": "Network.getCookies",
             "params": {"urls": ["https://gemini.google.com", "https://accounts.google.com"]}}
        ))
        deadline = time.time() + 15
        while time.time() < deadline:
            msg = json.loads(ws.recv(timeout=max(0.2, deadline - time.time())))
            if msg.get("id") == 2:
                if "error" in msg:
                    return []
                return msg.get("result", {}).get("cookies", []) or []
        return []


def _cdp_close(ws_url: str) -> None:
    """Best-effort graceful close of the whole login window."""
    from websockets.sync.client import connect

    try:
        with connect(ws_url, max_size=1024 * 1024, open_timeout=4) as ws:
            ws.send(json.dumps({"id": 1, "method": "Browser.close", "params": {}}))
            try:
                ws.recv(timeout=3)
            except Exception:
                pass
    except Exception:
        pass


# --------------------------------------------------------------------- flows
class LoginFlow:
    def __init__(self, provider: str, slot: int) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.provider = provider
        self.slot = slot
        self.port = 9300 + (slot % 200)
        self.pid: int | None = None
        self.state = "pending"          # pending | authenticated | closed | failed
        self.email: str | None = None
        self.error: str | None = None
        self.label = "New Gemini account"
        self.lock = threading.RLock()
        self.authentication_observed = False
        self.started = time.time()
        self.exe: str | None = None
        self.user_data_dir: str | None = None

    def public(self) -> dict:
        return {
            "ok": self.state == "authenticated",
            "flowId": self.id,
            "state": self.state,
            "email": self.email,
            "label": self.label,
            "provider": self.provider,
            "port": self.port,
            "slot": self.slot,
            "error": self.error,
            "elapsed": round(time.time() - self.started, 1),
        }


FLOWS: dict[str, LoginFlow] = {}
_flow_lock = threading.Lock()


def _persist(flow: LoginFlow, psid: str, psidts: str, email: str | None) -> dict | None:
    """Hand the harvested session to the headless pool."""
    try:
        from . import gemini_pool

        return gemini_pool.POOL.register_session(flow, psid, psidts, email)
    except Exception as e:  # pragma: no cover
        flow.state = "failed"
        flow.error = f"could not persist session: {e}"
        return None


def _sensor(flow: LoginFlow, proc: subprocess.Popen) -> None:
    deadline = flow.started + FLOW_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        with flow.lock:
            if flow.state != "pending":
                return  # cancellation must never create a delayed account

        # ---- user closed the window before finishing sign-in
        if proc.poll() is not None:
            flow.state = "closed"
            flow.error = CLOSED_EARLY_MSG
            return

        # ---- look for an authenticated gemini app tab
        target = None
        for t in _targets(flow.port):
            url = t.get("url") or ""
            if urlsplit(url).hostname == "gemini.google.com" and "/app" in urlsplit(url).path:
                target = t
                break
        if target is None:
            continue

        try:
            html = _cdp_eval(target["webSocketDebuggerUrl"], HTML_JS) or ""
        except Exception:
            continue

        # SNlM0e only exists on a signed-in, fully loaded app page
        if not re.search(r'"SNlM0e":"(.+?)"', html):
            continue

        # ---- harvest email
        try:
            flow.email = _cdp_eval(target["webSocketDebuggerUrl"], EMAIL_JS)
        except Exception:
            flow.email = None

        # ---- harvest session cookies
        psid = psidts = ""
        for c in _cdp_cookies(target["webSocketDebuggerUrl"]):
            name = c.get("name", "")
            if name == "__Secure-1PSID":
                psid = c.get("value", "")
            elif name == "__Secure-1PSIDTS":
                psidts = c.get("value", "")
        if not psid:
            flow.state = "failed"
            flow.error = "Signed in, but the session cookie could not be read. Please try again."
            return

        # ---- persist + verify headlessly, then close the window
        with flow.lock:
            if flow.state != "pending":
                return
            flow.authentication_observed = True
        result = _persist(flow, psid, psidts, flow.email)
        if result is None:
            return
        if not result.get("ok"):
            with flow.lock:
                if flow.state != "pending":
                    return
                flow.state = "failed"
                flow.error = result.get("reason") or "Session could not be verified."
            _cdp_close(target["webSocketDebuggerUrl"])
            return

        if result.get("email") and not flow.email:
            flow.email = result["email"]
        flow.state = "authenticated"
        _cdp_close(target["webSocketDebuggerUrl"])
        return

    flow.state = "closed"
    flow.error = "Sign-in timed out. Please try again."


def spawn_login_flow(provider: str = "chrome", slot: int | None = None) -> dict:
    """Open the isolated one-time login window and start the capture sensor."""
    provider = provider if provider in PROVIDER_LABELS else "chrome"
    exe = resolve_exe(provider)

    from . import gemini_pool

    # Reserve pending slots in memory, never by inserting a phantom DB row.
    # Do not reuse a cancelled flow's browser profile/port in this process.
    with _flow_lock:
        if slot is None:
            slot = max(gemini_pool.POOL.next_free_slot(), max((f.slot for f in FLOWS.values()), default=0) + 1)
        elif any(f.slot == slot and f.state == "pending" for f in FLOWS.values()):
            raise RuntimeError("This account already has a pending sign-in window")
        flow = LoginFlow(provider, slot)
        if any(f.port == flow.port and f.state == "pending" for f in FLOWS.values()):
            raise RuntimeError("Another sign-in is using this browser port; finish it first")
        udir = PROFILE_ROOT / f"Login_{flow.id}"
        udir.mkdir(parents=True, exist_ok=True)
        args = [
            exe, f"--user-data-dir={udir}", f"--remote-debugging-port={flow.port}",
            "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
            "--disable-session-crashed-bubble", "--disable-restore-session-state",
            "--new-window", LOGIN_URL,
        ]
        kwargs: dict = {}
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        flow.pid, flow.exe, flow.user_data_dir = proc.pid, exe, str(udir)
        FLOWS[flow.id] = flow

    threading.Thread(target=_sensor, args=(flow, proc), daemon=True).start()
    return {**flow.public(), "ok": True}


def flow_status(flow_id: str) -> dict | None:
    with _flow_lock:
        f = FLOWS.get(flow_id)
    return f.public() if f else None


def cancel_flow(flow_id: str) -> dict | None:
    """User dismissed the modal — stop the sensor, keep the window usable."""
    with _flow_lock:
        f = FLOWS.get(flow_id)
    if not f:
        return None
    with f.lock:
        if f.state == "pending":
            f.state = "closed"
            f.error = CLOSED_EARLY_MSG
    return f.public()
