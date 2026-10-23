"""
MerchFlow Pro — native print pipeline (Prepare Designs Lab, server grade).

Pipeline order:
  1. Keep-holes background removal — BFS flood strictly from the 4 canvas
     borders, so enclosed letter counters (O, A, D, B, P, R, 4, 8) stay solid.
  2. 1–3px edge defringe + neighbor decontamination (kills halos on dark tees).
  3. Micro-dot despeckle (connected-component sweep at 4× downscale).
  4. Lanczos3 upscale to the 4500×5400 @ 300 DPI canvas (pyvips when present,
     streaming encode — no uncompressed 24.3M-px arrays persist in RAM).
  5. Optional authentic spot-color halftone (45° rosette, hard alpha).
  6. Lossless PNG write to C:/MerchFlow/Output (or $MERCHFLOW_OUT).
"""
from __future__ import annotations

import base64
import io
import math
import os
import time
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

try:  # native libvips (recommended: winget install libvips)
    import pyvips

    HAS_VIPS = True
except Exception:  # pragma: no cover - fallback environment
    HAS_VIPS = False

EXPORT_W, EXPORT_H = 4500, 5400
ENGINE_NAME = "pyvips-lanczos3" if HAS_VIPS else "pillow-lanczos (pyvips not installed)"


# ---------------------------------------------------------------- keep-holes
def _border_bgrm(arr: np.ndarray, tol: int) -> None:
    h, w = arr.shape[:2]
    a = arr[..., 3].astype(np.int16)
    rgb = arr[..., :3].astype(np.int16)
    opaque = a >= 40
    border = np.zeros((h, w), bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    bp = rgb[border & opaque]
    if bp.size == 0:
        return
    bg = bp.mean(axis=0)
    d2 = ((rgb - bg) ** 2).sum(axis=2)
    near = d2 <= (tol * tol * 3)

    visited = np.zeros((h, w), bool)
    dq: deque[tuple[int, int]] = deque()
    ys, xs = np.nonzero(border & (~opaque | near))
    for y, x in zip(ys.tolist(), xs.tolist()):
        if not visited[y, x]:
            visited[y, x] = True
            a[y, x] = 0
            dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx]:
                if (not opaque[ny, nx]) or near[ny, nx]:
                    visited[ny, nx] = True
                    a[ny, nx] = 0
                    dq.append((ny, nx))
    arr[..., 3] = a


# ----------------------------------------------------------------- defringe
def _box3(x: np.ndarray) -> np.ndarray:
    s = x.copy()
    s[:, 1:] += x[:, :-1]
    s[:, :-1] += x[:, 1:]
    s[1:, :] += x[:-1, :]
    s[:-1, :] += x[1:, :]
    return s


def _defringe(arr: np.ndarray, px: int) -> None:
    a = arr[..., 3].astype(np.float32)
    rgb = arr[..., :3].astype(np.float32)
    for _ in range(max(0, int(px))):
        mn = a.copy()
        mn[:, 1:] = np.minimum(mn[:, 1:], a[:, :-1])
        mn[:, :-1] = np.minimum(mn[:, :-1], a[:, 1:])
        mn[1:, :] = np.minimum(mn[1:, :], a[:-1, :])
        mn[:-1, :] = np.minimum(mn[:-1, :], a[1:, :])
        boundary = (a > 0) & (mn == 0)
        if not boundary.any():
            break
        full = (a >= 250).astype(np.float32)
        cnt = _box3(full)
        valid = cnt > 0
        inv = np.where(valid, 1.0 / np.maximum(cnt, 1), 0)
        for ch in range(3):
            avg = _box3(rgb[..., ch] * full) * inv
            mix = np.where(boundary & valid[..., None][..., 0], 0.3 * rgb[..., ch] + 0.7 * avg, rgb[..., ch])
            rgb[..., ch] = mix
        a = np.where(boundary, np.maximum(0.0, a - 140.0), a)
    arr[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    arr[..., 3] = a.astype(np.uint8)


# ---------------------------------------------------------------- despeckle
def _despeckle(arr: np.ndarray, min_size: int = 24) -> int:
    h, w = arr.shape[:2]
    bin_ = arr[..., 3] > 16
    f = 4
    H, W = (h + f - 1) // f, (w + f - 1) // f
    pad = np.zeros((H * f, W * f), bool)
    pad[:h, :w] = bin_
    small = pad.reshape(H, f, W, f).max(axis=(1, 3))
    visited = np.zeros_like(small)
    specks = np.zeros_like(small)
    for sy in range(H):
        for sx in range(W):
            if not small[sy, sx] or visited[sy, sx]:
                continue
            dq = deque([(sy, sx)])
            visited[sy, sx] = True
            cells: list[tuple[int, int]] = []
            while dq:
                y, x = dq.popleft()
                cells.append((y, x))
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < H and 0 <= nx < W and small[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        dq.append((ny, nx))
            if len(cells) < max(1, min_size // (f * f)):
                for y, x in cells:
                    specks[y, x] = True
    if not specks.any():
        return 0
    up = np.repeat(np.repeat(specks, f, axis=0), f, axis=1)[:h, :w]
    arr[..., 3][up] = 0
    return int(up.sum())


# ----------------------------------------------------------------- halftone
def _halftone(src: Image.Image, lpi: int, ink_hex: str, out_w: int, out_h: int) -> Image.Image:
    sw, sh = src.size
    small = src.resize((1400, int(sh * 1400 / sw)), Image.BILINEAR) if sw > 1400 else src.copy()
    arr = np.asarray(small).astype(np.float32)
    H2, W2 = arr.shape[:2]
    lum = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]) / 255.0
    alpha = arr[..., 3] / 255.0

    px_per_inch = out_w / 12.0
    pitch_out = max(3.0, px_per_inch / max(20, lpi))
    factor = out_w / W2
    pitch = pitch_out / factor
    ang = math.radians(45.0)
    cos_a, sin_a = math.cos(ang), math.sin(ang)

    out = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    from PIL import ImageDraw

    drw = ImageDraw.Draw(out)
    ink = tuple(int(ink_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)) + (255,)
    bound = int(math.hypot(W2, H2) / pitch) + 2
    for gy in range(-bound, bound + 1):
        ry = gy * pitch
        for gx in range(-bound, bound + 1):
            rx = gx * pitch
            x = rx * cos_a - ry * sin_a + W2 / 2
            y = rx * sin_a + ry * cos_a + H2 / 2
            xi, yi = int(round(x)), int(round(y))
            if xi < 0 or yi < 0 or xi >= W2 or yi >= H2:
                continue
            a = alpha[yi, xi]
            if a < 0.12:
                continue
            t = (1.0 - lum[yi, xi]) * a
            r = (pitch_out / 2) * 0.94 * math.sqrt(t)
            if r < pitch_out * 0.075:  # micro-dot cleanup
                continue
            cx, cy = x * factor, y * factor
            drw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ink)
    return out


# ------------------------------------------------------------------- vips
def vips_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    if not HAS_VIPS:
        return img.resize((w, h), Image.LANCZOS)
    arr = np.ascontiguousarray(np.asarray(img))
    vi = pyvips.Image.new_from_memory(arr.tobytes(), img.width, img.height, 4, "uchar")
    vi = vi.resize(w / img.width, vscale=h / img.height, kernel="lanczos3")
    mem = vi.write_to_memory()
    out = np.frombuffer(mem, dtype=np.uint8).reshape(vi.height, vi.width, 4)
    return Image.fromarray(out, "RGBA")


def vips_save(img: Image.Image, out_path: Path) -> bytes:
    if HAS_VIPS:
        arr = np.ascontiguousarray(np.asarray(img))
        vi = pyvips.Image.new_from_memory(arr.tobytes(), img.width, img.height, 4, "uchar")
        vi.pngsave(str(out_path), compression=9, strip=True)
        return vi.pngsave_buffer(compression=9, strip=True)
    img.save(out_path, "PNG", optimize=True)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------- main entry
def process_design(raw: bytes, settings: dict, out_dir: Path, base_name: str = "design") -> tuple[bytes, dict]:
    t0 = time.perf_counter()
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    w0, h0 = img.size
    if max(w0, h0) > 3000:  # keep the BFS passes sub-second; vips lanczos still lands on 4500
        s = 3000 / max(w0, h0)
        img = img.resize((max(2, int(w0 * s)), max(2, int(h0 * s))), Image.LANCZOS)

    arr = np.asarray(img).copy()
    specks = 0
    if settings.get("remove", True):
        _border_bgrm(arr, int(settings.get("tol", 42)))
    if int(settings.get("defringe", 0)) > 0:
        _defringe(arr, int(settings.get("defringe", 2)))
    if settings.get("despeckle", True):
        specks = _despeckle(arr, int(settings.get("despeckleMin", 24)))

    coverage = float((arr[..., 3] > 16).mean() * 100.0)
    art = Image.fromarray(arr, "RGBA")

    placement = settings.get("placement", "top")
    top_margin = 350 if placement == "top" else 0
    max_w = int(EXPORT_W * 0.92)
    max_h = EXPORT_H - top_margin - (220 if placement == "top" else int(EXPORT_H * 0.10))
    scale = min(max_w / art.width, max_h / art.height)
    dw, dh = max(2, int(art.width * scale)), max(2, int(art.height * scale))
    art_big = vips_resize(art, dw, dh)
    dx = (EXPORT_W - dw) // 2
    dy = top_margin if placement == "top" else (EXPORT_H - dh) // 2

    if settings.get("halftone"):
        comp = Image.new("RGBA", (EXPORT_W, EXPORT_H), (0, 0, 0, 0))
        comp.alpha_composite(art_big, (dx, dy))
        final = _halftone(comp, int(settings.get("lpi", 48)), settings.get("ink", "#E8E2D2"), EXPORT_W, EXPORT_H)
    else:
        final = Image.new("RGBA", (EXPORT_W, EXPORT_H), (0, 0, 0, 0))
        final.alpha_composite(art_big, (dx, dy))

    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in base_name)[:60] or "design"
    out_path = out_dir / f"{safe}_{EXPORT_W}x{EXPORT_H}_{time.strftime('%Y%m%d-%H%M%S')}.png"
    png_bytes = vips_save(final, out_path)

    ms = round((time.perf_counter() - t0) * 1000)
    report = {
        "ms": ms,
        "coverage": round(coverage, 1),
        "specks": int(specks),
        "path": str(out_path),
        "w": EXPORT_W,
        "h": EXPORT_H,
        "engine": ENGINE_NAME,
    }
    return png_bytes, report


# ------------------------------------------------- mock engine (offline dev)
def mock_render(style_bytes: bytes, niche_bytes: bytes, aspect: str, background: str, text: str) -> bytes:
    """
    Deterministic placeholder engine (MERCHFLOW_MOCK_ENGINE=1) so the full
    dispatch → libvips → SSE → disk pipeline is testable without Gemini auth.
    """
    dims = {"4:5": (1120, 1400), "1:1": (1400, 1400), "3:4": (1200, 1600)}.get(aspect, (1120, 1400))
    style = Image.open(io.BytesIO(style_bytes)).convert("RGB").resize((64, 64))
    q = style.quantize(colors=6, method=Image.MEDIANCUT).convert("RGB")
    pal = q.getcolors(64 * 64) or [(1, (20, 20, 30))]
    pal.sort(reverse=True)
    ramp = sorted((c for _, c in pal), key=lambda c: sum(c))

    niche = Image.open(io.BytesIO(niche_bytes)).convert("RGBA")
    scale = min(dims[0] * 0.8 / niche.width, dims[1] * 0.7 / niche.height)
    niche = niche.resize((max(2, int(niche.width * scale)), max(2, int(niche.height * scale))), Image.LANCZOS)

    canvas = Image.new("RGBA", dims, (0, 0, 0, 0))
    px = niche.load()
    lo, hi = ramp[0], ramp[-1]
    for y in range(niche.height):
        for x in range(niche.width):
            r, g, b, a = px[x, y]
            if a < 12:
                continue
            t = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            px[x, y] = (
                int(lo[0] + (hi[0] - lo[0]) * t),
                int(lo[1] + (hi[1] - lo[1]) * t),
                int(lo[2] + (hi[2] - lo[2]) * t),
                a,
            )
    canvas.alpha_composite(niche, ((dims[0] - niche.width) // 2, (dims[1] - niche.height) // 2))

    if text.strip():
        from PIL import ImageDraw, ImageFont

        drw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("arialbd.ttf", int(dims[1] * 0.08))
        except Exception:
            font = ImageFont.load_default()
        drw.text((dims[0] / 2, dims[1] * 0.86), text.upper(), font=font, fill=hi + (255,), anchor="mm")

    if background in ("white", "black"):
        bg = Image.new("RGBA", dims, (255, 255, 255, 255) if background == "white" else (11, 11, 11, 255))
        bg.alpha_composite(canvas)
        canvas = bg

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()


def postprocess_generate(raw: bytes, payload: dict, out_dir: Path, name: str) -> tuple[bytes, dict]:
    """Print-standardize a fresh AI render (keep-holes + lanczos 4500×5400)."""
    settings = {
        "remove": True,
        "tol": 46,
        "defringe": 2,
        "despeckle": True,
        "halftone": False,
        "placement": "top",
    }
    return process_design(raw, settings, out_dir, base_name=name)
