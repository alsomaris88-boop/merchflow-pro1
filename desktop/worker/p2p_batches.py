"""Disk-spooled Donepixel batches, independent of any browser/SSE connection.

Only provider calls run in parallel; libvips export is bounded to one graph at a
time on desktop. Successful designs and job state commit atomically to History.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Literal
import re
import shutil
import sqlite3
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import gemini_pool, image_lab, output_store

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PARALLEL = max(1, min(32, int(os.environ.get("MERCHFLOW_P2P_PARALLEL", "8"))))
_ACTIVE: dict[tuple[str, str], threading.Thread] = {}
_START_LOCK = threading.Lock()
_EXPORT_LOCK = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS p2p_batches (
 id TEXT PRIMARY KEY, background TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS p2p_jobs (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL,
 state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT '',
 design_id TEXT, ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS p2p_jobs_batch ON p2p_jobs(batch_id, position);
"""


@contextmanager
def connect(out_dir: Path):
    with output_store._connect(out_dir) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        yield conn


def _changed(conn, batch_id: str) -> None:
    output_store.publish_event(conn, {"type": "p2p.updated", "batchId": batch_id})


def _batch(conn, batch_id: str):
    row = conn.execute("SELECT * FROM p2p_batches WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Pixel batch not found")
    return row


def _input_dir(out_dir: Path, batch_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", batch_id):
        raise HTTPException(404, "Pixel batch not found")
    return out_dir / ".p2p-inputs" / batch_id


def snapshot(out_dir: Path, batch_id: str) -> dict:
    with connect(out_dir) as conn:
        batch = dict(_batch(conn, batch_id))
        rows = conn.execute(
            "SELECT j.*, d.data FROM p2p_jobs j LEFT JOIN designs d ON d.id=j.design_id "
            "WHERE j.batch_id=? ORDER BY j.position", (batch_id,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        data = item.pop("data")
        item["design"] = json.loads(data) if data else None
        items.append(item)
    return {**batch, "items": items, "total": len(items),
            "completed": sum(j["state"] == "succeeded" for j in items),
            "failed": sum(j["state"] == "failed" for j in items)}


def set_state(out_dir: Path, batch_id: str, job_id: str, state: str, *, reason: str = "", account: str = "") -> None:
    with connect(out_dir) as conn:
        conn.execute("UPDATE p2p_jobs SET state=?, reason=?, account=CASE WHEN ?='' THEN account ELSE ? END "
                     "WHERE id=? AND batch_id=? AND state!='succeeded'",
                     (state, reason[:800], account, account, job_id, batch_id))
        _changed(conn, batch_id)


def require_engine() -> None:
    if gemini_pool.MOCK:
        raise HTTPException(409, "Mock production is blocked. Connect a real Gemini Pro account.")
    if not image_lab.HAS_VIPS:
        raise HTTPException(409, "Donepixel requires native libvips streaming. Install desktop/requirements.txt (pyvips[binary]).")
    if gemini_pool.POOL.ready_pro_count() < 1:
        raise HTTPException(409, "No available authenticated Gemini Pro account. Connect one in Account Pool.")


class BatchItem(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    name: str = Field(min_length=1, max_length=512)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outputsPerInput: Literal[1] = 1
    background: Literal["#000000", "#FFFFFF"] = "#000000"
    items: list[BatchItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Each design must have a unique item ID")
        return self


def create(out_dir: Path, req: BatchRequest) -> dict:
    batch_id = uuid.uuid4().hex
    with connect(out_dir) as conn:
        conn.execute("INSERT INTO p2p_batches VALUES (?, ?, 'uploading', ?)",
                     (batch_id, req.background.upper(), round(time.time() * 1000)))
        conn.executemany("INSERT INTO p2p_jobs (id,batch_id,name,position,state) VALUES (?,?,?,?,'awaiting-upload')",
                         [(item.id, batch_id, item.name, i) for i, item in enumerate(req.items)])
        _changed(conn, batch_id)
    return snapshot(out_dir, batch_id)


def _export(raw: bytes, out_dir: Path, job: dict, background: str, account: str, prompt: str, started: float) -> dict:
    import pyvips

    # This graph stays lazy until pngsave writes scanlines to disk. No full-size
    # Pillow/NumPy canvas and no returned/base64 master PNG are allocated.
    with _EXPORT_LOCK:
        pyvips.cache_set_max(0)  # unique inputs must not accumulate in the op cache
        source = pyvips.Image.new_from_buffer(raw, "", access="sequential", fail_on="error").autorot()
        native_w, native_h = source.width, source.height
        if source.interpretation not in {"srgb", "rgb16"}:
            source = source.colourspace("srgb")
        if source.format not in {"uchar", "ushort"}:
            raise ValueError("Provider image must use 8-bit or 16-bit colour channels")
        maximum = 65535 if source.format == "ushort" else 255
        if not source.hasalpha():
            source = source.bandjoin(maximum)
        if source.bands != 4:
            raise ValueError("Provider render could not be represented as lossless RGBA")
        scale = min(4500 / source.width, 5400 / source.height)
        if scale != 1:
            source = source.premultiply(max_alpha=maximum).resize(scale, kernel="lanczos3").unpremultiply(max_alpha=maximum).cast(source.format)
        colour = [int(background[i:i + 2], 16) * (257 if maximum == 65535 else 1) for i in (1, 3, 5)] + [maximum]
        final = source.embed((4500 - source.width) // 2, (5400 - source.height) // 2,
                             4500, 5400, extend="background", background=colour)
        return output_store.save_vips_print(final, out_dir, job["name"], {
            "background": background, "account": account, "prompt": prompt,
            "batchId": job["batch_id"], "jobId": job["id"],
            "nativeW": native_w, "nativeH": native_h, "upscaled": scale > 1,
        }, ms=round((time.perf_counter() - started) * 1000))


def _run_item(out_dir: Path, batch: dict, job: dict) -> None:
    started = time.perf_counter()
    source_path = _input_dir(out_dir, batch["id"]) / job["id"]
    try:
        reference = source_path.read_bytes()
        raw, account, prompt = gemini_pool.render_donepixel(
            reference, batch["background"],
            lambda label: set_state(out_dir, batch["id"], job["id"], "rendering", account=label),
        )
        del reference
        set_state(out_dir, batch["id"], job["id"], "saving", account=account)
        _export(raw, out_dir, job, batch["background"], account, prompt, started)
    except Exception as exc:
        set_state(out_dir, batch["id"], job["id"], "failed", reason=str(exc))
    finally:
        source_path.unlink(missing_ok=True)


def _finish(out_dir: Path, batch_id: str) -> None:
    data = snapshot(out_dir, batch_id)
    status = "completed" if not data["failed"] else "partial" if data["completed"] else "failed"
    with connect(out_dir) as conn:
        conn.execute("UPDATE p2p_batches SET status=? WHERE id=?", (status, batch_id))
        _changed(conn, batch_id)
    shutil.rmtree(_input_dir(out_dir, batch_id), ignore_errors=True)


def _run(out_dir: Path, batch_id: str) -> None:
    try:
        batch = snapshot(out_dir, batch_id)
        jobs = [j for j in batch["items"] if j["state"] == "queued"]
        workers = min(MAX_PARALLEL, max(1, gemini_pool.POOL.ready_pro_count()), max(1, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="donepixel") as executor:
            # executor.map is deliberately consumed: unexpected worker failures
            # cannot leave a silently 'running' batch behind.
            for _ in executor.map(lambda job: _run_item(out_dir, batch, job), jobs):
                pass
    except Exception as exc:
        with connect(out_dir) as conn:
            conn.execute("UPDATE p2p_jobs SET state='failed',reason=? WHERE batch_id=? AND state NOT IN ('succeeded','failed')",
                         (str(exc)[:800], batch_id))
    finally:
        try:
            _finish(out_dir, batch_id)
        finally:
            with _START_LOCK:
                _ACTIVE.pop((str(out_dir.resolve()), batch_id), None)


def start(out_dir: Path, batch_id: str) -> dict:
    key = (str(out_dir.resolve()), batch_id)
    with _START_LOCK:
        with connect(out_dir) as conn:
            batch = _batch(conn, batch_id)
            if batch["status"] != "uploading":
                return snapshot(out_dir, batch_id)  # idempotent: never render twice
            if conn.execute("SELECT 1 FROM p2p_jobs WHERE batch_id=? AND state='uploading' LIMIT 1", (batch_id,)).fetchone():
                raise HTTPException(409, "Wait for in-progress image uploads before starting")
            conn.execute("UPDATE p2p_jobs SET state='failed',reason='Image upload did not complete' WHERE batch_id=? AND state='awaiting-upload'", (batch_id,))
            conn.execute("UPDATE p2p_jobs SET state='queued' WHERE batch_id=? AND state='uploaded'", (batch_id,))
            conn.execute("UPDATE p2p_batches SET status='running' WHERE id=?", (batch_id,))
            _changed(conn, batch_id)
        thread = threading.Thread(target=_run, args=(out_dir, batch_id), daemon=True, name=f"p2p-{batch_id[:8]}")
        _ACTIVE[key] = thread
        thread.start()
    return snapshot(out_dir, batch_id)


def recover(out_dir: Path) -> None:
    """Do not silently repeat paid/uncertain renders after a worker restart."""
    with connect(out_dir) as conn:
        batches = [row[0] for row in conn.execute("SELECT id FROM p2p_batches WHERE status IN ('uploading','running')")]
    for batch_id in batches:
        with _START_LOCK:
            if (str(out_dir.resolve()), batch_id) in _ACTIVE:
                continue
        with connect(out_dir) as conn:
            conn.execute("UPDATE p2p_jobs SET state='failed',reason='Worker restarted before completion; already archived designs are safe. Retry unfinished images.' "
                         "WHERE batch_id=? AND state NOT IN ('succeeded','failed')", (batch_id,))
        _finish(out_dir, batch_id)


def install_routes(app: FastAPI, get_out) -> None:
    root = "/api/bridge/p2p/batches"

    @app.post(root)
    async def create_batch(req: BatchRequest):
        require_engine()
        try:
            return {"ok": True, "batch": await asyncio.to_thread(create, get_out(), req)}
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "An item ID is already registered; create a fresh batch") from exc

    @app.get(root + "/{batch_id}")
    async def get_batch(batch_id: str):
        return {"ok": True, "batch": await asyncio.to_thread(snapshot, get_out(), batch_id)}

    @app.post(root + "/{batch_id}/items/{job_id}")
    async def upload_item(batch_id: str, job_id: str, request: Request):
        out_dir = get_out()
        with connect(out_dir) as conn:
            batch = _batch(conn, batch_id)
            job = conn.execute("SELECT state FROM p2p_jobs WHERE id=? AND batch_id=?", (job_id, batch_id)).fetchone()
            if job is None:
                raise HTTPException(404, "Batch item not found")
            if batch["status"] != "uploading":
                raise HTTPException(409, "Batch has already started")
            if job["state"] == "uploaded":
                return {"ok": True}  # safe retry after a lost response
            if job["state"] == "uploading":
                raise HTTPException(409, "This image upload is already in progress")
            conn.execute("UPDATE p2p_jobs SET state='uploading' WHERE id=?", (job_id,))
        folder = _input_dir(out_dir, batch_id)
        destination = folder / job_id
        temp = folder / f"{job_id}.{uuid.uuid4().hex}.part"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            length = 0
            with temp.open("xb") as stream:
                async for chunk in request.stream():
                    length += len(chunk)
                    if length > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Each image must be 40 MB or smaller")
                    stream.write(chunk)
            if not length:
                raise HTTPException(400, "Empty image upload")
            with Image.open(temp) as check:
                check.verify()
            os.replace(temp, destination)
            set_state(out_dir, batch_id, job_id, "uploaded")
            return {"ok": True}
        except asyncio.CancelledError:
            destination.unlink(missing_ok=True)
            set_state(out_dir, batch_id, job_id, "failed", reason="Image upload was interrupted")
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            set_state(out_dir, batch_id, job_id, "failed", reason=str(getattr(exc, "detail", exc)))
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(400, f"Invalid image upload: {exc}") from exc
        finally:
            temp.unlink(missing_ok=True)

    @app.post(root + "/{batch_id}/start")
    async def start_batch(batch_id: str):
        return {"ok": True, "batch": await asyncio.to_thread(start, get_out(), batch_id)}

    @app.post(root + "/{batch_id}/discard")
    async def discard_batch(batch_id: str):
        out_dir = get_out()
        with _START_LOCK, connect(out_dir) as conn:
            batch = _batch(conn, batch_id)
            if batch["status"] != "uploading":
                raise HTTPException(409, "Only an unfinished upload can be discarded; queued renders continue archiving")
            if conn.execute("SELECT 1 FROM p2p_jobs WHERE batch_id=? AND state='uploading' LIMIT 1", (batch_id,)).fetchone():
                raise HTTPException(409, "An upload is still in progress; wait for it to finish")
            conn.execute("UPDATE p2p_jobs SET state='failed',reason='Unfinished upload discarded' WHERE batch_id=?", (batch_id,))
        await asyncio.to_thread(_finish, out_dir, batch_id)
        return {"ok": True, "batch": await asyncio.to_thread(snapshot, out_dir, batch_id)}

    @app.get("/api/bridge/events")
    async def history_events(request: Request):
        out_dir = get_out()
        try:
            last = request.headers.get("last-event-id")
            cursor = int(last) if last is not None else await asyncio.to_thread(output_store.latest_event_id, out_dir)
        except ValueError as exc:
            raise HTTPException(400, "Invalid event cursor") from exc

        async def stream():
            nonlocal cursor
            yield f"retry: 1500\nid: {cursor}\ndata: {{\"type\":\"sync\"}}\n\n"
            heartbeat = time.monotonic()
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(output_store.read_events, out_dir, cursor)
                for event_id, event in rows:
                    cursor = event_id
                    yield f"id: {event_id}\ndata: {json.dumps(event)}\n\n"
                if time.monotonic() - heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.35)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
        })
