"""
MerchFlow Pro — Gemini Advanced (18-Month Pro) SEQUENTIAL EXHAUSTION pool.

The Donepixel / PodTitan infinite loop. Gemini Advanced Pro accounts do not use
a 24-hour daily cap — they use an empirical 5-HOUR ROLLING COMPUTE WINDOW
(~60 high-res Imagen 3 Pro renders). Strategy:

  1. Every job goes to Account 1 until it reaches its Pro window capacity (~60).
  2. It enters a 5h cooldown (or the Flash-degradation sensor forces it early)
     and the entire workload shifts to Account 2.
  3. → Account 3 → 4 → 5 … By the time the tail accounts finish, Account 1's
     window has RESET and it comes back 🟢 Ready.
  4. Result: an uninterrupted 24/7 pipeline producing 500+ designs/day across
     5–10 accounts at $0 token cost.

  Degradation sensor: gemini_client raises ModelDegradedError the instant the
  response metadata advertises a -flash model → cooldown_until = now + 5h and
  the job reroutes immediately.

  MERCHFLOW_MOCK_ENGINE=1 shortens windows so the full sequential cycle is
  demonstrable without cookies.
"""
from __future__ import annotations

import base64
import csv
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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

FUSION_PROMPT = """Act as a print-on-demand vector artist.
IMAGE A is an ART-STYLE reference: extract ONLY its color palette, line weight,
vector shading and surface texture. Completely IGNORE the subject matter of
Image A — it must never appear in the output.
IMAGE B is the NICHE SUBJECT: render this exact subject with perfect fidelity,
redrawn 100% in the extracted style of Image A.
{TEXT_RULE}
Composition: single centred subject, clean silhouette edges, apparel design,
isolated on a plain white background, no frames, no watermarks, no mockups.
Aspect ratio {ASPECT}. Background instruction: {BG}."""


def _build_prompt(payload: dict) -> str:
    text = (payload.get("textOverride") or "").strip()
    text_rule = (
        f'TEXT OVERRIDE: render exactly the string "{text}" inside the design, '
        "set in typography matching Image A lettering. Spell it perfectly."
        if text
        else "TEXT RULE: generate PURE GRAPHIC ART. Absolutely no letters, words, "
        "numbers or garbled pseudo-text anywhere in the image."
    )
    bg = {
        "transparent": "subject on solid white (keyed out later — keep edges clean)",
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
        self.lock = threading.Lock()
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
            CONFIG_PATH.write_text(json.dumps({"jitter_seconds": [65, 85], "window_cap": WINDOW_CAP, "accounts": rows}, indent=2), encoding="utf-8")
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
    def acquire(self) -> AccountState:
        """
        Slot order only — never round-robin. The first Pro account with window
        capacity takes the ENTIRE workload until exhausted.
        """
        with self.lock:
            self._reset_expired_locked()
            if MOCK:
                healthy = [a for a in self.accounts if a.status in ("ready", "active") and client_for(a.id) is not None] or list(self.accounts)
                healthy = [a for a in healthy if a.window_usage < WINDOW_CAP]
            else:
                healthy = [
                    a for a in self.accounts
                    if a.status in ("ready", "active")
                    and a.window_usage < WINDOW_CAP
                    and client_for(a.id) is not None
                ]
            if not healthy:
                raise RuntimeError("pool exhausted — every Pro account is in its 5h cooldown; they reset automatically")
            a = healthy[0]  # lowest slot = head of the exhaustion chain
            a.status = "active"
            if not a.window_started:
                a.window_started = time.time()
            return a

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


def run_batch(payload: dict, push: Callable[[dict], None], out_dir: Path) -> None:
    style_name = payload.get("styleName") or "style"
    style_bytes = _b64_of(payload["styleImage"])
    niche_items = payload.get("nicheImages") or []
    prompt = _build_prompt(payload)

    rows: list[dict] = []
    rows_lock = threading.Lock()
    workers = max(1, min(4, len(POOL.accounts)))  # sequential pool, bounded concurrency

    def run_one(item: dict) -> None:
        job_id = item["id"]
        name = item.get("name") or job_id
        niche_bytes = _b64_of(item["image"])

        attempts = 0
        last_err: Exception | None = None
        max_attempts = max(1, min(3, len(POOL.accounts)))
        while attempts < max_attempts:
            attempts += 1
            try:
                acct = POOL.acquire()
            except RuntimeError as e:
                push({"type": "failed", "job": job_id, "name": name, "reason": str(e)})
                return
            push({"type": "dispatch", "job": job_id, "name": name, "account": acct.label})
            t0 = time.time()
            try:
                POOL._wait_jitter(acct)
                push({
                    "type": "progress", "job": job_id, "pct": 30,
                    "msg": f"[{acct.label}] window {acct.window_usage}/{WINDOW_CAP} · uploading refs → batchexecute (Pro flag on)",
                })
                if MOCK:
                    raw = image_lab.mock_render(
                        style_bytes, niche_bytes,
                        payload.get("aspect") or "4:5",
                        payload.get("background") or "transparent",
                        payload.get("textOverride") or "",
                    )
                    time.sleep(random.uniform(1.5, 3.0))
                else:
                    client = client_for(acct.id)
                    if client is None:
                        raise GeminiAuthError("no live session — sign the account in once via the login window")
                    raw = client.generate_image(
                        prompt,
                        [(style_bytes, "style_ref.png"), (niche_bytes, f"niche_{job_id}.png")],
                    )
                push({"type": "progress", "job": job_id, "pct": 82, "msg": f"[{acct.label}] libvips keep-holes → 4500×5400 lanczos3"})
                png, report = image_lab.postprocess_generate(raw, payload, out_dir, name)
                ms = int((time.time() - t0) * 1000)
                push(
                    {
                        "type": "image", "job": job_id, "name": name, "account": acct.label,
                        "ms": ms, "png": base64.b64encode(png).decode(),
                        "path": report["path"], "w": report["w"], "h": report["h"],
                    }
                )
                with rows_lock:
                    niche_txt = name.replace("_", " ").strip().title()
                    rows.append(
                        {
                            "file": Path(report["path"]).name,
                            "brand": f"{niche_txt} Culture Co"[:30],
                            "title": f"Retro {niche_txt} Design"[:60],
                            "bullet1": f"Striking {style_name} {niche_txt.lower()} artwork, headless-rendered and print-prepared.",
                            "bullet2": "Pairs effortlessly with denim, joggers or a flannel layer.",
                            "main_tag": niche_txt.lower(),
                            "tags": ", ".join(sorted({niche_txt.lower(), f"{niche_txt.lower()} lover", f"{style_name.lower()} art"})),
                        }
                    )
                if POOL.note_render(acct.id):
                    push({"type": "log", "msg": f"[{acct.label}] Pro window full ({WINDOW_CAP}) → 5h cooldown, workload shifts to next account"})
                return
            except ModelDegradedError as e:
                last_err = e
                POOL.degrade(acct.id)
                push({"type": "progress", "job": job_id, "pct": 12, "msg": f"[{acct.label}] ⚠ Flash downgrade detected → 5h cooldown, rerouting job"})
            except GeminiAuthError as e:
                last_err = e
                POOL.fault(acct.id, auth=True)
                push({"type": "progress", "job": job_id, "pct": 12, "msg": f"[{acct.label}] session expired → auth-required, rerouting"})
            except GeminiRenderError as e:
                last_err = e
                POOL.fault(acct.id)
                push({"type": "progress", "job": job_id, "pct": 12, "msg": f"[{acct.label}] cooldown — rerouting job"})
            except Exception as e:  # pragma: no cover
                last_err = e
                POOL.fault(acct.id)
                push({"type": "progress", "job": job_id, "pct": 12, "msg": f"[{acct.label}] worker error — rerouting job"})
        push({"type": "failed", "job": job_id, "name": name, "reason": str(last_err)[:220]})

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run_one, niche_items))

    if rows:
        _write_manifests(rows, out_dir, push)
