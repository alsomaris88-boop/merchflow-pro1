"""Gemini web image generation over the raw Chrome DevTools Protocol (CDP).

Architecture (modelled on clovericbot/cc-image and spurbro/gemini-web-image-gen):

  LOGIN (once per account, visible window)
    chrome --user-data-dir=desktop/profiles/account_<id> --app=https://gemini.google.com/app
    The human signs in (password, 2FA, passkey). A sensor polls the page over
    CDP; once the URL is gemini.google.com/app AND the session is live, the
    account is recorded in SQLite and the window is closed (browser.py).

  GENERATION (every job, invisible)
    chrome --headless=new --user-data-dir=<same profile> --remote-debugging-port=9222+
    The persistent profile already holds the Google session, so credentials
    are never requested again. Each job opens a fresh chat tab and drives the
    normal Gemini chat input — there is no separate "image tab":
      1. dispatch the reference file(s) into <input type="file" accept="image/*">
      2. GATE 1 — wait until the thumbnail card renders in the input container
      3. inject the exact prompt into the rich-textarea (Input.insertText)
      4. submit via button[aria-label="Send message"] (Enter as fallback)
         GATE 2 — the user bubble must show the attached image
      5. wait for the model response, then extract the FULL-SIZE image via the
         "Download full size image" button (browser-level setDownloadBehavior),
         falling back to an in-page full-resolution fetch, then canvas.toDataURL
      6. return EXACTLY ONE image — never a set of variants

  Port model: account N listens on 9222 + (N - 1). Every account is its own
  Chrome process (a profile directory can only be opened by one process), so
  parallel Pixel-to-Pixel leases need one DevTools port per account.

All Gemini DOM knowledge lives in SELECTORS so UI drift is a one-place fix.
"""
from __future__ import annotations

import atexit
import base64
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from .gemini_client import GeminiAuthError, GeminiRenderError, ModelDegradedError

IS_WIN = sys.platform.startswith("win")
DESKTOP_ROOT = Path(__file__).resolve().parent.parent            # desktop/
PROFILES_ROOT = Path(os.environ.get("MERCHFLOW_PROFILES") or (DESKTOP_ROOT / "profiles"))
APP_URL = os.environ.get("MERCHFLOW_GEMINI_URL", "https://gemini.google.com/app")
CDP_BASE_PORT = int(os.environ.get("MERCHFLOW_CDP_PORT", "9222"))
LOGIN_BASE_PORT = int(os.environ.get("MERCHFLOW_LOGIN_PORT", "9500"))
GENERATION_TIMEOUT = float(os.environ.get("MERCHFLOW_CDP_GEN_TIMEOUT", "300"))
READY_TIMEOUT = float(os.environ.get("MERCHFLOW_CDP_READY_TIMEOUT", "60"))
MIN_IMAGE_SIDE = 256
SESSION_COOKIES = {"__Secure-1PSID", "SID"}

SELECTORS: dict = {
    "editor": 'rich-textarea [contenteditable="true"], rich-textarea .ql-editor, div.ql-editor, '
              'div[contenteditable="true"][role="textbox"], [contenteditable="true"], textarea',
    "inputContainer": "input-area-v2, input-area, .input-area, .chat-input-container, input-container",
    "fileInput": 'input[type="file"][accept*="image"], input[type="file"]',
    "uploadButtonAria": ["open upload file menu", "upload", "add file", "attach", "tools", "上传"],
    "uploadMenuText": ["upload files", "upload file", "upload image", "add image", "上传文件", "上传图片"],
    "attachmentChips": [
        "uploader-file-preview", ".file-preview-card", ".attachment-preview", ".uploaded-image",
        ".gem-attachment-style-img", '[data-test-id*="attachment"]', '[data-test-id*="file-preview"]',
        'img[src^="blob:"]', 'img[src^="data:image"]',
    ],
    "uploadBusy": 'mat-progress-spinner, mat-progress-bar, [role="progressbar"], .upload-progress, [aria-busy="true"]',
    "sendAria": ["send message", "send", "submit", "发送"],
    "stopAria": ["stop response", "stop generating", "stop", "停止"],
    "userQuery": "user-query, .user-query-container, [data-test-id=\"user-query\"]",
    "response": "model-response, .model-response, [data-test-id=\"model-response\"]",
    "generatedImages": ["generated-image img", "single-image img", ".generated-image img",
                        ".image-container img", "img.image", "model-response img"],
    "downloadAria": "download full size",
    "dismissText": ["got it", "acknowledge", "no thanks", "dismiss", "close"],
    "minSide": MIN_IMAGE_SIDE,
}
QUOTA_HINTS = ("reached your limit", "image generation limit", "limit for generating",
               "try again tomorrow", "you've reached", "you have reached", "usage limit")
REFUSAL_HINTS = ("i can't generate", "i cannot generate", "unable to generate", "can't create",
                 "content policy", "safety policy", "try a different prompt", "that violates")

EXE_CANDIDATES = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome", "google-chrome-stable", "chrome", "chrome.exe", "chromium", "chromium-browser",
    ],
    "brave": [
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        "brave", "brave-browser", "brave.exe",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "msedge", "msedge.exe", "microsoft-edge",
    ],
}
PROVIDER_LABELS = {"chrome": "Google Chrome", "brave": "Brave", "edge": "Microsoft Edge"}


class CDPError(RuntimeError):
    pass


# --------------------------------------------------------------- paths/ports
def profile_dir(account_id: int) -> Path:
    return PROFILES_ROOT / f"account_{int(account_id)}"


def cdp_port(account_id: int) -> int:
    """Account 1 → 9222, account 2 → 9223, … (one Chrome process per profile)."""
    return CDP_BASE_PORT + max(0, int(account_id) - 1)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def is_app_url(url: str) -> bool:
    """True for the Gemini chat app (also /u/N/app), never Lens/accounts pages."""
    want, got = urlparse(APP_URL), urlparse(url or "")
    return got.scheme in {"http", "https"} and got.netloc == want.netloc and bool(
        re.match(r"^/(u/\d+/)?app(/.*)?$", got.path or ""))


def resolve_exe(provider: str = "chrome") -> str:
    override = os.environ.get("MERCHFLOW_CHROME")
    if override:
        if Path(override).exists() or shutil.which(override):
            return override
        raise FileNotFoundError(f"MERCHFLOW_CHROME points to a missing browser: {override}")
    for cand in EXE_CANDIDATES.get(provider, EXE_CANDIDATES["chrome"]):
        if os.path.isabs(cand) and Path(cand).exists():
            return cand
        found = shutil.which(cand)
        if found:
            return found
    raise FileNotFoundError(f"Could not find a {PROVIDER_LABELS.get(provider, provider)} installation on this computer.")


def chrome_args(exe: str, user_data_dir: Path | str, port: int, *, headless: bool, url: str | None = None) -> list[str]:
    """Exact launch contract for both the visible login and the headless worker."""
    url = url or APP_URL
    args = [
        exe,
        f"--user-data-dir={Path(user_data_dir)}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-features=Translate,MediaRouter",
    ]
    if headless:
        args += ["--headless=new", "--window-size=1440,1000", "--mute-audio", "about:blank"]
    else:
        args += ["--window-size=560,820", f"--app={url}"]
    extra = os.environ.get("MERCHFLOW_CHROME_EXTRA_ARGS", "").strip()
    if extra:  # power-user knob (proxies, test sandboxes); appended, never replaces the contract
        args[1:1] = shlex.split(extra, posix=not IS_WIN)
    return args


def popen(args: list[str], *, visible: bool) -> subprocess.Popen:
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if IS_WIN:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        if not visible:
            flags |= subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


# ---------------------------------------------------------------- DevTools HTTP
def devtools_version(port: int, timeout: float = 2.0) -> dict | None:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def wait_devtools(port: int, timeout: float, proc: subprocess.Popen | None = None) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = devtools_version(port)
        if info and info.get("webSocketDebuggerUrl"):
            return info
        if proc is not None and proc.poll() is not None:
            raise CDPError("Chrome exited during start-up. Is this profile already open in another Chrome window?")
        time.sleep(0.25)
    raise CDPError(f"Chrome DevTools did not answer on port {port} within {int(timeout)}s")


def list_pages(port: int) -> list[dict]:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=2.0)
        return [t for t in r.json() if t.get("type") == "page"] if r.status_code == 200 else []
    except Exception:
        return []


def new_page(port: int, url: str = "about:blank") -> dict:
    r = httpx.put(f"http://127.0.0.1:{port}/json/new?{quote(url, safe=':/?&=')}", timeout=10)
    r.raise_for_status()
    return r.json()


def close_page(port: int, target_id: str) -> None:
    try:
        httpx.get(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=3)
    except Exception:
        pass


# ------------------------------------------------------------------- session
class CDPSession:
    """Minimal synchronous CDP client over one DevTools WebSocket."""

    def __init__(self, ws_url: str, timeout: float = 10) -> None:
        from websockets.sync.client import connect

        self._cm = connect(ws_url, max_size=256 * 1024 * 1024, open_timeout=timeout)
        self.ws = self._cm.__enter__()
        self._id = 0
        self.events: list[dict] = []

    def _stash(self, msg: dict) -> None:
        if "method" in msg:
            self.events.append(msg)
            del self.events[:-400]

    def send(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError(f"CDP {method} timed out")
            msg = json.loads(self.ws.recv(timeout=remaining))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})
            self._stash(msg)

    def wait_event(self, method: str, timeout: float) -> dict | None:
        for i, ev in enumerate(self.events):
            if ev.get("method") == method:
                return self.events.pop(i).get("params", {})
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                msg = json.loads(self.ws.recv(timeout=remaining))
            except TimeoutError:
                return None
            if msg.get("method") == method:
                return msg.get("params", {})
            self._stash(msg)
        return None

    def evaluate(self, expression: str, timeout: float = 30, await_promise: bool = True):
        # userGesture: Chrome only opens file choosers / downloads for user-activated clicks.
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                "awaitPromise": await_promise, "userGesture": True}, timeout=timeout + 5)
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text") or "script error"
            raise CDPError(f"page script failed: {text[:300]}")
        return result.get("result", {}).get("value")

    def object_id(self, expression: str) -> str | None:
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": False})
        return result.get("result", {}).get("objectId")

    def close(self) -> None:
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass


# ------------------------------------------------------------- page scripts
_SEL = json.dumps(SELECTORS)
_HELPERS = """
const S = %s;
const lower = (v) => (v || '').toString().toLowerCase();
const ariaMatch = (el, words) => { const a = lower(el.getAttribute('aria-label')); return words.some((w) => a.includes(w)); };
const enabled = (b) => b && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
const box = () => { const ed = document.querySelector(S.editor);
  return document.querySelector(S.inputContainer) || (ed && (ed.closest('form,fieldset') || ed.parentElement?.parentElement?.parentElement)) || document; };
const lastOf = (sel) => { const all = document.querySelectorAll(sel); return all[all.length - 1] || null; };
const outermost = (els) => els.filter((el) => !els.some((other) => other !== el && other.contains(el)));
const goodImg = (img) => img.complete && img.naturalWidth >= S.minSide && img.naturalHeight >= S.minSide;
""" % _SEL


def _js(body: str) -> str:
    return "(() => {" + _HELPERS + body + "})()"


SESSION_JS = _js("""
  const signIn = [...document.querySelectorAll('a,button')].some((el) => {
    const label = lower((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).trim();
    return /^sign in\\b/.test(label) || /accounts\\.google\\.com\\/(servicelogin|signin)/i.test(el.getAttribute('href') || '');
  });
  const rx = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/;
  let account = null;
  for (const el of document.querySelectorAll('a[aria-label],button[aria-label],img[alt]')) {
    const v = el.getAttribute('aria-label') || el.getAttribute('alt') || '';
    if (/google account/i.test(v)) { const m = v.match(rx); if (m) { account = m[0]; break; } account = account || 'signed-in'; }
  }
  return { href: location.href, ready: document.readyState, editor: !!document.querySelector(S.editor), signIn, account };
""")

DISMISS_JS = _js("""
  let n = 0;
  for (const b of document.querySelectorAll('button')) {
    const t = lower(b.innerText || b.getAttribute('aria-label'));
    if (S.dismissText.some((w) => t === w) && b.offsetParent !== null) { b.click(); n++; }
  }
  return n;
""")

STATE_JS = _js("""
  const responses = document.querySelectorAll(S.response);
  const last = responses[responses.length - 1] || null;
  const imgs = last ? outermost([...last.querySelectorAll(S.generatedImages.join(','))]).filter(goodImg) : [];
  const downloads = last ? [...last.querySelectorAll('button')].filter((b) => lower(b.getAttribute('aria-label')).includes(S.downloadAria)).length : 0;
  const stop = [...document.querySelectorAll('button')].some((b) => ariaMatch(b, S.stopAria) && b.offsetParent !== null);
  const lastQuery = lastOf(S.userQuery);
  return { responses: responses.length, userQueries: document.querySelectorAll(S.userQuery).length,
           queryImages: lastQuery ? lastQuery.querySelectorAll('img').length : 0,
           images: imgs.length, downloads, stop, text: last ? (last.innerText || '').slice(0, 800) : '',
           editorText: (document.querySelector(S.editor)?.innerText || document.querySelector(S.editor)?.value || '') };
""")

GATE1_JS = _js("""
  const container = box();
  const chips = outermost([...container.querySelectorAll(S.attachmentChips.join(','))])
    .filter((el) => el.tagName !== 'IMG' || (el.complete && el.naturalWidth > 0));
  return { chips: chips.length, busy: !!container.querySelector(S.uploadBusy) };
""")

FOCUS_EDITOR_JS = _js("""
  const ed = document.querySelector(S.editor);
  if (!ed) return false;
  ed.focus();
  if (ed.isContentEditable) { const r = document.createRange(); r.selectNodeContents(ed); const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.execCommand('delete'); }
  else { ed.value = ''; }
  return true;
""")

CLICK_UPLOAD_JS = _js("""
  const b = [...document.querySelectorAll('button,[role="button"]')].find((el) => ariaMatch(el, S.uploadButtonAria) && enabled(el));
  if (!b) return false; b.click(); return true;
""")

CLICK_UPLOAD_MENU_JS = _js("""
  const items = [...document.querySelectorAll('[role="menuitem"], .mat-mdc-menu-item, button, [data-test-id*="uploader"]')];
  const it = items.find((el) => S.uploadMenuText.some((w) => lower(el.innerText || el.getAttribute('aria-label')).includes(w)));
  if (!it) return false; it.click(); return true;
""")

EDITOR_RECT_JS = _js("""
  const ed = document.querySelector(S.editor); if (!ed) return null;
  const r = ed.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
""")

SEND_JS = _js("""
  const b = [...document.querySelectorAll('button')].find((el) => ariaMatch(el, S.sendAria) && !ariaMatch(el, S.stopAria));
  if (!enabled(b)) return false; b.click(); return true;
""")

SCROLL_DOWNLOAD_JS = _js("""
  const last = lastOf(S.response); if (!last) return false;
  const b = [...last.querySelectorAll('button')].find((el) => lower(el.getAttribute('aria-label')).includes(S.downloadAria));
  if (!b) return false; b.scrollIntoView({ block: 'center' }); return true;
""")

CLICK_DOWNLOAD_JS = _js("""
  const last = lastOf(S.response); if (!last) return false;
  const b = [...last.querySelectorAll('button')].find((el) => lower(el.getAttribute('aria-label')).includes(S.downloadAria));
  if (!b) return false; b.click(); return true;
""")

FETCH_FULL_JS = "(async () => {" + _HELPERS + """
  const last = lastOf(S.response); if (!last) return null;
  const img = outermost([...last.querySelectorAll(S.generatedImages.join(','))]).filter(goodImg)[0];
  if (!img) return null;
  const src = img.currentSrc || img.src;
  const full = src.replace(/=[swh]\\d+[^/=]*$/, '=s0');
  for (const url of [...new Set([full, src])]) {
    try {
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) continue;
      const blob = await res.blob();
      if (!blob.type.startsWith('image/') || !blob.size) continue;
      const data = await new Promise((ok, bad) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = bad; fr.readAsDataURL(blob); });
      return { data, url };
    } catch (e) { /* try the next candidate */ }
  }
  return null;
})()"""

CANVAS_JS = _js("""
  const last = lastOf(S.response); if (!last) return null;
  const img = outermost([...last.querySelectorAll(S.generatedImages.join(','))]).filter(goodImg)[0];
  if (!img) return null;
  const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
  c.getContext('2d').drawImage(img, 0, 0);
  try { return { data: c.toDataURL('image/png') }; } catch (e) { return null; }
""")


# --------------------------------------------------------------- helpers
def signed_in(info: dict | None, cookies: list[dict] | None = None) -> bool:
    """Signed-in Gemini = chat URL + editor + no Sign-in CTA + account/session evidence."""
    if not info or not is_app_url(info.get("href", "")) or not info.get("editor") or info.get("signIn"):
        return False
    has_cookie = any(c.get("name") in SESSION_COOKIES and c.get("value") for c in (cookies or []))
    return bool(info.get("account")) or has_cookie


def session_cookies(session: CDPSession) -> list[dict]:
    try:
        session.send("Network.enable")
        return session.send("Network.getCookies", {"urls": [APP_URL]}).get("cookies", []) or []
    except Exception:
        return []


def validate_image(data: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as probe:
        probe.verify()
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    if min(width, height) < MIN_IMAGE_SIDE:
        raise GeminiRenderError(f"Extracted image is only {width}×{height}; refusing a thumbnail")
    return width, height


def _decode_data_url(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(",", 1)[1])


def _hint(text: str, hints: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(h in low for h in hints)


# ----------------------------------------------------------- headless client
_LIVE: "set[CdpGeminiClient]" = set()


class CdpGeminiClient:
    """Provider client: same ``generate_image(prompt, references) -> bytes``
    contract as the legacy HTTP client, driven through a headless profile."""

    transport = "cdp-headless"

    def __init__(self, account_id: int, provider: str = "chrome", label: str = "", user_data_dir: str | Path | None = None) -> None:
        self.account_id = int(account_id)
        self.provider = provider
        self.label = label or f"Profile {self.account_id:02d}"
        self.user_data_dir = Path(user_data_dir) if user_data_dir else profile_dir(self.account_id)
        self.port = cdp_port(self.account_id)
        self.email: str = ""
        self.last_extraction: dict = {}
        self._proc: subprocess.Popen | None = None
        self._attached = False          # True once we own a live DevTools endpoint
        self._ua: str | None = None
        self._lock = threading.RLock()
        _LIVE.add(self)

    # ---------------------------------------------------------- browser
    def _owned_port(self) -> int | None:
        """Re-attach to this profile's own headless Chrome (e.g. after an engine restart)."""
        marker = self.user_data_dir / "DevToolsActivePort"
        try:
            port = int(marker.read_text(encoding="utf-8").splitlines()[0])
        except Exception:
            return None
        return port if devtools_version(port) else None

    def _ensure_browser(self) -> int:
        if self._proc is not None and self._proc.poll() is None and devtools_version(self.port):
            return self.port
        if not self.user_data_dir.exists():
            raise GeminiAuthError(f"{self.label}: profile folder is missing; add the account again")
        owned = self._owned_port()
        if owned:
            self.port = owned
            self._attached = True
        else:
            if not port_free(self.port):  # someone else's DevTools on 9222+N → let Chrome pick
                self.port = 0
            args = chrome_args(resolve_exe(self.provider), self.user_data_dir, self.port, headless=True)
            self._proc = popen(args, visible=False)
            if self.port == 0:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not (owned := self._owned_port()):
                    if self._proc.poll() is not None:
                        break
                    time.sleep(0.25)
                if not owned:
                    raise GeminiRenderError(f"{self.label}: headless Chrome did not publish a DevTools port")
                self.port = owned
        info = wait_devtools(self.port, 25, self._proc)
        self._attached = True
        self._ua = (info.get("User-Agent") or "").replace("HeadlessChrome", "Chrome") or None
        return self.port

    def close(self) -> None:
        with self._lock:
            info = devtools_version(self.port) if (self._proc is not None or self._attached) else None
            if info and info.get("webSocketDebuggerUrl"):
                try:
                    s = CDPSession(info["webSocketDebuggerUrl"], timeout=3)
                    s.send("Browser.close", timeout=5)
                    s.close()
                except Exception:
                    pass
            if self._proc is not None:
                try:
                    self._proc.wait(timeout=8)
                except Exception:
                    self._proc.kill()
            self._proc = None
            self._attached = False

    # ------------------------------------------------------------- page
    def _open_tab(self, port: int) -> tuple[dict, CDPSession]:
        target = new_page(port)
        session = CDPSession(target["webSocketDebuggerUrl"])
        session.send("Page.enable")
        session.send("DOM.enable")
        if self._ua:
            session.send("Emulation.setUserAgentOverride", {"userAgent": self._ua})
        session.send("Page.navigate", {"url": APP_URL})
        return target, session

    def _wait_ready(self, s: CDPSession) -> dict:
        deadline = time.monotonic() + READY_TIMEOUT
        info: dict = {}
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                info = s.evaluate(SESSION_JS, timeout=10) or {}
            except CDPError:
                continue  # navigation in flight
            host = urlparse(info.get("href", "")).netloc
            if host.startswith("accounts.google.") or (info.get("ready") == "complete" and info.get("signIn")):
                raise GeminiAuthError(f"{self.label}: Google session expired — re-add this account in Account Pool")
            if signed_in(info, session_cookies(s)):
                if info.get("account") and "@" in info["account"]:
                    self.email = info["account"]
                s.evaluate(DISMISS_JS)
                return info
        raise GeminiRenderError(f"{self.label}: Gemini chat did not become ready within {int(READY_TIMEOUT)}s")

    def init(self) -> None:
        """Verify the persistent session headlessly (no credentials involved)."""
        with self._lock:
            port = self._ensure_browser()
            target, s = self._open_tab(port)
            try:
                self._wait_ready(s)
            finally:
                s.close()
                close_page(port, target["id"])

    # ------------------------------------------------ step 1 + 2: attach
    def _gate1(self, s: CDPSession, expected: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = s.evaluate(GATE1_JS) or {}
            if state.get("chips", 0) >= expected and not state.get("busy"):
                return True
            time.sleep(0.2)
        return False

    def _attach(self, s: CDPSession, paths: list[str]) -> str:
        expected = len(paths)
        # Strategy A — the page's own <input type="file" accept="image/*">
        obj = s.object_id(f"document.querySelector({json.dumps(SELECTORS['fileInput'])})")
        if obj:
            s.send("DOM.setFileInputFiles", {"files": paths, "objectId": obj})
            if self._gate1(s, expected, 15):
                return "file-input"
        # Strategy B — the attachment button → native file chooser, intercepted via CDP
        try:
            s.send("Page.setInterceptFileChooserDialog", {"enabled": True})
            if s.evaluate(CLICK_UPLOAD_JS):
                chooser = s.wait_event("Page.fileChooserOpened", 2.5)
                if chooser is None and s.evaluate(CLICK_UPLOAD_MENU_JS):
                    chooser = s.wait_event("Page.fileChooserOpened", 6)
                if chooser and chooser.get("backendNodeId"):
                    s.send("DOM.setFileInputFiles", {"files": paths, "backendNodeId": chooser["backendNodeId"]})
                    if self._gate1(s, expected, 15):
                        return "file-chooser"
                obj = s.object_id(f"document.querySelector({json.dumps(SELECTORS['fileInput'])})")
                if obj:
                    s.send("DOM.setFileInputFiles", {"files": paths, "objectId": obj})
                    if self._gate1(s, expected, 15):
                        return "file-input-after-menu"
        finally:
            try:
                s.send("Page.setInterceptFileChooserDialog", {"enabled": False})
            except CDPError:
                pass
        # Strategy C — native drag-and-drop onto the rich-textarea
        rect = s.evaluate(EDITOR_RECT_JS)
        if rect:
            data = {"items": [], "files": paths, "dragOperationsMask": 1}
            for kind in ("dragEnter", "dragOver", "drop"):
                s.send("Input.dispatchDragEvent", {"type": kind, "x": rect["x"], "y": rect["y"], "data": data})
            if self._gate1(s, expected, 15):
                return "drag-drop"
        raise GeminiRenderError("GATE 1 FAILED: the reference thumbnail never rendered in the Gemini input box")

    # ---------------------------------------------- step 3 + 4: prompt
    def _type_prompt(self, s: CDPSession, prompt: str) -> None:
        if not s.evaluate(FOCUS_EDITOR_JS):
            raise GeminiRenderError("Gemini prompt box (rich-textarea) not found")
        s.send("Input.insertText", {"text": prompt})
        typed = re.sub(r"\s+", " ", (s.evaluate(STATE_JS) or {}).get("editorText", ""))
        if re.sub(r"\s+", " ", prompt)[:48].strip() not in typed:
            s.evaluate(FOCUS_EDITOR_JS)
            s.evaluate(f"document.execCommand('insertText', false, {json.dumps(prompt)})")
            typed = re.sub(r"\s+", " ", (s.evaluate(STATE_JS) or {}).get("editorText", ""))
            if re.sub(r"\s+", " ", prompt)[:48].strip() not in typed:
                raise GeminiRenderError("Prompt injection into the Gemini rich-textarea failed")

    def _submit(self, s: CDPSession, before: dict, references: int) -> None:
        deadline = time.monotonic() + 30
        sent = False
        while time.monotonic() < deadline and not sent:  # Send stays disabled while uploads finish
            sent = bool(s.evaluate(SEND_JS))
            if not sent:
                time.sleep(0.4)
        if not sent:
            for kind in ("keyDown", "keyUp"):
                s.send("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter",
                                                  "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
                                                  **({"text": "\r"} if kind == "keyDown" else {})})
        # GATE 2 — the prompt left the box and the user bubble carries the image
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            st = s.evaluate(STATE_JS) or {}
            if st.get("userQueries", 0) > before.get("userQueries", 0):
                if references == 0 or st.get("queryImages", 0) >= 1:
                    return
            time.sleep(0.4)
        raise GeminiRenderError("GATE 2 FAILED: Gemini did not accept the message with its reference image")

    # ------------------------------------------------- step 5: response
    def _await_image(self, s: CDPSession, before: dict) -> dict:
        start = time.monotonic()
        deadline = start + GENERATION_TIMEOUT
        image_at = idle_since = None
        last_text = ""
        while time.monotonic() < deadline:
            time.sleep(1.5)
            st = s.evaluate(STATE_JS) or {}
            if st.get("responses", 0) <= before.get("responses", 0):
                continue
            text = st.get("text", "")
            if _hint(text, QUOTA_HINTS):
                raise ModelDegradedError(f"{self.label}: Gemini image limit reached — {text[:200]}")
            if st.get("images", 0) or st.get("downloads", 0):
                image_at = image_at or time.monotonic()
                # thinking models keep "Stop" visible long after the image is ready
                if not st.get("stop") or time.monotonic() - image_at >= 15:
                    return st
                continue
            if not st.get("stop") and text and text == last_text:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= 6:
                    reason = "refused" if _hint(text, REFUSAL_HINTS) else "answered without an image"
                    raise GeminiRenderError(f"Gemini {reason}: {text[:240]}")
            else:
                idle_since = None
            last_text = text
        raise GeminiRenderError(f"Gemini produced no image within {int(GENERATION_TIMEOUT)}s")

    # ------------------------------------------------ step 6: one image
    def _download_full_size(self, s: CDPSession, port: int, folder: Path) -> bytes | None:
        folder.mkdir(parents=True, exist_ok=True)
        info = devtools_version(port) or {}
        browser = CDPSession(info["webSocketDebuggerUrl"]) if info.get("webSocketDebuggerUrl") else None
        try:
            for _attempt in range(3):
                if browser is not None:  # MUST be browser-level: the button navigates to lh3.googleusercontent.com
                    browser.send("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(folder), "eventsEnabled": True})
                try:
                    s.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(folder)})
                except CDPError:
                    pass
                time.sleep(0.5)
                if not s.evaluate(SCROLL_DOWNLOAD_JS):
                    return None
                time.sleep(0.5)  # two-step scroll → click avoids clicking mid-scroll
                before = {p.name for p in folder.iterdir()}
                s.evaluate(CLICK_DOWNLOAD_JS)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    time.sleep(0.4)
                    fresh = [p for p in folder.iterdir() if p.name not in before and not p.name.endswith(".crdownload")]
                    if fresh:
                        path = max(fresh, key=lambda p: p.stat().st_mtime)
                        size = -1
                        while size != path.stat().st_size:  # wait for the write to settle
                            size = path.stat().st_size
                            time.sleep(0.3)
                        return path.read_bytes()
        finally:
            if browser is not None:
                browser.close()
        return None

    def _extract_one(self, s: CDPSession, port: int, workdir: Path, found: dict) -> bytes:
        attempts: list[tuple[str, callable]] = [
            ("download-full-size", lambda: self._download_full_size(s, port, workdir / "downloads")),
            ("fetch-full-resolution", lambda: (lambda r: _decode_data_url(r["data"]) if r else None)(s.evaluate(FETCH_FULL_JS, timeout=60))),
            ("canvas-toDataURL", lambda: (lambda r: _decode_data_url(r["data"]) if r else None)(s.evaluate(CANVAS_JS))),
        ]
        errors = []
        for method, attempt in attempts:
            try:
                data = attempt()
                if not data:
                    continue
                width, height = validate_image(data)
                self.last_extraction = {"method": method, "width": width, "height": height,
                                        "bytes": len(data), "imagesInResponse": max(found.get("images", 0), found.get("downloads", 0))}
                return data  # EXACTLY ONE image — extras in the response are ignored on purpose
            except Exception as exc:
                errors.append(f"{method}: {exc}")
        raise GeminiRenderError("Generated image could not be extracted at full size (" + "; ".join(errors)[:400] + ")")

    # ---------------------------------------------------------- public
    def generate_image(self, prompt: str, references: list[tuple[bytes, str]]) -> bytes:
        with self._lock:
            workdir = Path(tempfile.mkdtemp(prefix=f"merchflow-cdp-{self.account_id}-"))
            try:
                paths = []
                for index, (data, name) in enumerate(references):
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name or f"reference_{index + 1}.png")
                    path = workdir / f"{index + 1:02d}_{safe}"
                    path.write_bytes(data)
                    paths.append(str(path))
                try:
                    port = self._ensure_browser()
                except CDPError as exc:
                    raise GeminiRenderError(f"{self.label}: {exc}") from exc
                target, s = self._open_tab(port)
                try:
                    self._wait_ready(s)
                    before = s.evaluate(STATE_JS) or {}
                    if paths:
                        self.last_extraction = {"upload": self._attach(s, paths)}
                    self._type_prompt(s, prompt)
                    self._submit(s, before, len(paths))
                    found = self._await_image(s, before)
                    return self._extract_one(s, port, workdir, found)
                except CDPError as exc:
                    raise GeminiRenderError(f"{self.label}: CDP automation failed — {exc}") from exc
                finally:
                    s.close()
                    close_page(port, target["id"])
            finally:
                shutil.rmtree(workdir, ignore_errors=True)


def shutdown_all() -> None:
    for client in list(_LIVE):
        try:
            client.close()
        except Exception:
            pass


atexit.register(shutdown_all)
