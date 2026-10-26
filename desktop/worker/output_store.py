"""Verified PNG output, durable history, and bounded-memory ZIP streaming.

The history lives beside the actual files, not only in a browser cache. Never
accept a client-supplied filesystem path: downloads are resolved by record ID.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from PIL import Image, ImageOps, PngImagePlugin

DPI = 300
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_DB_LOCK = threading.RLock()


@contextmanager
def _connect(out_dir: Path):
    # sqlite's busy timeout does not reliably serialize the initial WAL/schema
    # transition. History polling and concurrent saves may race on a fresh DB.
    # Only the tiny SQL transaction is serialized, never PNG encoding/ZIP I/O.
    with _DB_LOCK:
        out_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(out_dir / ".history.sqlite3", timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS designs (id TEXT PRIMARY KEY, created REAL NOT NULL, data TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS design_events (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def safe_name(name: str) -> str:
    return re.sub(r"[^\w-]+", "-", name, flags=re.UNICODE).strip("-_")[:70] or "design"


def save_image(
    image: Image.Image, out_dir: Path, name: str, meta: dict | None = None,
    *, ms: int = 0, compress: bool = True,
) -> tuple[bytes, dict]:
    """Atomically write, verify, then register a PNG. Failure is never success.

    Both the HTTP response and the on-disk artifact use the SAME encoded bytes.
    PNG pHYs stores 300 DPI (rounded to the nearest pixel per metre).
    """
    save_started = time.perf_counter()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    design_id = uuid.uuid4().hex
    path = out_dir / f"{safe_name(name)}_{image.width}x{image.height}_{design_id}.png"
    temp = path.with_suffix(".png.part")
    thumb = out_dir / ".previews" / f"{design_id}.png"
    metadata = meta or {}
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add(b"sRGB", b"\x00")
    try:
        with temp.open("xb") as fp:
            image.save(fp, "PNG", dpi=(DPI, DPI), pnginfo=pnginfo, compress_level=9 if compress else 1)
            fp.flush()
            os.fsync(fp.fileno())
        with Image.open(temp) as check:
            if check.format != "PNG" or check.size != image.size:
                raise OSError("PNG dimensions/format verification failed")
            if any(abs(d - DPI) > 0.1 for d in check.info.get("dpi", (0, 0))):
                raise OSError("PNG DPI verification failed")
            check.verify()
        os.replace(temp, path)
        png = path.read_bytes()
        if not png.startswith(PNG_SIGNATURE) or path.stat().st_size != len(png):
            raise OSError("PNG disk write verification failed")
        # A small actual preview prevents decoding fifty 8K masters in History.
        thumb.parent.mkdir(exist_ok=True)
        scale = min(1.0, 384 / max(image.size))
        image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS).save(thumb, "PNG")
        record = {
            "id": design_id, "name": name[:160],
            "niche": str(metadata.get("niche", name))[:160],
            "style": str(metadata.get("style", "Prepared print"))[:160],
            "source": str(metadata.get("source", "prepare")),
            "aspect": str(metadata.get("aspect", f"{image.width}:{image.height}")),
            "background": str(metadata.get("background", "transparent")),
            "prompt": str(metadata.get("prompt", ""))[:12000],
            "account": str(metadata.get("account", "local"))[:80],
            "w": image.width, "h": image.height, "dpi": DPI, "bytes": len(png),
            "ms": ms + round((time.perf_counter() - save_started) * 1000), "saved": True, "path": str(path), "createdAt": round(time.time() * 1000),
            "fileUrl": f"/api/bridge/designs/{design_id}",
            "previewUrl": f"/api/bridge/designs/{design_id}?preview=1",
        }
        with _connect(out_dir) as conn:
            conn.execute("INSERT INTO designs VALUES (?, ?, ?)", (design_id, record["createdAt"], json.dumps(record)))
            publish_event(conn, {"type": "design.saved", "design": record})
        return png, record
    except Exception:
        temp.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)
        raise


def save_vips_image(image, out_dir: Path, name: str, metadata: dict | None = None, *, ms: int = 0, compress: bool = True) -> dict:
    """Stream a prepared libvips graph directly to a verified History PNG.

    Unlike :func:`save_image`, this path does not materialize the full encoded
    master in Python memory. It is used by the desktop Prepare endpoint, which
    returns only the saved-design record. Resampling and PNG encoding stay in
    libvips; the browser can still fetch the exact disk-backed original later.
    """
    import pyvips

    metadata = metadata or {}
    if image.bands != 4 or image.format != "uchar":
        raise ValueError("Prepared print output must be 8-bit RGBA")
    if image.width < 1 or image.height < 1:
        raise ValueError("Prepared print output has invalid dimensions")

    save_started = time.perf_counter()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    design_id = uuid.uuid4().hex
    path = out_dir / f"{safe_name(name)}_{image.width}x{image.height}_{design_id}.png"
    temp = path.with_suffix(".png.part")
    thumb = out_dir / ".previews" / f"{design_id}.png"
    thumb_temp = thumb.with_suffix(".png.part")
    try:
        image = image.copy(xres=DPI / 25.4, yres=DPI / 25.4)
        image.pngsave(str(temp), compression=6 if compress else 1, interlace=False,
                      palette=False, bitdepth=8, filter=248)
        with temp.open("r+b") as fp:
            os.fsync(fp.fileno())
        with Image.open(temp) as check:
            if check.format != "PNG" or check.size != (image.width, image.height) or check.mode != "RGBA":
                raise OSError("Prepared PNG dimensions, format, or RGBA mode verification failed")
            if any(abs(d - DPI) > 0.1 for d in check.info.get("dpi", (0, 0))):
                raise OSError("Prepared PNG DPI verification failed")
            check.verify()
        thumb.parent.mkdir(exist_ok=True)
        preview = pyvips.Image.thumbnail(str(temp), 384, height=384, size="down")
        preview.pngsave(str(thumb_temp), compression=6, palette=False)
        del preview
        with Image.open(thumb_temp) as check:
            check.verify()
        os.replace(temp, path)
        os.replace(thumb_temp, thumb)
        record = {
            "id": design_id, "name": str(name)[:160],
            "niche": str(metadata.get("niche", name))[:160],
            "style": str(metadata.get("style", "Prepared print"))[:160],
            "source": str(metadata.get("source", "prepare")),
            "aspect": str(metadata.get("aspect", f"{image.width}:{image.height}")),
            "background": str(metadata.get("background", "transparent")),
            "prompt": str(metadata.get("prompt", ""))[:12000],
            "account": str(metadata.get("account", "local"))[:80],
            "w": image.width, "h": image.height, "dpi": DPI,
            "bytes": path.stat().st_size,
            "ms": ms + round((time.perf_counter() - save_started) * 1000),
            "saved": True, "path": str(path), "previewPath": str(thumb),
            "createdAt": round(time.time() * 1000),
            "fileUrl": f"/api/bridge/designs/{design_id}",
            "previewUrl": f"/api/bridge/designs/{design_id}?preview=1",
            "lossless": True, "mode": "RGBA", "encoder": "libvips-png",
            **{key: metadata[key] for key in (
                "upscaled", "upscaleFactor", "cutoutMode", "detectedBg", "resizeForPrint",
            ) if key in metadata},
        }
        with _connect(out_dir) as conn:
            conn.execute("INSERT INTO designs VALUES (?, ?, ?)",
                         (design_id, record["createdAt"], json.dumps(record)))
            publish_event(conn, {"type": "design.saved", "design": record})
        return record
    except Exception:
        for file in (temp, path, thumb_temp, thumb):
            file.unlink(missing_ok=True)
        raise


def save_raw(raw: bytes, out_dir: Path, name: str, meta: dict | None = None) -> tuple[bytes, dict]:
    with Image.open(io.BytesIO(raw)) as image:
        image.load()  # truncated or invalid uploads must fail before registration
        return save_image(ImageOps.exif_transpose(image).convert("RGBA"), out_dir, name, meta)


def list_designs(out_dir: Path) -> list[dict]:
    with _connect(out_dir) as conn:
        rows = conn.execute("SELECT data FROM designs ORDER BY created DESC").fetchall()
    return [record for (data,) in rows if (record := json.loads(data)) and file_path(out_dir, record).is_file()]


def get_design(out_dir: Path, design_id: str) -> dict:
    with _connect(out_dir) as conn:
        row = conn.execute("SELECT data FROM designs WHERE id = ?", (design_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"Design {design_id} is not in history")
    record = json.loads(row[0])
    if not file_path(out_dir, record).is_file():
        raise FileNotFoundError(f"The PNG for {design_id} is missing from disk")
    return record


def file_path(out_dir: Path, record: dict, preview: bool = False) -> Path:
    path = Path(record["path"]).resolve()
    root = out_dir.resolve()
    if path.parent != root or path.suffix.lower() != ".png":
        raise ValueError("Output path is outside the configured output directory")
    if preview:
        small = root / ".previews" / f"{record['id']}.png"
        if small.is_file():
            return small
    return path


def delete_records(out_dir: Path, ids: list[str] | None = None) -> None:
    """Clear history entries only; user PNGs on disk are deliberately untouched."""
    with _connect(out_dir) as conn:
        if ids is None:
            conn.execute("DELETE FROM designs")
        else:
            conn.executemany("DELETE FROM designs WHERE id = ?", [(i,) for i in ids])
        publish_event(conn, {"type": "designs.changed"})


class _ZipSink:
    """Non-seekable zipfile destination; drained after EACH input file chunk."""
    def __init__(self) -> None:
        self.chunks: deque[bytes] = deque()
        self.offset = 0

    def write(self, data: bytes) -> int:
        if data:
            self.chunks.append(bytes(data))
            self.offset += len(data)
        return len(data)

    def tell(self) -> int:
        return self.offset

    def flush(self) -> None:
        pass

    def drain(self) -> Iterator[bytes]:
        while self.chunks:
            yield self.chunks.popleft()


def stream_zip(out_dir: Path, records: Iterable[dict]) -> Iterator[bytes]:
    """ZIP64 + data descriptors: no seek, no full archive buffer, no recompression.

    The caller resolves/validates every ID BEFORE returning response headers.
    PNGs are already compressed. STORE avoids wasted CPU and memory.
    """
    sink = _ZipSink()
    used: set[str] = set()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for record in records:
            base = f"{safe_name(record['name'])}_{record['w']}x{record['h']}_{DPI}dpi"
            filename, index = f"{base}.png", 2
            while filename.casefold() in used:
                filename = f"{base}_{index}.png"
                index += 1
            used.add(filename.casefold())
            with file_path(out_dir, record).open("rb") as source, archive.open(filename, "w", force_zip64=True) as entry:
                while chunk := source.read(256 * 1024):
                    entry.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()


# The event outbox is committed in the same SQLite transaction as History.
# SSE consumers can reconnect without depending on a mounted Pixel component.
def publish_event(conn: sqlite3.Connection, event: dict) -> None:
    conn.execute("INSERT INTO design_events (data) VALUES (?)", (json.dumps(event),))


def latest_event_id(out_dir: Path) -> int:
    with _connect(out_dir) as conn:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM design_events").fetchone()[0]


def read_events(out_dir: Path, after: int) -> list[tuple[int, dict]]:
    with _connect(out_dir) as conn:
        rows = conn.execute("SELECT id, data FROM design_events WHERE id > ? ORDER BY id LIMIT 200", (after,)).fetchall()
    return [(event_id, json.loads(data)) for event_id, data in rows]


P2P_MAX_BYTES = 25_000_000


def save_vips_print(image, out_dir: Path, name: str, metadata: dict, *, ms: int = 0) -> dict:
    """Stream an RGBA libvips graph straight to an atomic lossless print PNG.

    No palette, quantization, full-image memory buffer, or 4 MB rejection.
    The 25 MB ceiling is checked before registration; oversize files never
    degrade silently. The PNG and small preview must both verify successfully.
    """
    import pyvips
    from datetime import datetime, timezone

    save_started = time.perf_counter()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    design_id = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = out_dir / f"p2p_{safe_name(Path(name).stem)}_{stamp}_{design_id[:8]}.png"
    temp = path.with_suffix(".png.part")
    thumb = out_dir / ".previews" / f"{design_id}.png"
    thumb_temp = thumb.with_suffix(".png.part")
    try:
        image = image.copy(xres=DPI / 25.4, yres=DPI / 25.4)
        image.pngsave(str(temp), compression=9, interlace=False, palette=False,
                      bitdepth=16 if image.format == "ushort" else 8, filter=248)
        size = temp.stat().st_size
        if size >= P2P_MAX_BYTES:
            raise ValueError("Lossless PNG exceeds the 25 MB limit; no colours were reduced and nothing was archived")
        with temp.open("r+b") as fp:
            os.fsync(fp.fileno())
        with Image.open(temp) as check:
            if check.format != "PNG" or check.size != (4500, 5400) or check.mode != "RGBA":
                raise OSError("Print PNG must be genuine 4500 x 5400 RGBA")
            if any(abs(d - DPI) > 0.1 for d in check.info.get("dpi", (0, 0))):
                raise OSError("Print PNG DPI verification failed")
            check.verify()
        thumb.parent.mkdir(exist_ok=True)
        # Decode a separate sequential source, never re-read a consumed graph.
        small = pyvips.Image.thumbnail(str(temp), 384, height=384, size="down")
        small.pngsave(str(thumb_temp), compression=6, palette=False)
        del small  # release the source file before Windows atomic renames
        with Image.open(thumb_temp) as check:
            check.verify()
        os.replace(temp, path)
        os.replace(thumb_temp, thumb)
        record = {
            "id": design_id, "name": Path(name).stem[:160], "niche": Path(name).stem[:160],
            "source": "pixel-to-pixel", "style": "Donepixel recreation", "aspect": "5:6",
            "background": metadata["background"], "prompt": metadata["prompt"],
            "account": metadata["account"], "w": 4500, "h": 5400, "dpi": DPI,
            "bytes": size, "ms": ms + round((time.perf_counter() - save_started) * 1000), "saved": True, "path": str(path),
            "previewPath": str(thumb), "createdAt": round(time.time() * 1000),
            "fileUrl": f"/api/bridge/designs/{design_id}",
            "previewUrl": f"/api/bridge/designs/{design_id}?preview=1",
            "lossless": True, "mode": "RGBA", "encoder": "libvips-png",
            **{key: metadata[key] for key in ("batchId", "jobId", "nativeW", "nativeH", "upscaled") if key in metadata},
        }
        with _connect(out_dir) as conn:
            conn.execute("INSERT INTO designs VALUES (?, ?, ?)", (design_id, record["createdAt"], json.dumps(record)))
            if metadata.get("batchId") and metadata.get("jobId"):
                updated = conn.execute(
                    "UPDATE p2p_jobs SET state='succeeded', design_id=?, ms=? WHERE id=? AND batch_id=? AND state='saving'",
                    (design_id, ms, metadata["jobId"], metadata["batchId"]),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("Batch job state changed before archive; transaction rolled back")
                publish_event(conn, {"type": "p2p.updated", "batchId": metadata["batchId"]})
            publish_event(conn, {"type": "design.saved", "design": record})
        return record
    except Exception:
        for file in (temp, path, thumb_temp, thumb):
            file.unlink(missing_ok=True)
        raise
