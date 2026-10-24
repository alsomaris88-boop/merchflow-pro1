"""
MerchFlow Pro — headless Gemini client (the Donepixel paradigm).

ZERO browser windows. ZERO CDP. Pure, silent HTTPS:

  * Session cookies  __Secure-1PSID / __Secure-1PSIDTS  (verified real browser login)
  * SNlM0e token     scraped from https://gemini.google.com/app HTML
  * batchexecute RP  POST /_/BardChatUi/data/batchexecute  (rpcids=maZAc)
  * Imagen 3 bytes   downloaded straight from the lh3 URLs in the RPC reply

Each account costs one httpx.Client (~ a few hundred KB). Ten pooled accounts
stay well under 40 MB RAM — versus 4 GB+ for ten external Chrome windows.

Protocol implementation follows the public reverse-engineering of
  - HanaokaYuzu/Gemini-API   (batchexecute envelope, SNlM0e, file upload)
  - imagegeneration (PyPI)   (headless multi-threaded cookie generation)
"""
from __future__ import annotations

import json
import random
import re
import time

import httpx

BASE_URL = "https://gemini.google.com"
BATCH_EXEC_URL = f"{BASE_URL}/_/BardChatUi/data/batchexecute"
UPLOAD_URL = "https://content-push.googleapis.com/upload/"
ROTATE_URL = "https://accounts.google.com/RotateCookies"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
PUSH_HEADERS = {
    "User-Agent": USER_AGENT,
    "push-id": "feeds/mcudyrk2a4khkz",
    "x-goog-upload-protocol": "resumable",
}

DEFAULT_BL = "boq_assistant-bard-web-server_20251119.01_p0"


class GeminiAuthError(RuntimeError):
    """Cookies missing / expired / logged-out."""


class GeminiRenderError(RuntimeError):
    """RPC executed but returned no usable image (rate limit, refusal, drift)."""


class ModelDegradedError(GeminiRenderError):
    """
    Pro→Flash degradation sensor trigger.

    Gemini Advanced Pro rarely returns an explicit 429. Instead the web client
    is silently downgraded from the Pro Imagen engine to the fast Flash model.
    When the response metadata advertises a -flash model, the account is
    exhausted for its current 5-hour rolling window — the pool puts it into a 5h
    cooldown and reroutes the job to the next Pro account.
    """


class GeminiClient:
    """One Google account = one silent HTTP session."""

    def __init__(self, psid: str, psidts: str | None = None, label: str = "") -> None:
        self.label = label or "account"
        self.token: str | None = None          # SNlM0e
        self.bl: str | None = None             # build label (cfb2h)
        self.f_sid: str | None = None
        self.email: str | None = None
        cookies = {"__Secure-1PSID": psid}
        if psidts:
            cookies["__Secure-1PSIDTS"] = psidts
        self.client = httpx.Client(
            base_url=BASE_URL,
            cookies=cookies,
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/app",
                "X-Same-Domain": "1",
            },
        )
        self._reqid = random.randint(100_000, 999_999)

    # ---------------------------------------------------------- lifecycle
    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    def init(self) -> None:
        """Fetch /app → extract SNlM0e, build labels, account email."""
        resp = self.client.get("/app")
        if resp.status_code != 200:
            raise GeminiAuthError(f"/app responded HTTP {resp.status_code}")
        html = resp.text

        m = re.search(r'"SNlM0e":"(.*?)"', html)
        if not m or not m.group(1):
            raise GeminiAuthError(
                "SNlM0e token not found — cookies expired, invalid, or the "
                "account is signed out. Reconnect with the Google/Gemini browser login."
            )
        self.token = m.group(1)

        bl = re.search(r'"cfb2h":"(.*?)"', html)
        sid = re.search(r'"FdrFJe":"(-?[0-9]+)"', html)
        self.bl = bl.group(1) if bl else DEFAULT_BL
        self.f_sid = sid.group(1) if sid else None

        mail = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', html)
        if mail:
            self.email = mail.group(1)

    # ------------------------------------------------------------ cookies
    def rotate_cookies(self) -> None:
        """
        Best-effort __Secure-1PSIDTS rotation (rotate endpoint answers with a
        fresh cookie inside Set-Cookie on HTTP 401).
        """
        try:
            resp = httpx.post(
                ROTATE_URL,
                http2=False,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "Referer": "https://accounts.google.com/",
                    "Cookie": self._cookie_header(),
                },
                content=(
                    '[0,"-1207300801110416021",[null,null,null,true,null,null,null,null,'
                    '[null,1,false,"[\\"https://accounts.google.com\\"]",6,null,false,1,null,null,2]]'
                ),
                timeout=30,
            )
            for chunk in resp.headers.get_list("set-cookie"):
                if chunk.startswith("__Secure-1PSIDTS"):
                    value = chunk.split("=", 1)[1].split(";")[0]
                    self.client.cookies.set("__Secure-1PSIDTS", value)
        except Exception:
            pass  # rotation is optional; existing cookie continues

    def _cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.client.cookies.items())

    # --------------------------------------------------------------- upload
    def upload_image(self, data: bytes, filename: str) -> str:
        """Resumable push of raw bytes; returns the lh3 upload identifier."""
        start = httpx.post(
            UPLOAD_URL,
            headers={
                **PUSH_HEADERS,
                "x-goog-upload-command": "start",
                "x-goog-upload-header-content-length": str(len(data)),
            },
            data={"File name": [filename]},
            timeout=60,
        )
        upload_url = start.headers.get("x-goog-upload-url")
        if not upload_url:
            raise GeminiRenderError(f"upload refused (HTTP {start.status_code})")

        fin = httpx.post(
            upload_url,
            headers={
                **PUSH_HEADERS,
                "x-goog-upload-command": "upload, finalize",
                "x-goog-upload-offset": "0",
            },
            content=data,
            timeout=120,
        )
        fin.raise_for_status()
        return fin.text.strip()

    # -------------------------------------------------------------- RPC gen
    def generate_image(
        self,
        prompt: str,
        images: list[tuple[bytes, str]],
        timeout_s: int = 150,
    ) -> bytes:
        """
        Multimodal batchexecute call: reference images + fused prompt → Imagen 3.
        `images`: list of (raw_bytes, filename). Returns raw PNG/JPEG bytes.
        """
        if not self.token:
            self.init()

        upload_ids = [self.upload_image(b, n) for b, n in images]
        return self._rpc(prompt, upload_ids, timeout_s)

    def _rpc(self, prompt: str, upload_ids: list[str], timeout_s: int) -> bytes:
        files_block = [[[uid] for uid in upload_ids]] if upload_ids else None
        inner = [
            [prompt],
            ["en"],
            ["", "", ""],           # conversation id / response id / choice id
            None, None, None, None, None, None, [0], None,
            files_block,            # multimodal attachment block (index 12)
            None,
            None,
            # Imagen 3 Pro flag — "enforce_default_to_fast_version": false
            # forces the flagship Pro render path over the fast Flash model.
            {"enforce_default_to_fast_version": False},
            ["gemini-imagen-3-pro"],
        ]
        self._reqid += random.randint(1000, 9000)

        params = {
            "rpcids": "maZAc",
            "source-path": "/app",
            "hl": "en",
            "rt": "c",
            "_reqid": str(self._reqid),
        }
        if self.bl:
            params["bl"] = self.bl
        if self.f_sid:
            params["f.sid"] = self.f_sid

        payload = {
            "at": self.token or "",
            "rpcids": "maZAc",
            "f.req": json.dumps([None, json.dumps(inner)]),
        }
        resp = self.client.post(
            BATCH_EXEC_URL,
            params=params,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=timeout_s,
        )

        if resp.status_code in (401, 403):
            # token went stale mid-flight → rotate + re-init once
            self.rotate_cookies()
            self.init()
            raise GeminiAuthError("session expired — re-authenticated the token, retry the job")

        if resp.status_code != 200:
            raise GeminiRenderError(f"batchexecute HTTP {resp.status_code}")

        # ---- Pro→Flash degradation sensor ----
        # A silently downgraded response only advertises the fast model. This is
        # the concrete capacity signal — surface it as its own exception type.
        model_hits = re.findall(r'"?(gemini-[0-9a-z.\-]*flash[0-9a-z.\-]*)"', resp.text, re.IGNORECASE)
        if model_hits and not re.search(r'gemini-(?:3|2(?:\.0)?|1\.5)[0-9a-z.\-]*pro', resp.text, re.IGNORECASE):
            raise ModelDegradedError(
                f"response model degraded to {model_hits[0]} — Pro window exhausted, account needs its 5h cooldown"
            )

        urls = parse_generated_image_urls(resp.text, exclude=set(upload_ids))
        if not urls:
            raise GeminiRenderError(
                "no Imagen URL in RPC response (rate limit / content refusal / protocol drift)"
            )
        # Strict single-output contract: even if Gemini returns several asset
        # candidates, download only the first. Never loop over alternatives.
        return self._download(urls[0])

    @staticmethod
    def _download(url: str) -> bytes:
        # Request the original rendition; never down-cap an 8K provider result
        # to 2048px before the verified print export.
        url = re.sub(r"=s\d+(-[-a-z0-9]+)?$", "=s0", url) if "=s" in url else f"{url}=s0"
        for attempt in (1, 2, 3):
            try:
                r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=90, follow_redirects=True)
                r.raise_for_status()
                if len(r.content) > 10_000:
                    return r.content
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1.5 * attempt)
        raise GeminiRenderError("image download failed on all attempts")


# ------------------------------------------------------------- parse utils
_LH3_RE = re.compile(r"https://lh3\.googleusercontent\.com/[A-Za-z0-9_\-=;/]+")


def parse_generated_image_urls(text: str, exclude: set[str] | None = None) -> list[str]:
    """
    Fish Imagen 3 URLs out of the batchexecute payload. Uploaded reference
    identifiers are lh3 URLs too, so they are excluded; `gg-dl` (the served
    generation download service) ranks first.
    """
    exclude = exclude or set()
    seen: list[str] = []
    bases: set[str] = set()
    for u in _LH3_RE.findall(text):
        base = re.sub(r"=s\d+(-[-a-z0-9]+)?$", "", u)
        if any(base in e or e in base for e in exclude):
            continue
        if base not in bases:
            bases.add(base)
            seen.append(u)
    # Python's stable sort preserves the first candidate. Do not inspect the
    # list itself inside its sort key (CPython temporarily empties that list).
    seen.sort(key=lambda u: "gg-dl" not in u)
    return seen
