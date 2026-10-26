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
from contextlib import asynccontextmanager
import base64
import json
import os
import threading
import time
import uuid
from typing import Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import browser, db, elite_tools, gemini_cdp, gemini_pool, image_lab, output_store, p2p_batches, trademark_shield, trends_local

WORKER_VERSION = "2.5.4-prepare-3card-streaming"

OUT_DIR = Path(os.environ.get("MERCHFLOW_OUT") or (Path(__file__).resolve().parents[2] / "output")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)

_here = Path(__file__).resolve()
STATIC_CANDIDATES = [
    Path(os.environ.get("MERCHFLOW_UI_DIR") or ""),
    _here.parent.parent.parent / "out",           # tidy shipped bundle
    _here.parent.parent.parent / ".next-export",  # raw Next 16 Turbopack export
    _here.parent.parent / "out",                  # desktop/out (bundled)
]
STATIC_DIR = next((p for p in STATIC_CANDIDATES if p and (p / "index.html").exists()), None)

@asynccontextmanager
async def lifespan(_app):
    await asyncio.to_thread(p2p_batches.recover, OUT_DIR)
    yield
    # Never leave orphaned headless Chrome processes holding account profiles.
    await asyncio.to_thread(gemini_pool.shutdown_clients)


app = FastAPI(title="MerchFlow Local Engine", version=WORKER_VERSION, lifespan=lifespan)
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
        "engine": f"gemini-cdp-headless + {image_lab.ENGINE_NAME}",
        "libvips": image_lab.HAS_VIPS, "mock": gemini_pool.MOCK, "headless": True,
        "outDir": str(OUT_DIR), "profiles": gemini_pool.profile_cards(),
        "capabilities": {**elite_tools.CAPABILITIES, "rembg": image_lab.HAS_REMBG, "opencv": image_lab.HAS_CV, "streamZip": True, "diskHistory": True, "historyEvents": True, "donepixelBatch": image_lab.HAS_VIPS, "donepixelRetry": image_lab.HAS_VIPS, "silentClipboard": True, "originalImagesOnly": True, "p2pParallel": p2p_batches.MAX_PARALLEL,
                        "geminiSlotsPerAccount": gemini_pool.SLOTS_PER_ACCOUNT},
    }


@app.get("/api/bridge")
async def bridge_probe() -> dict:
    return await health()


# ------------------------------------------------------------- accounts
@app.get("/api/accounts")
async def api_accounts() -> dict:
    rows = await asyncio.to_thread(db.list_accounts)
    live = {row["id"]: row for row in gemini_pool.profile_cards()}
    return {"source": "sqlite", "items": [{**row, **live.get(row["id"], {})} for row in rows]}


@app.post("/api/accounts")
@app.patch("/api/accounts")
async def reject_manual_account_creation() -> None:
    raise HTTPException(405, "Accounts can only be connected through the real Google/Gemini browser login")


@app.delete("/api/accounts")
@app.delete("/api/bridge/accounts")
async def api_accounts_delete(id: int) -> dict:
    try:
        if not await asyncio.to_thread(gemini_pool.POOL.remove, id):
            raise HTTPException(404, "Account not found")
        return {"ok": True, "profileDeleted": True}
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.delete("/api/accounts/{account_id}")
@app.delete("/api/bridge/accounts/{account_id}")
async def api_account_path_delete(account_id: int) -> dict:
    return await api_accounts_delete(account_id)


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
@app.get("/api/bridge/listings")
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
    designId: str = Field(default="", max_length=100)


@app.post("/api/listings")
@app.post("/api/bridge/listings")
async def api_listings_post(req: ListingReq) -> JSONResponse:
    try:
        payload = req.model_dump()
        if req.designId:
            design = await asyncio.to_thread(output_store.get_design, OUT_DIR, req.designId)
            payload["designPath"] = design["path"]
        return JSONResponse(await asyncio.to_thread(db.add_listing, payload))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        return JSONResponse({"error": "listing insert failed", "detail": str(e)}, status_code=500)


# ===========================================================================
# Engine endpoints (identical to before) + /api/bridge aliases
# ===========================================================================
@app.post("/accounts/cookies")
@app.post("/api/bridge/accounts")
async def reject_manual_cookies() -> None:
    raise HTTPException(405, "Pasted credentials cannot create an account. Use Add Gemini Account and complete the real browser login")


@app.get("/accounts/status")
@app.get("/api/bridge/accounts")       # Next bridge GET returns account cards
@app.get("/api/bridge/accounts/status")
async def accounts_status() -> dict:
    return {"ok": True, "headless": True, "profiles": gemini_pool.profile_cards()}


# -------------------------------------------------- one-time login window
class LoginStartReq(BaseModel):
    provider: Literal["chrome", "brave", "edge"] = "chrome"
    slot: int | None = Field(default=None, ge=1)


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
    flow = await asyncio.to_thread(browser.cancel_flow, flow_id)
    if not flow:
        return JSONResponse({"ok": False, "reason": "login session not found"}, status_code=404)
    return JSONResponse(flow)


# -------------------------------------------------------------- generate SSE
class ReferenceImage(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="design", max_length=160)
    image: str


class GenerateReq(BaseModel):
    styleName: str = "style"
    styleImage: str = ""
    aspect: Literal["4:5", "1:1", "3:4"] = "4:5"
    background: Literal["transparent", "black", "white", "original"] = "transparent"
    textOverride: str = Field(default="", max_length=1000)
    model: Literal["u2net", "birefnet-general"] = "u2net"
    instructions: str = Field(default="", max_length=4000)
    nicheImages: list[ReferenceImage] = Field(min_length=1, max_length=1000)


def _decode(data: str) -> bytes:
    import binascii
    try:
        raw = base64.b64decode(data.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, "Invalid base64 image") from exc
    if not raw or len(raw) > 40 * 1024 * 1024:
        raise HTTPException(413, "Each image must be between 1 byte and 40 MB")
    return raw


def _generate_response(req: GenerateReq, task: str) -> StreamingResponse:
    if gemini_pool.MOCK:
        raise HTTPException(409, "Mock generation is disabled. Unset MERCHFLOW_MOCK_ENGINE and connect a real account.")
    if not any(gemini_pool.client_for(a.id) is not None for a in gemini_pool.POOL.accounts):
        raise HTTPException(409, "No authenticated image-provider account. Connect one in Account Pool.")
    if task == "fusion" and not req.styleImage:
        raise HTTPException(400, "A style reference is required")
    if task == "fusion" and req.background == "original":
        raise HTTPException(400, "Keep Original is available for Pixel to Pixel recreations")
    payload = {**req.model_dump(), "task": task}
    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def push(ev: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    def runner() -> None:
        try:
            summary = gemini_pool.run_batch(payload, push, OUT_DIR)
            push({"type": "done", **summary})
        except Exception as exc:
            push({"type": "fatal", "reason": str(exc)})

    loop.run_in_executor(None, runner)

    async def sse():
        yield "retry: 2000\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in {"done", "fatal"}:
                break

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@app.post("/generate/stream")
@app.post("/api/bridge/generate")
async def generate_stream(req: GenerateReq) -> StreamingResponse:
    return _generate_response(req, "fusion")


@app.post("/p2p")
@app.post("/api/bridge/pixel-to-pixel")
async def recreate_stream(req: GenerateReq) -> StreamingResponse:
    return _generate_response(req, "recreate")


class ProcessReq(BaseModel):
    image: str
    name: str = Field(default="lab-design", max_length=160)
    settings: dict = Field(default_factory=dict)


@app.post("/process")
@app.post("/api/bridge/process")
async def process(req: ProcessReq) -> JSONResponse:
    try:
        raw = _decode(req.image)
        _, report = await asyncio.to_thread(
            image_lab.process_design, raw, req.settings, OUT_DIR, req.name, return_bytes=False,
        )
        return JSONResponse({"ok": True, "design": report["design"], "report": report})
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "reason": str(exc)}, status_code=500)


# ------------------------------- genuine file/history API ------------------
class SaveReq(BaseModel):
    image: str
    name: str = Field(default="design", max_length=160)
    meta: dict = Field(default_factory=dict)


@app.get("/api/bridge/designs")
async def designs_list() -> dict:
    return {"ok": True, "items": await asyncio.to_thread(output_store.list_designs, OUT_DIR)}


@app.post("/api/bridge/designs")
async def designs_save(req: SaveReq) -> JSONResponse:
    try:
        _, design = await asyncio.to_thread(output_store.save_raw, _decode(req.image), OUT_DIR, req.name, req.meta)
        return JSONResponse({"ok": True, "design": design})
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "reason": str(exc)}, status_code=500)


class DeleteDesignsReq(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all: bool = False


@app.delete("/api/bridge/designs")
async def designs_delete(req: DeleteDesignsReq) -> dict:
    await asyncio.to_thread(output_store.delete_records, OUT_DIR, None if req.all else req.ids)
    return {"ok": True}


@app.get("/api/bridge/designs/{design_id}")
async def design_file(design_id: str, preview: bool = False) -> FileResponse:
    try:
        record = await asyncio.to_thread(output_store.get_design, OUT_DIR, design_id)
        path = output_store.file_path(OUT_DIR, record, preview)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="image/png", filename=f"{output_store.safe_name(record['name'])}_{record['w']}x{record['h']}_300dpi.png", content_disposition_type="inline", headers={"Cache-Control": "no-store"})


# A short-lived ticket keeps arbitrarily large ID lists OUT of download URLs.
_ZIP_TICKETS: dict[str, tuple[float, list[str]]] = {}
_ZIP_LOCK = threading.Lock()


class ExportReq(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5000)


@app.post("/api/bridge/export")
async def export_ticket(req: ExportReq) -> dict:
    ids = list(dict.fromkeys(req.ids))
    try:
        # Preflight every file before promising a ZIP to the browser.
        await asyncio.to_thread(lambda: [output_store.get_design(OUT_DIR, i) for i in ids])
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc
    with _ZIP_LOCK:
        now = time.monotonic()
        for key in [key for key, (expiry, _) in _ZIP_TICKETS.items() if expiry < now]:
            del _ZIP_TICKETS[key]
        if len(_ZIP_TICKETS) >= 1000:
            raise HTTPException(429, "Too many pending ZIP downloads")
        ticket = uuid.uuid4().hex
        _ZIP_TICKETS[ticket] = (now + 600, ids)
    return {"ok": True, "url": f"/api/bridge/export?ticket={ticket}", "count": len(ids)}


@app.get("/api/bridge/export")
async def export_zip(ticket: str) -> StreamingResponse:
    with _ZIP_LOCK:
        entry = _ZIP_TICKETS.get(ticket)
    if not entry or entry[0] < time.monotonic():
        raise HTTPException(404, "Download expired; click Download all again")
    try:
        records = await asyncio.to_thread(lambda: [output_store.get_design(OUT_DIR, i) for i in entry[1]])
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return StreamingResponse(output_store.stream_zip(OUT_DIR, records), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="merchflow-designs_{len(records)}.zip"',
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })


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
    return {"ok": True, "capabilities": {**elite_tools.CAPABILITIES, "rembg": image_lab.HAS_REMBG, "opencv": image_lab.HAS_CV, "streamZip": True, "diskHistory": True, "historyEvents": True, "donepixelBatch": image_lab.HAS_VIPS, "donepixelRetry": image_lab.HAS_VIPS, "silentClipboard": True, "originalImagesOnly": True, "p2pParallel": p2p_batches.MAX_PARALLEL,
                                                        "geminiSlotsPerAccount": gemini_pool.SLOTS_PER_ACCOUNT}, "products": elite_tools.PRODUCTS}


async def _elite_decode(req: EliteReq) -> bytes:
    return _decode(req.image)


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
        png, design = await asyncio.to_thread(output_store.save_raw, png, OUT_DIR, f"{req.name}-distress", {"source": "prepare", "style": "Distress knockout"})
        return JSONResponse({"ok": True, "png": base64.b64encode(png).decode(), "design": design})
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


p2p_batches.install_routes(app, lambda: OUT_DIR)


# ===========================================================================
# STATIC UI — mount LAST so /api/* always wins. SPA fallback for client routes.
# ===========================================================================
_FALLBACK_UI = """<!doctype html><html><head><meta charset="utf-8"><title>MerchFlow Pro</title>
<style>body{background:#07070c;color:#e8e8f0;font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.c{max-width:560px;text-align:center}.k{color:#c9f24b}code{background:#11111c;padding:2px 8px;border-radius:6px}</style></head>
<body><div class="c"><h1>MerchFlow Pro <span class="k">Engine Online</span></h1>
<p>The engine is running on <b>127.0.0.1:8000</b>, but the static UI bundle (<code>out/</code>) was not found.</p>
<p>Build it once with <code>npm run build:desktop</code>, or open the cloud dashboard while developing.</p>
<p><a style="color:#b9a8ff" href="/api/health">/api/health</a> · <a style="color:#b9a8ff" href="/api/accounts/status">/api/accounts/status</a></p>
</div></body></html>"""


if STATIC_DIR is not None:
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")
else:
    @app.get("/{full_path:path}")
    async def _ui_missing(full_path: str) -> HTMLResponse:
        return HTMLResponse(_FALLBACK_UI)
