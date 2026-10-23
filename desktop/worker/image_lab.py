"""Neural cutout and genuine print/8K PNG pipelines.

There is intentionally NO colour-key fallback. Missing rembg/model weights or
an inference/write error fails the job visibly rather than faking success.
The 1–2 px alpha choke runs at EXPORT resolution, not on tiny source artwork.
"""
from __future__ import annotations

import io
import math
import os
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import output_store

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from rembg import new_session as _rembg_new_session
    from rembg import remove as _rembg_remove
except ImportError:
    _rembg_new_session = _rembg_remove = None

try:
    import pyvips
    HAS_VIPS = True
except (ImportError, OSError):
    HAS_VIPS = False

HAS_REMBG = _rembg_remove is not None
HAS_CV = cv2 is not None
EXPORT_W, EXPORT_H = 4500, 5400
ENGINE_NAME = "pyvips-lanczos3" if HAS_VIPS else "pillow-lanczos"
REMBG_MODELS = {"u2net": "u2net", "birefnet": "birefnet-general", "birefnet-general": "birefnet-general"}
_SESSIONS: dict[str, object] = {}
_SESSION_LOCK = threading.Lock()
# Bounded memory on ordinary Windows PCs, including concurrent HTTP requests.
PIPELINE_LOCK = threading.RLock()


def _session(model: str):
    if not HAS_REMBG:
        raise RuntimeError("Neural cutout requires rembg: pip install -r desktop/requirements.txt")
    if model not in REMBG_MODELS:
        raise ValueError("Cutout model must be u2net or birefnet-general")
    name = REMBG_MODELS[model]
    with _SESSION_LOCK:
        if name not in _SESSIONS:
            # Keep only one model resident: BiRefNet and U2Net together are large.
            _SESSIONS.clear()
            _SESSIONS[name] = _rembg_new_session(name, providers=["CPUExecutionProvider"])
        return _SESSIONS[name]


def decode_image(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as opened:
        opened.load()
        return ImageOps.exif_transpose(opened).convert("RGBA")


def _decontaminate_edges(image: Image.Image) -> Image.Image:
    """Replace mixed edge RGB with nearby confident foreground, never a bg key.

    Soft hair with no opaque neighbour is left to rembg's foreground matting.
    Original transparent counters remain transparent; we never fill holes.
    """
    if cv2 is None:
        raise RuntimeError("Alpha cleanup requires opencv-python-headless")
    arr = np.array(image)
    alpha = arr[..., 3]
    solid = (alpha >= 245).astype(np.float32)
    weight = cv2.boxFilter(solid, -1, (3, 3), normalize=False)
    edge = (alpha > 0) & (alpha < 245) & (weight > 0)
    if edge.any():
        for ch in range(3):
            colour = cv2.boxFilter(arr[..., ch].astype(np.float32) * solid, -1, (3, 3), normalize=False)
            colour /= np.maximum(weight, 1)
            arr[..., ch][edge] = np.clip(colour[edge], 0, 255).astype(np.uint8)
    arr[..., :3][alpha == 0] = 0
    return Image.fromarray(arr)


def remove_background(image: Image.Image, model: str = "u2net") -> Image.Image:
    """Universal semantic matting for orange/navy/photographic/textured backdrops."""
    session = _session(model)
    try:
        result = _rembg_remove(
            image, session=session, alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=3,
            post_process_mask=False,  # closing/smoothing can erase small lettering
        )
        cutout = result.convert("RGBA") if isinstance(result, Image.Image) else decode_image(result)
        # rembg must never resurrect transparent pixels in an uploaded PNG.
        alpha = np.minimum(np.asarray(image.getchannel("A")), np.asarray(cutout.getchannel("A")))
        cutout.putalpha(Image.fromarray(alpha))
        if not cutout.getchannel("A").getbbox():
            raise ValueError("The cutout model returned an empty foreground; try the other model")
        return _decontaminate_edges(cutout)
    except Exception as exc:
        raise RuntimeError(f"rembg {model} matting failed (no colour-key fallback): {exc}") from exc


def choke_alpha(image: Image.Image, pixels: int = 1) -> Image.Image:
    """A real 1–2 export-pixel cv2.erode; no repeated source-resolution shrinking."""
    if cv2 is None:
        raise RuntimeError("Alpha choke requires opencv-python-headless")
    pixels = max(1, min(2, int(pixels)))
    alpha = np.asarray(image.getchannel("A"))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pixels * 2 + 1, pixels * 2 + 1))
    eroded = cv2.erode(alpha, kernel, iterations=1, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    image.putalpha(Image.fromarray(eroded))
    return image


def _despeckle(image: Image.Image, min_size: int = 4) -> tuple[Image.Image, int]:
    if cv2 is None:
        raise RuntimeError("Micro-dot cleanup requires opencv-python-headless")
    alpha = np.array(image.getchannel("A"))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 16).astype(np.uint8), connectivity=8)
    small = np.zeros(count, dtype=bool)
    small[1:] = stats[1:, cv2.CC_STAT_AREA] < max(1, min_size)
    removed = small[labels]
    pixels = int(np.count_nonzero(removed & (alpha > 0)))
    alpha[removed] = 0
    image.putalpha(Image.fromarray(alpha))
    return image, pixels


def _halftone(src: Image.Image, lpi: int, ink_hex: str, out_w: int, out_h: int) -> Image.Image:
    from PIL import ImageDraw
    sw, sh = src.size
    small = src.resize((1400, max(1, round(sh * 1400 / sw))), Image.Resampling.BILINEAR) if sw > 1400 else src
    arr = np.asarray(small)
    h, w = arr.shape[:2]
    lum = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]) / 255.0
    alpha = arr[..., 3] / 255.0
    pitch_out = max(3.0, 300 / max(20, min(100, lpi)))
    factor = out_w / w
    pitch = pitch_out / factor
    angle = math.radians(45)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    out = Image.new("RGBA", (out_w, out_h))
    draw = ImageDraw.Draw(out)
    ink = tuple(int(ink_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    bound = int(math.hypot(w, h) / pitch) + 2
    for gy in range(-bound, bound + 1):
        for gx in range(-bound, bound + 1):
            x = gx * pitch * cos_a - gy * pitch * sin_a + w / 2
            y = gx * pitch * sin_a + gy * pitch * cos_a + h / 2
            xi, yi = int(round(x)), int(round(y))
            if not (0 <= xi < w and 0 <= yi < h) or alpha[yi, xi] < 0.12:
                continue
            radius = pitch_out * 0.47 * math.sqrt((1 - lum[yi, xi]) * alpha[yi, xi])
            if radius < pitch_out * 0.075:
                continue
            cx, cy = x * factor, y * factor
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=ink)
    return out


def vips_resize(image: Image.Image, w: int, h: int) -> Image.Image:
    if not HAS_VIPS:
        # Pillow resamples RGBA via premultiplied alpha, avoiding RGB fringe bleed.
        return image.resize((w, h), Image.Resampling.LANCZOS)
    # Explicit premultiplication prevents transparent RGB bleeding with libvips.
    source = pyvips.Image.new_from_memory(image.tobytes(), image.width, image.height, 4, "uchar")
    resized = source.premultiply().resize(w / image.width, vscale=h / image.height, kernel="lanczos3").unpremultiply().cast("uchar")
    return Image.frombytes("RGBA", (resized.width, resized.height), bytes(resized.write_to_memory()))


def _coverage(image: Image.Image) -> float:
    hist = image.getchannel("A").histogram()
    return round(sum(hist[17:]) / (image.width * image.height) * 100, 1)


def _prepare(raw: bytes, settings: dict, out_dir: Path, name: str, metadata: dict | None) -> tuple[bytes, dict]:
    t0 = time.perf_counter()
    image = decode_image(raw)
    model = str(settings.get("model") or os.environ.get("MERCHFLOW_REMBG_MODEL", "u2net"))
    remove = bool(settings.get("remove", True))
    if remove:
        image = remove_background(image, model)
    specks = 0
    if settings.get("despeckle", True):
        image, specks = _despeckle(image, int(settings.get("despeckleMin", 4)))
    if remove:
        bounds = image.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError("No printable foreground remains")
        image = image.crop(bounds)

    w, h = settings.get("dimensions", (EXPORT_W, EXPORT_H))
    if (w, h) not in {(4500, 5400), (4500, 4500), (4050, 5400)}:
        raise ValueError("Unsupported print canvas")
    top = 350 if settings.get("placement", "top") == "top" else 0
    max_w, max_h = int(w * 0.92), h - top - (220 if top else int(h * 0.10))
    scale = min(max_w / image.width, max_h / image.height)
    dw, dh = max(1, round(image.width * scale)), max(1, round(image.height * scale))
    placed = vips_resize(image, dw, dh)
    final = Image.new("RGBA", (w, h))
    final.alpha_composite(placed, ((w - dw) // 2, top if top else (h - dh) // 2))
    del image, placed
    if remove:
        final = choke_alpha(final, int(settings.get("defringe", 1)))
    if settings.get("halftone"):
        final = _halftone(final, int(settings.get("lpi", 48)), settings.get("ink", "#E8E2D2"), w, h)
    coverage = _coverage(final)
    bg = str((metadata or {}).get("background", "transparent"))
    if bg in {"black", "white"}:
        solid = Image.new("RGBA", final.size, "black" if bg == "black" else "white")
        solid.alpha_composite(final)
        final = solid
    png, design = output_store.save_image(final, out_dir, name, metadata, ms=round((time.perf_counter() - t0) * 1000), compress=bool(settings.get("compress", True)))
    report = {**design, "design": design, "coverage": coverage, "specks": specks, "engine": ENGINE_NAME, "cutoutModel": model if remove else None}
    return png, report


def process_design(raw: bytes, settings: dict, out_dir: Path, base_name: str = "design", metadata: dict | None = None) -> tuple[bytes, dict]:
    with PIPELINE_LOCK:
        return _prepare(raw, settings, out_dir, base_name, metadata)


def pixel_to_pixel(raw: bytes, payload: dict, out_dir: Path, name: str) -> tuple[bytes, dict]:
    """Export a REAL provider recreation, not a renamed/recoloured input.

    8K means 8192 px on the longest edge. Provider-native dimensions are
    disclosed; Lanczos enlarges the real render when the provider returns less.
    'original' bypasses segmentation and retains the provider-rendered backdrop.
    """
    with PIPELINE_LOCK:
        t0 = time.perf_counter()
        image = decode_image(raw)
        native_w, native_h = image.size
        background = payload.get("background", "transparent")
        if background not in {"transparent", "black", "white", "original"}:
            raise ValueError("Unknown recreation background")
        if background != "original":
            image = remove_background(image, str(payload.get("model", "u2net")))
        scale = 8192 / max(image.size)
        image = vips_resize(image, max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        if background != "original":
            image = choke_alpha(image, int(payload.get("defringe", 1)))
        if background in {"black", "white"}:
            solid = Image.new("RGBA", image.size, background)
            solid.alpha_composite(image)
            image = solid
        meta = {
            "source": "pixel-to-pixel", "style": "8K concept recreation", "niche": name,
            "background": background, "prompt": payload.get("prompt", ""), "account": payload.get("account", "local"),
        }
        png, design = output_store.save_image(image, out_dir, name, meta, ms=round((time.perf_counter() - t0) * 1000))
        return png, {**design, "design": design, "nativeW": native_w, "nativeH": native_h, "upscaled": scale > 1, "engine": ENGINE_NAME}


def postprocess_generate(raw: bytes, payload: dict, out_dir: Path, name: str) -> tuple[bytes, dict]:
    if payload.get("task") == "recreate":
        return pixel_to_pixel(raw, payload, out_dir, name)
    dimensions = {"4:5": (4500, 5400), "1:1": (4500, 4500), "3:4": (4050, 5400)}.get(payload.get("aspect"), (EXPORT_W, EXPORT_H))
    metadata = {
        "source": "generator", "niche": name, "style": payload.get("styleName", "Style fusion"),
        "aspect": payload.get("aspect", "4:5"), "background": payload.get("background", "transparent"),
        "prompt": payload.get("prompt", ""), "account": payload.get("account", "local"),
    }
    return process_design(raw, {"remove": True, "model": payload.get("model", "u2net"), "defringe": 1, "despeckle": False, "dimensions": dimensions, "placement": "top"}, out_dir, name, metadata)
