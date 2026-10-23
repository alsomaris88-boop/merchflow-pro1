"""Real Gemini provider pool with exclusive per-account render leases.

Pixel-to-Pixel distributes work across verified, available Pro sessions in
parallel. Legacy fusion keeps lowest-slot scheduling. Provider limits, auth
failures and model degradation are respected; mock production is blocked.
"""
from __future__ import annotations

import base64
import csv
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import gemini_client, image_lab
from .gemini_client import GeminiAuthError, GeminiRenderError, ModelDegradedError

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "gemini_accounts.json"
MOCK = os.environ.get("MERCHFLOW_MOCK_ENGINE", "").lower() in {"1", "true", "yes"}

# ---- 18-month Pro window tuning -------------------------------------------
WINDOW_SECONDS = int(os.environ.get("MERCHFLOW_WINDOW_SECONDS", "150" if MOCK else 5 * 3600))
WINDOW_CAP = int(os.environ.get("MERCHFLOW_WINDOW_CAP", "6" if MOCK else 60))  # ~60 Pro renders/window
MINI_COOLDOWN_SECONDS = int(os.environ.get("MERCHFLOW_MINI_COOLDOWN", "10" if MOCK else 90))
JITTER = (
    float(os.environ.get("MERCHFLOW_JITTER_MIN", "1" if MOCK else 65)),
    float(os.environ.get("MERCHFLOW_JITTER_MAX", "2" if MOCK else 85)),
)

# ---------------------------------------------------------------------------
# CLIENT-MANDATED MULTIMODAL DIRECTIVE — injected VERBATIM into every
# Gemini Pro Style × Niche fusion generation. Do not paraphrase, trim, or
# reword a single character of the directive body.
#
# Reference upload order is contractually bound to this text:
#   IMAGE 1 (first upload)  = the NICHE SUBJECT   (Dropzone B in Module 01)
#   IMAGE 2 (second upload) = the ART STYLE        (Dropzone A in Module 01)
# ---------------------------------------------------------------------------
FUSION_DIRECTIVE = """[MANDATORY MULTIMODAL DIRECTIVE: STYLE × NICHE COMPLETE FUSION]

I have provided two reference images:
- IMAGE 1 = The NICHE SUBJECT (The character/object/theme to be drawn)
- IMAGE 2 = The ART STYLE REFERENCE (The visual DNA, aesthetic, and medium)

YOUR MISSION:
Completely re-draw and synthesize the core subject of IMAGE 1 entirely inside the exact artistic medium and visual universe of IMAGE 2.

EXECUTION RULES:

1. STYLE EXTRACTION (From IMAGE 2):
   - Extract the full visual DNA: exact color palette, line weight, shading method, lighting contrast, artistic medium (e.g. vintage screenprint, 90s bootleg rap tee, distressed retro vector, hand-drawn linework, or airbrush texture).
   - STRICTLY FILTER OUT: Ignore any t-shirt mockups, fabric wrinkles, watermarks, background frames, logos, or overlaid stamps from IMAGE 2.

2. SUBJECT EMBODIMENT (From IMAGE 1):
   - Capture the essence, character, pose, and distinctive anatomy/features of the subject in IMAGE 1.
   - Do NOT simply cut and paste IMAGE 1 onto IMAGE 2. You must re-imagine and illustrate the subject from scratch using IMAGE 2's visual grammar, textures, and color hierarchy.

3. PRINT-ON-DEMAND GRAPHIC QUALITY:
   - Output must be an ultra-high resolution (8K clarity), high-contrast, master apparel graphic design ready for commercial printing.
   - Razor-sharp edges, vibrant focal subject, zero blur, zero random gibberish or garbled AI text.
   - Background: Render the isolated graphic cleanly on a pure, solid pitch-black background (#000000) with zero t-shirt collars or garment borders.

Generate the finished master apparel graphic now."""


RECREATION_PROMPT = """Recreate the uploaded design concept as fresh, high-detail artwork.
Preserve its subject, composition, visual style, palette, and any legible text.
Refine contours, small details and typography. Do not merely recolour the input.
Keep the reference aspect ratio. Return ONE artwork image, not a comparison,
mockup, photograph of a garment, contact sheet, or watermarked preview.
Background: {BG}. Additional direction: {DIRECTION}.
The application will export the real render at 8192px on its longest edge."""


# Client-mandated Donepixel 1:1 recreation wording — sent VERBATIM per input.
# One render call → one downloaded image → exactly ONE archived design per
# upload; the "Do NOT add variations" clause rejects multi-variant responses.
DONEPIXEL_PROMPT = "Recreate this exact uploaded graphic artwork in ultra-high resolution, 8K crisp print definition. Maintain 100% fidelity to the original composition, typography, characters, and graphic elements. Do NOT add variations, do NOT change the subject. Remove all background wrinkles/mockups and render the clean artwork strictly on a solid {Selected_Color_Hex} background."


def donepixel_prompt(colour: str) -> str:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
        raise ValueError("Background must be a six-digit hex colour, such as #000000")
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
        f'TEXT OVERRIDE: additionally render exactly the string "{text}" inside the design, '
        "set in typography matching IMAGE 2's lettering. Spell it perfectly."
        if text
        else "TEXT RULE: generate PURE GRAPHIC ART. Absolutely no letters, words, "
        "numbers or garbled pseudo-text anywhere in the image."
    )
    # The directive mandates a pitch-black (#000000) backdrop. Only an explicit
    # operator override below may relax that single clause (e.g. when the local
    # worker is about to neural-mat the subject onto transparency).
    bg = {
        "transparent": "BACKGROUND OVERRIDE: despite the pitch-black clause above, render the isolated graphic on a plain, uniform, high-contrast backdrop so the local worker can neural-mat it to true transparency; keep edges razor-clean.",
        "white": "BACKGROUND OVERRIDE: despite the pitch-black clause above, render the isolated graphic on a solid pure white background (RGB 255 255 255) with zero garment borders.",
    }.get(payload.get("background") or "black")
    addendum = (
        "\n\n[RUNTIME OPERATOR SETTINGS — append-only; every clause of the mandatory "
        "directive above remains in force unless a setting below explicitly overrides it]\n"
        f"- Canvas aspect ratio: {payload.get('aspect') or '4:5'}.\n"
        f"- {text_rule}\n"
        + (f"- {bg}\n" if bg else "")
    )
    return FUSION_DIRECTIVE + addendum


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
    secure_1psid: str = ""
    secure_1psidts: str = ""
    status: str = "auth-required"          # ready | active | cooldown | auth-required
    window_usage: int = 0                  # renders inside the current 5h window
    window_started: float = 0.0            # first render of the window
    cooldown_until: float = 0.0            # unix ts — window resets here
    is_pro: bool = False                   # verified Pro session (snlm0e init)
    last_used: float = 0.0
    daily_used: int = 0
    weekly_used: int = 0
    total_generated: int = 0
    daily_cap: int = 200                   # 200+ designs/day across the loop
    weekly_cap: int = 1400

    @property
    def label(self) -> str:
        return f"Profile {self.id:02d}"


class AccountPool:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self._busy: set[int] = set()
        self.accounts: list[AccountState] = []
        self._load()
        self._warm_clients()

    # ------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            rows = data.get("accounts", [])
        except Exception:
            rows = [{"id": i} for i in range(1, 6)]
        for r in rows:
            a = AccountState(
                id=int(r.get("id", self.accounts.__len__() + 1)),
                email=r.get("email", ""),
                provider=r.get("provider", "chrome"),
                secure_1psid=r.get("secure_1psid", ""),
                secure_1psidts=r.get("secure_1psidts", ""),
                window_usage=int(r.get("window_usage", 0)),
                window_started=float(r.get("window_started", 0.0)),
                cooldown_until=float(r.get("cooldown_until", 0.0)),
                is_pro=bool(r.get("is_pro", False)),
                daily_used=int(r.get("daily_used", 0)),
                weekly_used=int(r.get("weekly_used", 0)),
                total_generated=int(r.get("total_generated", 0)),
            )
            if r.get("status") and not a.secure_1psid:
                a.status = r.get("status")
            elif a.secure_1psid:
                a.status = "cooldown" if a.cooldown_until > time.time() else "ready"
            self.accounts.append(a)
        self.accounts.sort(key=lambda a: a.id)

    def save(self) -> None:
        with self.lock:
            rows = [
                {
                    "id": a.id, "label": a.label, "email": a.email, "provider": a.provider,
                    "secure_1psid": a.secure_1psid, "secure_1psidts": a.secure_1psidts,
                    "window_usage": a.window_usage, "window_started": a.window_started,
                    "cooldown_until": a.cooldown_until, "is_pro": a.is_pro,
                    "daily_used": a.daily_used, "weekly_used": a.weekly_used,
                    "total_generated": a.total_generated,
                }
                for a in self.accounts
            ]
            try:
                temp = CONFIG_PATH.with_suffix(".json.part")
                temp.write_text(json.dumps({"jitter_seconds": [65, 85], "window_cap": WINDOW_CAP, "accounts": rows}, indent=2), encoding="utf-8")
                os.replace(temp, CONFIG_PATH)
            except Exception:
                pass

    def _warm_clients(self) -> None:
        for a in self.accounts:
            if a.secure_1psid:
                _register_client(a.id, gemini_client.GeminiClient(a.secure_1psid, a.secure_1psidts or None, a.label))

    # ------------------------------ window lifecycle
    def _reset_expired_locked(self) -> None:
        now = time.time()
        for a in self.accounts:
            if (a.status == "cooldown" or a.window_usage >= WINDOW_CAP) and a.cooldown_until and a.cooldown_until <= now:
                a.status = "ready"
                a.window_usage = 0
                a.window_started = 0.0
                a.cooldown_until = 0.0

    # ------------------------------ session intake
    def next_free_slot(self) -> int:
        with self.lock:
            used = {a.id for a in self.accounts}
            for i in range(1, 200):
                if i not in used:
                    return i
            return len(used) + 1

    def register_session(self, account_id: int, psid: str, psidts: str, email: str | None, provider: str, label: str | None = None) -> dict:
        with self.lock:
            acct = next((a for a in self.accounts if a.id == account_id), None)
            if acct is None:
                acct = AccountState(id=account_id)
                self.accounts.append(acct)
                self.accounts.sort(key=lambda x: x.id)
            acct.secure_1psid = (psid or "").strip()
            acct.secure_1psidts = (psidts or "").strip()
            acct.provider = provider
            if email:
                acct.email = email

        client = gemini_client.GeminiClient(acct.secure_1psid, acct.secure_1psidts or None, acct.label)
        try:
            client.init()  # headless SNlM0e — proves the Pro session
            if client.email:
                acct.email = client.email
            _register_client(acct.id, client)
            with self.lock:
                acct.is_pro = True
                acct.status = "ready"
                acct.window_usage = 0
                acct.cooldown_until = 0.0
            self.save()
            return {"ok": True, "authed": True, "email": acct.email, "label": acct.label, "provider": provider, "headless": True, "isPro": True}
        except Exception as e:
            _register_client(acct.id, None)
            with self.lock:
                acct.status = "auth-required"
                acct.is_pro = False
            self.save()
            return {"ok": False, "authed": False, "reason": str(e), "email": acct.email, "label": acct.label}

    def verify_cookies(self, account_id: int, psid: str, psidts: str, label: str | None, email: str | None) -> dict:
        with self.lock:
            acct = next((a for a in self.accounts if a.id == account_id), None)
            provider = acct.provider if acct else "chrome"
        return self.register_session(account_id, psid, psidts, email, provider, label)

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
                cool_left = max(0, int(a.cooldown_until - now)) if a.cooldown_until else 0
                out.append(
                    {
                        "id": a.id,
                        "label": a.label,
                        "email": a.email,
                        "provider": a.provider,
                        "status": a.status,
                        "authed": client_for(a.id) is not None,
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
                # Upload order is the directive contract: niche FIRST (= IMAGE 1
                # subject), style SECOND (= IMAGE 2 visual DNA). Never reorder.
                references = [(niche_bytes, "concept-reference.png")] if recreate else [(niche_bytes, f"niche_{job_id}_IMAGE1.png"), (style_bytes, "style_ref_IMAGE2.png")]
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
