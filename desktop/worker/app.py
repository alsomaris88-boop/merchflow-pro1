"""
MerchFlow Pro — UNIFIED desktop engine (FastAPI), single port 127.0.0.1:8000.

One process serves EVERYTHING for the desktop build — no Node.js at runtime:

  UI      GET  /                        → pre-built static Next.js bundle (out/)
  APIs    ALL  /api/...                 → SQLite-backed, same shapes the UI uses
          /api/bridge/generate|process  → aliases of /generate/stream & /process
          /api/bridge/accounts|login/*  → aliases of /accounts/*
          /api/bridge/elite/{tool}      → aliases of /elite/{tool}
  Engine  POST /generate/stream         → SSE pooled Imagen-3 batch (headless)
          POST /process                 → libvips Prepare pipeline
          /accounts/login/start|status  → one-time interactive login window

Static mount resolution order:  ./out  → ../out  → bundled landing page.

Run:  uvicorn worker.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import browser, db, elite_tools, gemini_pool, image_lab, trademark_shield, trends_local

WORKER_VERSION = "2.1.0-unified"

OUT_DIR = Path(os.environ.get("MERCHFLOW_OUT") or (Path.home() / "MerchFlow" / "Output"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

_here = Path(__file__).resolve()
STATIC_CANDIDATES = [
    Path(os.environ.get("MERCHFLOW_UI_DIR") or ""),
    _here.parent.parent.parent / "out",           # tidy shipped bundle
    _here.parent.parent.parent / ".next-export",  # raw Next 16 Turbopack export
    _here.parent.parent / "out",                  # desktop/out (bundled)
]
STATIC_DIR = next((p for p in STATIC_CANDIDATES if p and (p / "index.html").exists()), None)

app = FastAPI(title="MerchFlow Local Engine", version=WORKER_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1024)


# ===========================================================================
# UNIFIED API SURFACE (shapes identical to the Next.js /api routes)
# ===========================================================================
@app.get("/api/health")
@app.get("/health")
async def health() -> dict:
    return {
        "ok": True, "db": True, "connected": True, "unified": True,
        "version": WORKER_VERSION,
        "engine": f"gemini-headless-batchexecute + {image_lab.ENGINE_NAME}",
        "libvips": image_lab.HAS_VIPS, "mock": gemini_pool.MOCK, "headless": True,
        "outDir": str(OUT_DIR), "profiles": gemini_pool.profile_cards(),
        "capabilities": elite_tools.CAPABILITIES,
    }


@app.get("/api/bridge")
async def bridge_probe() -> dict:
    return await health()


# ------------------------------------------------------------- accounts
@app.get("/api/accounts")
async def api_accounts() -> dict:
    return {"source": "sqlite", "items": db.list_accounts()}


class AccountCreateReq(BaseModel):
    label: str
    email: str
    provider: str = "chrome"


@app.post("/api/accounts")
async def api_accounts_post(req: AccountCreateReq) -> JSONResponse:
    return JSONResponse(db.add_account(req.label, req.email, req.provider))


class AccountPatchReq(BaseModel):
    id: int
    status: str | None = None


@app.patch("/api/accounts")
async def api_accounts_patch(req: AccountPatchReq) -> dict:
    return db.patch_account(req.id, req.status)


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs")
async def api_jobs() -> dict:
    return {"items": db.list_jobs()}


class JobReq(BaseModel):
    styleName: str = ""
    nicheName: str = ""
    aspect: str = "4:5"
    background: str = "transparent"
    textOverride: str = ""
    accountLabel: str = ""
    status: str = "done"
    durationMs: int = 0


@app.post("/api/jobs")
async def api_jobs_post(req: JobReq) -> dict:
    return db.add_job(req.model_dump())


# -------------------------------------------------------------- activity
@app.get("/api/activity")
async def api_activity() -> dict:
    jobs = db.list_jobs(8)
    listings = db.list_listings(8)
    items = [
        {
            "kind": "job",
            "title": f"{j['style_name'] or 'Style'} × {j['niche_name'] or 'niche'}",
            "detail": f"{j['aspect']} · {j['background']} · {j['account_label'] or 'pool'} · {round(j['duration_ms'] / 1000)}s",
            "at": j["created_at"],
        }
        for j in jobs
    ] + [
        {
            "kind": "listing",
            "title": l["title"] or l["niche"],
            "detail": f"MBA + TeePublic · main tag: {l['main_tag']} · risk {l['risk_score']}/100",
            "at": l["created_at"],
        }
        for l in listings
    ]
    return {"items": items[:10], "jobCount": len(db.list_jobs(200)), "listingCount": len(listings), "source": "sqlite"}


# ----------------------------------------------------------------- trends
@app.get("/api/trends")
async def api_trends(view: str = "suggest", q: str = "") -> JSONResponse:
    if view == "suggest":
        return JSONResponse(await trends_local.get_suggestions(q))
    if view == "movers":
        return JSONResponse(trends_local.movers())
    if view == "etsy":
        return JSONResponse(trends_local.etsy())
    if view == "pinterest":
        return JSONResponse(trends_local.pinterest())
    return JSONResponse({"error": "unknown view"}, status_code=400)


# ---------------------------------------------------------------- trademark
class TmReq(BaseModel):
    text: str = ""


@app.post("/api/trademark")
async def api_trademark(req: TmReq) -> JSONResponse:
    text = (req.text or "")[:4000]
    if not text.strip():
        return JSONResponse({"error": "text is required"}, status_code=400)
    return JSONResponse(trademark_shield.analyze_text(text))


# ----------------------------------------------------------------- license
class LicenseReq(BaseModel):
    key: str = ""
    hwid: str = ""


@app.post("/api/license")
async def api_license(req: LicenseReq) -> JSONResponse:
    key = (req.key or "").strip().upper()[:40]
    hwid = (req.hwid or "")[:64]
    if not key:
        return JSONResponse({"valid": False, "reason": "Enter a license key."}, status_code=400)
    res = db.license_verify(key, hwid)
    if res is None:
        return JSONResponse({"valid": False, "reason": "Invalid key. Check your LemonSqueezy receipt."})
    return JSONResponse(res)


# ---------------------------------------------------------------- listings
@app.get("/api/listings")
async def api_listings() -> dict:
    return {"items": db.list_listings()}


class ListingReq(BaseModel):
    niche: str = ""
    brand: str = ""
    title: str = ""
    bullet1: str = ""
    bullet2: str = ""
    tpTitle: str = ""
    tpDescription: str = ""
    mainTag: str = ""
    tags: str = ""
    riskScore: int = 0


@app.post("/api/listings")
async def api_listings_post(req: ListingReq) -> JSONResponse:
    try:
        return JSONResponse(db.add_listing(req.model_dump()))
    except Exception as e:
        return JSONResponse({"error": "listing insert failed", "detail": str(e)}, status_code=500)


# ===========================================================================
# Engine endpoints (identical to before) + /api/bridge aliases
# ===========================================================================
class CookiesReq(BaseModel):
    id: int
    psid: str
    psidts: str | None = ""
    label: str | None = None
    email: str | None = None


@app.post("/accounts/cookies")
@app.post("/api/bridge/accounts")
async def set_cookies(req: CookiesReq) -> JSONResponse:
    if not req.psid or len(req.psid.strip()) < 20:
        return JSONResponse({"ok": False, "authed": False, "reason": "__Secure-1PSID looks too short."}, status_code=400)
    result = await asyncio.to_thread(
        gemini_pool.POOL.verify_cookies, req.id, req.psid, req.psidts or "", req.label, req.email
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 401)


@app.get("/accounts/status")
@app.get("/api/bridge/accounts")       # Next bridge GET returns account cards
@app.get("/api/bridge/accounts/status")
async def accounts_status() -> dict:
    return {"ok": True, "headless": True, "profiles": gemini_pool.profile_cards()}


# -------------------------------------------------- one-time login window
class LoginStartReq(BaseModel):
    provider: str = "chrome"
    slot: int | None = None


@app.post("/accounts/login/start")
@app.post("/api/bridge/login/start")
async def login_start(req: LoginStartReq) -> JSONResponse:
    try:
        flow = await asyncio.to_thread(browser.spawn_login_flow, req.provider, req.slot)
        return JSONResponse(flow)
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "state": "failed", "reason": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "state": "failed", "reason": str(e)}, status_code=500)


@app.get("/accounts/login/status/{flow_id}")
@app.get("/api/bridge/login/flow/{flow_id}")
async def login_status(flow_id: str) -> JSONResponse:
    flow = browser.flow_status(flow_id)
    if not flow:
        return JSONResponse({"ok": False, "state": "unknown", "reason": "login session not found"}, status_code=404)
    if flow.get("state") == "authenticated":
        flow["profiles"] = gemini_pool.profile_cards()
    return JSONResponse(flow)


@app.post("/accounts/login/cancel/{flow_id}")
@app.post("/api/bridge/login/flow/{flow_id}")
async def login_cancel(flow_id: str) -> JSONResponse:
    flow = browser.cancel_flow(flow_id)
    if not flow:
        return JSONResponse({"ok": False, "reason": "login session not found"}, status_code=404)
    return JSONResponse(flow)


# -------------------------------------------------------------- generate SSE
class GenerateReq(BaseModel):
    styleName: str = "style"
    styleImage: str
    aspect: str = "4:5"
    background: str = "transparent"
    textOverride: str = ""
    nicheImages: list[dict] = []


@app.post("/generate/stream")
@app.post("/api/bridge/generate")
async def generate_stream(req: GenerateReq) -> StreamingResponse:
    payload = req.model_dump()
    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def push(ev: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    def runner() -> None:
        try:
            gemini_pool.run_batch(payload, push, OUT_DIR)
            push({"type": "done"})
        except Exception as e:
            push({"type": "fatal", "reason": str(e)})

    loop.run_in_executor(None, runner)

    async def sse():
        yield "retry: 2000\n\n"
        while True:
            ev = await queue.get()
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") in {"done", "fatal"}:
                break

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- process
class ProcessReq(BaseModel):
    image: str
    settings: dict = {}


@app.post("/process")
@app.post("/api/bridge/process")
async def process(req: ProcessReq) -> JSONResponse:
    try:
        raw = base64.b64decode(req.image.split(",", 1)[-1])
        png, report = await asyncio.to_thread(image_lab.process_design, raw, req.settings, OUT_DIR, "lab-design")
        return JSONResponse({"ok": True, "png": base64.b64encode(png).decode(), "report": report})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


# ===========================================================================
# Elite design production tools (+ /api/bridge/elite alias)
# ===========================================================================
class EliteReq(BaseModel):
    image: str
    name: str = "design"
    products: list[str] | None = None
    intensity: float = 0.45
    seed: int = 7
    mode: str = "spline"
    strategy: str = "auto"
    catalog: list[dict] = []
    threshold: int = 18


@app.get("/elite/capabilities")
@app.get("/api/bridge/elite/capabilities")
async def elite_caps() -> dict:
    return {"ok": True, "capabilities": elite_tools.CAPABILITIES, "products": elite_tools.PRODUCTS}


async def _elite_decode(req: EliteReq) -> bytes:
    return base64.b64decode(req.image.split(",", 1)[-1])


@app.post("/elite/polycanvas")
@app.post("/api/bridge/elite/polycanvas")
async def elite_polycanvas(req: EliteReq) -> JSONResponse:
    try:
        items = await asyncio.to_thread(elite_tools.adapt_to_products, await _elite_decode(req), OUT_DIR, req.name, req.products)
        for it in items:
            prev = it.pop("preview", b"")
            it["preview"] = base64.b64encode(prev).decode() if prev else ""
        return JSONResponse({"ok": True, "items": items})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@app.post("/elite/ocr")
@app.post("/api/bridge/elite/ocr")
async def elite_ocr(req: EliteReq) -> JSONResponse:
    try:
        res = await asyncio.to_thread(elite_tools.scan_graphic_ocr, await _elite_decode(req))
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "available": False, "reason": str(e)}, status_code=500)


@app.post("/elite/phash")
@app.post("/api/bridge/elite/phash")
async def elite_phash(req: EliteReq) -> JSONResponse:
    try:
        res = await asyncio.to_thread(elite_tools.check_duplicate, await _elite_decode(req), req.catalog, req.threshold)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@app.post("/elite/vectorize")
@app.post("/api/bridge/elite/vectorize")
async def elite_vectorize(req: EliteReq) -> JSONResponse:
    try:
        res = await asyncio.to_thread(elite_tools.vectorize_svg, await _elite_decode(req), OUT_DIR, req.name, req.mode)
        return JSONResponse(res, status_code=200 if res.get("ok") else 501)
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@app.post("/elite/distress")
@app.post("/api/bridge/elite/distress")
async def elite_distress(req: EliteReq) -> JSONResponse:
    try:
        png = await asyncio.to_thread(elite_tools.apply_distress_knockout, await _elite_decode(req), req.intensity, req.seed)
        return JSONResponse({"ok": True, "png": base64.b64encode(png).decode()})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@app.post("/elite/split")
@app.post("/api/bridge/elite/split")
async def elite_split(req: EliteReq) -> JSONResponse:
    try:
        res = await asyncio.to_thread(elite_tools.generate_dark_light_split, await _elite_decode(req), OUT_DIR, req.name, req.strategy)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


# ===========================================================================
# STATIC UI — mount LAST so /api/* always wins. SPA fallback for client routes.
# ===========================================================================
_FALLBACK_UI = """<!doctype html><html><head><meta charset="utf-8"><title>MerchFlow Pro</title>
<style>body{background:#07070c;color:#e8e8f0;font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.c{max-width:560px;text-align:center}.k{color:#c9f24b}code{background:#11111c;padding:2px 8px;border-radius:6px}</style></head>
<body><div class="c"><h1>MerchFlow Pro <span class="k">Engine Online</span></h1>
<p>The engine is running on <b>127.0.0.1:8000</b>, but the static UI bundle (<code>out/</code>) was not found.</p>
<p>Build it once with <code>DESKTOP_EXPORT=1 npx next build</code>, or open the cloud dashboard while developing.</p>
<p><a style="color:#b9a8ff" href="/api/health">/api/health</a> · <a style="color:#b9a8ff" href="/api/accounts/status">/api/accounts/status</a></p>
</div></body></html>"""


if STATIC_DIR is not None:
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")
else:
    @app.get("/{full_path:path}")
    async def _ui_missing(full_path: str) -> HTMLResponse:
        return HTMLResponse(_FALLBACK_UI)
