"""Real Gemini provider pool with exclusive per-account render leases.

Pixel-to-Pixel distributes work across verified, available Pro sessions in
parallel. Legacy fusion keeps lowest-slot scheduling. Provider limits, auth
failures and model degradation are respected; mock production is blocked.
"""
from __future__ import annotations

import base64
import csv
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import db, gemini_client, image_lab
from .gemini_client import GeminiAuthError, GeminiRenderError, ModelDegradedError

MOCK = os.environ.get("MERCHFLOW_MOCK_ENGINE", "").lower() in {"1", "true", "yes"}

# ---- Configured local scheduling budgets -------------------------------------------
WINDOW_SECONDS = int(os.environ.get("MERCHFLOW_WINDOW_SECONDS", 5 * 3600))
WINDOW_CAP = int(os.environ.get("MERCHFLOW_WINDOW_CAP", 60))  # local budget; actual provider limits still apply
MINI_COOLDOWN_SECONDS = int(os.environ.get("MERCHFLOW_MINI_COOLDOWN", 90))
JITTER = (
    float(os.environ.get("MERCHFLOW_JITTER_MIN", 65)),
    float(os.environ.get("MERCHFLOW_JITTER_MAX", 85)),
)

FUSION_PROMPT = """Act as a print-on-demand vector artist.
IMAGE A is an ART-STYLE reference: extract ONLY its color palette, line weight,
vector shading and surface texture. Completely IGNORE the subject matter of
Image A — it must never appear in the output.
IMAGE B is the NICHE SUBJECT: render this exact subject with perfect fidelity,
redrawn 100% in the extracted style of Image A.
{TEXT_RULE}
Composition: single centred subject, clean silhouette edges, apparel design,
no frames, no watermarks, no mockups.
Aspect ratio {ASPECT}. Background instruction: {BG}."""


RECREATION_PROMPT = """Recreate the uploaded design concept as fresh, high-detail artwork.
Preserve its subject, composition, visual style, palette, and any legible text.
Refine contours, small details and typography. Do not merely recolour the input.
Keep the reference aspect ratio. Return ONE artwork image, not a comparison,
mockup, photograph of a garment, contact sheet, or watermarked preview.
Background: {BG}. Additional direction: {DIRECTION}.
The application will export the real render at 8192px on its longest edge."""


DONEPIXEL_PROMPT = "Recreate this exact uploaded graphic artwork in ultra-high resolution, 8K crisp print definition. Maintain 100% fidelity to the original composition, typography, characters, and graphic elements. Do NOT add variations, do NOT alter the subject or layout. Clean up all low-resolution artifacts, blur, and fabric wrinkles, and render the exact clean artwork strictly on a solid background of: {Selected_Color_Hex}."


def donepixel_prompt(colour: str) -> str:
    if colour.upper() not in {"#000000", "#FFFFFF"}:
        raise ValueError("Choose Pitch Black (#000000) or Pure White (#FFFFFF)")
    return DONEPIXEL_PROMPT.replace("{Selected_Color_Hex}", colour.upper())


def _build_prompt(payload: dict) -> str:
    if payload.get("task") == "donepixel":
        return donepixel_prompt(payload["background"])
    if payload.get("task") == "recreate":
        bg = {
            "transparent": "isolate the artwork on a simple backdrop for neural alpha matting",
            "black": "solid pure black, RGB 0 0 0",
            "white": "solid pure white, RGB 255 255 255",
            "original": "retain the reference background, including its colours and texture",
        }[payload.get("background", "transparent")]
        return RECREATION_PROMPT.format(BG=bg, DIRECTION=payload.get("instructions") or "faithful concept recreation")
    text = (payload.get("textOverride") or "").strip()
    text_rule = (
        f'TEXT OVERRIDE: render exactly the string "{text}" inside the design, '
        "set in typography matching Image A lettering. Spell it perfectly."
        if text
        else "TEXT RULE: generate PURE GRAPHIC ART. Absolutely no letters, words, "
        "numbers or garbled pseudo-text anywhere in the image."
    )
    bg = {
        "transparent": "subject isolated on a plain backdrop for neural alpha matting; clean edges",
        "white": "solid white",
        "black": "solid black",
    }.get(payload.get("background") or "transparent", "solid white")
    return FUSION_PROMPT.format(TEXT_RULE=text_rule, ASPECT=(payload.get("aspect") or "4:5"), BG=bg)


# --------------------------------------------------------------- clients
CLIENTS: dict[int, gemini_client.GeminiClient] = {}
_clients_lock = threading.Lock()


def client_for(account_id: int) -> gemini_client.GeminiClient | None:
    with _clients_lock:
        return CLIENTS.get(account_id)


def _register_client(account_id: int, client: gemini_client.GeminiClient | None) -> None:
    with _clients_lock:
        old = CLIENTS.pop(account_id, None)
        if old:
            old.close()
        if client:
            CLIENTS[account_id] = client


# ----------------------------------------------------------------- state
@dataclass
class AccountState:
    id: int
    email: str = ""
    provider: str = "chrome"
    secure_1psid: str = field(default="", repr=False)
    secure_1psidts: str = field(default="", repr=False)
    login_flow_id: str = ""
    verified_at: float = 0.0
    status: str = "auth-required"          # ready | active | cooldown | auth-required
    window_usage: int = 0                  # renders inside the current 5h window
    window_started: float = 0.0            # first render of the window
    cooldown_until: float = 0.0            # unix ts — window resets here
    is_pro: bool = False                   # verified Pro session (snlm0e init)
    last_used: float = 0.0
    daily_used: int = 0
    weekly_used: int = 0
    total_generated: int = 0
    daily_cap: int = 200                   # configured local budget, not provider quota
    weekly_cap: int = 1400

    @property
    def label(self) -> str:
        return self.email or "Gemini account"


class AccountPool:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self._busy: set[int] = set()
        self.accounts: list[AccountState] = []
        self._load()
        self._warm_clients()

    # ------------------------------ persistence
    @staticmethod
    def _from_row(row: dict) -> AccountState:
        account = AccountState(**{key: row[key] for key in AccountState.__dataclass_fields__ if key in row})
        account.is_pro = bool(account.is_pro)
        if account.status == "active":
            account.status = "cooldown" if account.cooldown_until > time.time() else "ready"
        return account

    def _load(self) -> None:
        # An empty SQLite database means exactly zero accounts. No JSON import,
        # placeholder profiles, fallback identities or fabricated auth state.
        self.accounts = [self._from_row(row) for row in db.session_rows()]

    def save(self) -> None:
        with self.lock:
            db.save_account_states([
                {"id": a.id, "login_flow_id": a.login_flow_id, "status": a.status,
                 "window_usage": a.window_usage, "window_started": a.window_started,
                 "cooldown_until": a.cooldown_until, "is_pro": a.is_pro,
                 "daily_used": a.daily_used, "weekly_used": a.weekly_used,
                 "total_generated": a.total_generated}
                for a in self.accounts if a.verified_at > 0
            ])

    def _warm_clients(self) -> None:
        for a in self.accounts:
            if a.secure_1psid:
                _register_client(a.id, gemini_client.GeminiClient(a.secure_1psid, a.secure_1psidts or None, a.label))

    # ------------------------------ window lifecycle
    def _reset_expired_locked(self) -> None:
        now = time.time()
        for a in self.accounts:
            if a.status != "auth-required" and (a.status == "cooldown" or a.window_usage >= WINDOW_CAP) and a.cooldown_until and a.cooldown_until <= now:
                a.status = "ready"
                a.window_usage = 0
                a.window_started = 0.0
                a.cooldown_until = 0.0

    # ------------------------------ session intake
    def next_free_slot(self) -> int:
        with self.lock:
            return max(db.next_account_id(), max((a.id for a in self.accounts), default=0) + 1)

    def register_session(self, flow, psid: str, psidts: str, email: str | None) -> dict:
        """Only the real browser sensor may supply an observed login flow.

        Verify before inserting anything. Failed/cancelled attempts create no
        row, no pool member, and no client; existing valid accounts stay intact.
        """
        with flow.lock:
            if flow.state != "pending" or not flow.authentication_observed:
                return {"ok": False, "authed": False, "reason": "Complete the real Google/Gemini browser login first"}
        client = gemini_client.GeminiClient(psid, psidts or None, "Google/Gemini login")
        try:
            client.init()
            verified_email = (client.email or email or "").strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", verified_email):
                raise GeminiAuthError("Could not verify the signed-in Google account email")
            with flow.lock, self.condition:
                if flow.state != "pending":
                    raise GeminiAuthError("Sign-in was cancelled; no account was saved")
                existing = next((a for a in self.accounts if a.id == flow.slot or a.email.casefold() == verified_email.casefold()), None)
                if existing and existing.id in self._busy:
                    raise GeminiAuthError("This account is generating; wait before reconnecting it")
                row = db.persist_browser_session(flow.slot, verified_email, flow.provider, psid, psidts, flow.id)
                account = self._from_row(row)
                self.accounts = sorted([a for a in self.accounts if a.id != account.id] + [account], key=lambda a: a.id)
                _register_client(account.id, client)
                flow.slot = account.id
                flow.email = account.email
                flow.label = account.email
                flow.state = "authenticated"
                self.condition.notify_all()
            return {"ok": True, "authed": True, "email": account.email, "label": account.label,
                    "provider": account.provider, "headless": True, "isPro": account.is_pro}
        except Exception as exc:
            client.close()
            return {"ok": False, "authed": False, "reason": str(exc)}

    def remove(self, account_id: int) -> bool:
        with self.condition:
            if account_id in self._busy:
                raise RuntimeError("This account is generating; wait before removing it")
            removed = db.delete_account(account_id)
            self.accounts = [a for a in self.accounts if a.id != account_id]
            _register_client(account_id, None)
            self.condition.notify_all()
            return removed

    # ------------------------------ SEQUENTIAL EXHAUSTION scheduling
    def ready_pro_count(self) -> int:
        with self.lock:
            self._reset_expired_locked()
            return sum(a.is_pro and a.status in {"ready", "active"}
                       and a.window_usage < WINDOW_CAP and client_for(a.id) is not None
                       for a in self.accounts)

    def acquire(self, *, parallel: bool = False, require_pro: bool = False,
                exclude: set[int] | None = None, timeout: float = 300) -> AccountState:
        """Reserve one session exclusively; wait only for busy eligible accounts.

        Cooldown/auth-required/Flash-degraded accounts are never dispatched.
        Parallel scheduling favours the least-recently-used free account.
        """
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                self._reset_expired_locked()
                eligible = [a for a in self.accounts
                            if a.status in {"ready", "active"}
                            and a.window_usage < WINDOW_CAP
                            and (not require_pro or a.is_pro)
                            and a.id not in (exclude or set())
                            and client_for(a.id) is not None]
                free = [a for a in eligible if a.id not in self._busy]
                if free:
                    a = min(free, key=lambda a: (a.last_used, a.id)) if parallel else free[0]
                    self._busy.add(a.id)
                    a.status = "active"
                    if not a.window_started:
                        a.window_started = time.time()
                    return a
                if not eligible:
                    raise RuntimeError("No available authenticated Pro account; check Account Pool, provider limits and cooldowns")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out waiting for an available Pro account; check Account Pool")
                self.condition.wait(min(remaining, 1.0))

    def release(self, account_id: int) -> None:
        with self.condition:
            self._busy.discard(account_id)
            a = next((a for a in self.accounts if a.id == account_id), None)
            if a and a.status == "active":
                a.status = "ready"
            self.condition.notify_all()

    def note_render(self, account_id: int) -> bool:
        """Record a successful render. Returns True if the window just filled → cooldown."""
        with self.lock:
            a = next((x for x in self.accounts if x.id == account_id), None)
            if not a:
                return False
            a.window_usage += 1
            a.daily_used += 1
            a.weekly_used += 1
            a.total_generated += 1
            a.last_used = time.time()
            if a.window_usage >= WINDOW_CAP:
                a.status = "cooldown"
                a.cooldown_until = time.time() + WINDOW_SECONDS
                self.save()
                return True
            a.status = "ready"
            self.save()
            return False

    def degrade(self, account_id: int) -> None:
        """Flash-degradation sensor → full 5h Pro cooldown, immediately."""
        with self.lock:
            a = next((x for x in self.accounts if x.id == account_id), None)
            if not a:
                return
            a.status = "cooldown"
            a.window_usage = max(a.window_usage, WINDOW_CAP)
            a.cooldown_until = time.time() + WINDOW_SECONDS
            a.last_used = time.time()
        self.save()

    def fault(self, account_id: int, auth: bool = False) -> None:
        with self.lock:
            a = next((x for x in self.accounts if x.id == account_id), None)
            if not a:
                return
            a.last_used = time.time()
            if auth:
                a.status = "auth-required"
                a.is_pro = False
            else:
                a.status = "cooldown"
                a.cooldown_until = time.time() + MINI_COOLDOWN_SECONDS
            self.save()

    def _wait_jitter(self, a: AccountState) -> None:
        jitter = random.uniform(*JITTER)
        wait = a.last_used + jitter - time.time()
        while wait > 0:
            time.sleep(min(1.0, wait))
            wait = a.last_used + jitter - time.time()
        a.last_used = time.time()

    # ------------------------------ UI cards
    def cards(self) -> list[dict]:
        with self.lock:
            self._reset_expired_locked()
            now = time.time()
            out = []
            for a in self.accounts:
                if not a.login_flow_id or a.verified_at <= 0 or not a.secure_1psid:
                    continue
                cool_left = max(0, int(a.cooldown_until - now)) if a.cooldown_until else 0
                out.append(
                    {
                        "id": a.id,
                        "label": a.label,
                        "email": a.email,
                        "provider": a.provider,
                        "status": a.status,
                        "authed": a.status != "auth-required" and client_for(a.id) is not None,
                        "authSource": "browser-login", "verifiedAt": a.verified_at,
                        "hasCookies": bool(a.secure_1psid),
                        "isPro": a.is_pro,
                        "headless": True,
                        "windowUsage": a.window_usage,
                        "windowCap": WINDOW_CAP,
                        "windowSeconds": WINDOW_SECONDS,
                        "cooldownUntil": a.cooldown_until or None,
                        "cooldownRemaining": cool_left,
                        "dailyUsed": a.daily_used,
                        "dailyCap": a.daily_cap,
                        "weeklyUsed": a.weekly_used,
                        "weeklyCap": a.weekly_cap,
                        "totalGenerated": a.total_generated,
                    }
                )
            return out


POOL = AccountPool()


def profile_cards() -> list[dict]:
    return POOL.cards()


def _b64_of(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(",", 1)[-1])


def _write_manifests(rows: list[dict], out_dir: Path, push: Callable[[dict], None]) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    csv_path = out_dir / f"productor-mba_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Design File", "Brand", "Title", "Bullet Point 1", "Bullet Point 2", "Main Tag", "Tags"])
        for r in rows:
            w.writerow([r["file"], r["brand"], r["title"], r["bullet1"], r["bullet2"], r["main_tag"], r["tags"]])
    push({"type": "log", "msg": f"Productor CSV → {csv_path.name}"})
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "MerchFlow"
        ws.append(["Design File", "Brand", "Title", "Bullet 1", "Bullet 2"])
        for r in rows:
            ws.append([r["file"], r["brand"], r["title"], r["bullet1"], r["bullet2"]])
        xlsx_path = out_dir / f"merchflow-batch_{stamp}.xlsx"
        wb.save(xlsx_path)
        push({"type": "log", "msg": f"Excel workbook → {xlsx_path.name}"})
    except Exception:
        pass


def run_batch(payload: dict, push: Callable[[dict], None], out_dir: Path) -> dict:
    """Only real provider images may enter the save/History pipeline."""
    if MOCK:
        raise RuntimeError("Mock generation is disabled for design production. Unset MERCHFLOW_MOCK_ENGINE and connect a real account.")
    out_dir.mkdir(parents=True, exist_ok=True)
    recreate = payload.get("task") == "recreate"
    style_name = payload.get("styleName") or ("8K recreation" if recreate else "style")
    style_bytes = b"" if recreate else _b64_of(payload["styleImage"])
    niche_items = payload.get("nicheImages") or []
    prompt = _build_prompt(payload)
    rows: list[dict] = []
    # A sequential raster queue prevents several full print/8K canvases and
    # neural mattes from exhausting a normal desktop PC's memory.
    for item in niche_items:
        job_id = item["id"]
        name = item.get("name") or job_id
        try:
            niche_bytes = _b64_of(item["image"])
        except Exception as exc:
            push({"type": "failed", "job": job_id, "name": name, "reason": f"Invalid reference image: {exc}"})
            continue
        raw = None
        last_err = None
        t0 = time.perf_counter()
        acct = None
        for _ in range(max(1, min(3, len(POOL.accounts)))):
            acct = None
            try:
                acct = POOL.acquire()
                push({"type": "dispatch", "job": job_id, "name": name, "account": acct.label})
                POOL._wait_jitter(acct)
                client = client_for(acct.id)
                if client is None:
                    raise GeminiAuthError("No live provider session; connect an account in Account Pool")
                push({"type": "progress", "job": job_id, "pct": 25, "msg": f"[{acct.label}] sending reference to the image provider"})
                references = [(niche_bytes, "concept-reference.png")] if recreate else [(style_bytes, "style_ref.png"), (niche_bytes, f"niche_{job_id}.png")]
                raw = client.generate_image(prompt, references)
                POOL.note_render(acct.id)
                break
            except ModelDegradedError as exc:
                last_err = exc
                if acct:
                    POOL.degrade(acct.id)
            except GeminiAuthError as exc:
                last_err = exc
                if acct:
                    POOL.fault(acct.id, auth=True)
            except GeminiRenderError as exc:
                last_err = exc
                if acct:
                    POOL.fault(acct.id)
            except Exception as exc:
                last_err = exc
                break
            finally:
                if acct is not None:
                    POOL.release(acct.id)
        if raw is None or acct is None:
            push({"type": "failed", "job": job_id, "name": name, "reason": str(last_err or "No authenticated account available")[:400]})
            continue
        try:
            push({"type": "progress", "job": job_id, "pct": 80, "msg": "Provider returned an image; processing and verifying the PNG on disk"})
            png, report = image_lab.postprocess_generate(raw, {**payload, "prompt": prompt, "account": acct.label}, out_dir, name)
            del png, raw
            ms = round((time.perf_counter() - t0) * 1000)
            # The saved record exists before success is emitted, even if the
            # browser closes immediately and never receives this event.
            push({
                "type": "image", "job": job_id, "name": name, "account": acct.label,
                "ms": ms, "path": report["path"], "w": report["w"], "h": report["h"],
                "saved": report["saved"], "bytes": report["bytes"], "fileUrl": report["fileUrl"],
                "design": report["design"], "nativeW": report.get("nativeW"), "nativeH": report.get("nativeH"),
                "upscaled": report.get("upscaled", False),
            })
            rows.append({
                "file": Path(report["path"]).name,
                "brand": "", "title": name[:60], "bullet1": "", "bullet2": "",
                "main_tag": name.replace("_", " ").lower(), "tags": style_name,
            })
        except Exception as exc:
            # Matting/disk errors are NOT provider account faults and must not
            # trigger another costly generation or a fake 'done' event.
            push({"type": "failed", "job": job_id, "name": name, "reason": str(exc)[:600]})
    if rows and not recreate:
        _write_manifests(rows, out_dir, push)
    return {"completed": len(rows), "failed": len(niche_items) - len(rows)}


def render_donepixel(reference: bytes, colour: str, dispatch: Callable[[str], None]) -> tuple[bytes, str, str]:
    """Real multimodal recreation, one concurrent render per verified account.

    Disk/encoder failures happen outside this function, so they never consume a
    second provider render or mark a healthy account as faulty.
    """
    if MOCK:
        raise RuntimeError("Mock generation is disabled for design production")
    prompt = donepixel_prompt(colour)
    tried: set[int] = set()
    last_error: Exception | None = None
    for _ in range(max(1, len(POOL.accounts))):
        account = None
        try:
            account = POOL.acquire(parallel=True, require_pro=True, exclude=tried)
            tried.add(account.id)
            dispatch(account.label)
            POOL._wait_jitter(account)
            client = client_for(account.id)
            if client is None:
                raise GeminiAuthError("Provider session expired; reconnect in Account Pool")
            raw = client.generate_image(prompt, [(reference, "concept-reference.png")])
            if not raw:
                raise GeminiRenderError("Provider returned no image")
            POOL.note_render(account.id)
            return raw, account.label, prompt
        except ModelDegradedError as exc:
            last_error = exc
            if account is not None:
                POOL.degrade(account.id)
        except GeminiAuthError as exc:
            last_error = exc
            if account is not None:
                POOL.fault(account.id, auth=True)
        except GeminiRenderError as exc:
            last_error = exc
            if account is not None:
                POOL.fault(account.id)
        except Exception as exc:
            last_error = exc
            break
        finally:
            if account is not None:
                POOL.release(account.id)
    raise RuntimeError(str(last_error or "No authenticated Pro account available"))
