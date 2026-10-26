"""Neural cutout and genuine print/8K PNG pipelines.

With AI uses rembg and never falls back to colour-key removal. The explicitly
opt-in Without AI mode keys a sampled flat border colour. Missing rembg/model
weights or an inference/write error fails visibly rather than faking success.
Background removal, despeckle, and the 1–2 px alpha choke run at provider-native
resolution; only validated foreground is expanded for print export.
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
EMPTY_FOREGROUND_ERROR = "No foreground remaining; try With AI mode or adjust tolerance."
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


def _decode_native_rgba(raw: bytes) -> Image.Image:
    """Decode common no-AI inputs into one mutable native-size RGBA array."""
    if cv2 is None:
        return decode_image(raw)
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.mode not in {"RGBA", "RGB", "L", "P"}:
                return decode_image(raw)
            if opened.info.get("exif") and opened.getexif().get(274, 1) != 1:
                return decode_image(raw)
    except Exception:
        return decode_image(raw)
    if HAS_VIPS:
        try:
            decoded_vips = pyvips.Image.new_from_buffer(raw, "", access="sequential")
            if decoded_vips.format != "uchar":
                return decode_image(raw)
            if decoded_vips.bands == 3:
                opaque = pyvips.Image.black(decoded_vips.width, decoded_vips.height).new_from_image(255)
                decoded_vips = decoded_vips.bandjoin(opaque)
            elif decoded_vips.bands == 1:
                decoded_vips = decoded_vips.colourspace("srgb")
                opaque = pyvips.Image.black(decoded_vips.width, decoded_vips.height).new_from_image(255)
                decoded_vips = decoded_vips.bandjoin(opaque)
            if decoded_vips.bands == 4:
                pixels = decoded_vips.write_to_memory()
                rgba = np.frombuffer(pixels, dtype=np.uint8).reshape(decoded_vips.height, decoded_vips.width, 4)
                image = Image.frombuffer("RGBA", (decoded_vips.width, decoded_vips.height), rgba,
                                         "raw", "RGBA", 0, 1)
                image._merchflow_rgba = rgba
                return image
        except Exception:
            pass
    decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None or decoded.dtype != np.uint8:
        return decode_image(raw)
    if decoded.ndim == 2:
        rgba = cv2.cvtColor(decoded, cv2.COLOR_GRAY2RGBA)
    elif decoded.ndim == 3 and decoded.shape[2] == 3:
        rgba = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGBA)
    elif decoded.ndim == 3 and decoded.shape[2] == 4:
        rgba = decoded
        cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA, dst=rgba)
    else:
        return decode_image(raw)
    h, w = rgba.shape[:2]
    image = Image.frombuffer("RGBA", (w, h), rgba, "raw", "RGBA", 0, 1)
    image._merchflow_rgba = rgba
    return image


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
        cleaned = _decontaminate_edges(cutout)
    except Exception as exc:
        raise RuntimeError(f"rembg {model} matting failed (no colour-key fallback): {exc}") from exc
    if not cleaned.getchannel("A").getbbox():
        raise ValueError(EMPTY_FOREGROUND_ERROR)
    return cleaned


def remove_background_color(
    image: Image.Image, *, precision: str = "keep_holes", tolerance: int = 24,
    consume: bool = False,
) -> tuple[Image.Image, tuple[int, int, int]]:
    """Remove a flat border colour without loading the ONNX matting model.

    The border median provides a fast automatic key for solid backgrounds. In
    ``keep_holes`` mode, OpenCV flood-fills only matching pixels reachable from
    the exterior (4-connected), leaving enclosed letter counters untouched.
    ``everywhere`` clears every matching pixel, including enclosed counters.
    This is intentionally a solid-colour tool; use neural matting for textured
    or uneven backgrounds. No RGB palette quantization or recolouring occurs.
    ``consume=True`` reuses an attached mutable RGBA buffer when available,
    otherwise copies the pixels and closes the caller's decoded Pillow buffer.
    """
    if cv2 is None:
        raise RuntimeError("Without AI cutout requires opencv-python-headless")
    precision = str(precision).strip().lower().replace("-", "_")
    if precision not in {"keep_holes", "everywhere"}:
        raise ValueError("Cutout precision must be keep_holes or everywhere")

    rgba_source = image if image.mode == "RGBA" else image.convert("RGBA")
    backing = getattr(image, "_merchflow_rgba", None)
    if (consume and isinstance(backing, np.ndarray) and backing.shape == (image.height, image.width, 4)
            and backing.dtype == np.uint8 and backing.flags.c_contiguous and backing.flags.writeable):
        rgba = backing
    else:
        rgba = np.array(rgba_source, copy=True)
    if rgba_source is not image:
        rgba_source.close()
    if consume:
        image.close()
    h, w = rgba.shape[:2]
    if h < 1 or w < 1:
        raise ValueError("Cannot detect a background colour in an empty image")
    rgb, alpha = rgba[..., :3], rgba[..., 3]
    border_pixels = np.concatenate([rgb[0, :], rgb[h - 1, :], rgb[:, 0], rgb[:, w - 1]], axis=0)
    border_alpha = np.concatenate([alpha[0, :], alpha[h - 1, :], alpha[:, 0], alpha[:, w - 1]], axis=0)
    visible_border = border_pixels[border_alpha > 0]
    sample = visible_border if visible_border.size else border_pixels
    detected_bg = np.median(sample, axis=0).astype(np.int32)

    tolerance = max(0, min(128, int(tolerance)))
    lower = np.empty(4, dtype=np.uint8)
    upper = np.empty(4, dtype=np.uint8)
    lower[:3] = np.maximum(detected_bg - tolerance, 0).astype(np.uint8)
    upper[:3] = np.minimum(detected_bg + tolerance, 255).astype(np.uint8)
    lower[3], upper[3] = 1, 255  # preserve fully transparent input pixels
    candidates = cv2.inRange(rgba, lower, upper)

    if precision == "keep_holes":
        # Mark each exterior-connected matching region with 128. The matching
        # pixels still marked 255 are enclosed counters and remain unchanged.
        # OpenCV performs each fill in native code; no Python per-pixel BFS.
        for x in range(w):
            if candidates[0, x] == 255:
                cv2.floodFill(candidates, None, (x, 0), 128, loDiff=0, upDiff=0, flags=4)
            if candidates[h - 1, x] == 255:
                cv2.floodFill(candidates, None, (x, h - 1), 128, loDiff=0, upDiff=0, flags=4)
        for y in range(h):
            if candidates[y, 0] == 255:
                cv2.floodFill(candidates, None, (0, y), 128, loDiff=0, upDiff=0, flags=4)
            if candidates[y, w - 1] == 255:
                cv2.floodFill(candidates, None, (w - 1, y), 128, loDiff=0, upDiff=0, flags=4)
        # Reuse the candidate mask as the removal mask; no second full-size mask.
        cv2.compare(candidates, 128, cv2.CMP_EQ, dst=candidates)

    # Masked in-place subtraction clears RGBA without a full-size advanced-index
    # temporary. Fully transparent input was excluded by the four-band range.
    cv2.subtract(rgba, rgba, dst=rgba, mask=candidates)
    detected = tuple(int(c) for c in detected_bg)
    del rgb, alpha, border_pixels, border_alpha, visible_border, sample
    del lower, upper, candidates
    cutout = Image.frombuffer("RGBA", (w, h), rgba, "raw", "RGBA", 0, 1)
    # Keep the mutable backing array attached: choke/despeckle can mutate its
    # alpha band without materializing another full RGBA Pillow buffer.
    cutout._merchflow_rgba = rgba
    return cutout, detected


def choke_alpha(image: Image.Image, pixels: int = 1) -> Image.Image:
    """Erode the alpha once at the current (native) image resolution."""
    if cv2 is None:
        raise RuntimeError("Alpha choke requires opencv-python-headless")
    pixels = max(1, min(2, int(pixels)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pixels * 2 + 1, pixels * 2 + 1))
    rgba = getattr(image, "_merchflow_rgba", None)
    if isinstance(rgba, np.ndarray) and rgba.shape == (image.height, image.width, 4):
        alpha = rgba[..., 3]
        rgba[..., 3] = cv2.erode(alpha, kernel, iterations=1,
                                 borderType=cv2.BORDER_CONSTANT, borderValue=0)
        return image
    alpha = np.asarray(image.getchannel("A"))
    eroded = cv2.erode(alpha, kernel, iterations=1, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    image.putalpha(Image.fromarray(eroded))
    return image


def _despeckle(image: Image.Image, min_size: int = 4) -> tuple[Image.Image, int]:
    if cv2 is None:
        raise RuntimeError("Micro-dot cleanup requires opencv-python-headless")
    rgba = getattr(image, "_merchflow_rgba", None)
    backed = isinstance(rgba, np.ndarray) and rgba.shape == (image.height, image.width, 4)
    alpha = rgba[..., 3] if backed else np.array(image.getchannel("A"))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 16).astype(np.uint8), connectivity=8)
    small = np.zeros(count, dtype=bool)
    small[1:] = stats[1:, cv2.CC_STAT_AREA] < max(1, min_size)
    removed = small[labels]
    pixels = int(np.count_nonzero(removed))
    alpha[removed] = 0
    if not backed:
        image.putalpha(Image.fromarray(alpha))
    del labels, stats, small, removed
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


def _coverage(image: Image.Image, *, denominator: int | None = None) -> float:
    hist = image.getchannel("A").histogram()
    area = denominator or (image.width * image.height)
    return round(sum(hist[17:]) / area * 100, 1)


def _vips_from_image(image: Image.Image):
    """Wrap a lossless RGBA buffer directly for the streaming libvips graph."""
    if not HAS_VIPS:
        raise RuntimeError("Streaming Prepare export requires native libvips")
    rgba_buffer = getattr(image, "_merchflow_rgba", None)
    if not (isinstance(rgba_buffer, np.ndarray) and rgba_buffer.shape == (image.height, image.width, 4)
            and rgba_buffer.dtype == np.uint8 and rgba_buffer.flags.c_contiguous):
        rgba_buffer = image.tobytes()
    source = pyvips.Image.new_from_memory(rgba_buffer, image.width, image.height, 4, "uchar").copy(interpretation="srgb")
    if source.bands != 4 or source.format != "uchar":
        raise ValueError("Prepare source could not be represented as lossless RGBA")
    # new_from_memory borrows this buffer; retain it until the output PNG has
    # finished evaluating the graph.
    return source, rgba_buffer


def _crop_cutout(image: Image.Image, bounds: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = bounds
    rgba = getattr(image, "_merchflow_rgba", None)
    if isinstance(rgba, np.ndarray) and rgba.shape == (image.height, image.width, 4):
        cropped = np.ascontiguousarray(rgba[top:bottom, left:right])
        result = Image.frombuffer("RGBA", (right - left, bottom - top), cropped, "raw", "RGBA", 0, 1)
        result._merchflow_rgba = cropped
        image.close()
        return result
    return image.crop(bounds)


def _vips_coverage(image) -> float:
    histogram = np.frombuffer(image.extract_band(3).hist_find().write_to_memory(), dtype=np.uint32)
    return round(float(histogram[17:].sum()) / (image.width * image.height) * 100, 1)


def _vips_flatten_rgba(image, background: str):
    colour = (0, 0, 0) if background == "black" else (255, 255, 255)
    flattened = image.flatten(background=list(colour))
    opaque = pyvips.Image.black(image.width, image.height).new_from_image(255)
    return flattened.bandjoin(opaque).copy(interpretation="srgb")


def _prepare(raw: bytes, settings: dict, out_dir: Path, name: str, metadata: dict | None,
             *, return_bytes: bool = True) -> tuple[bytes, dict]:
    t0 = time.perf_counter()
    if not return_bytes and not HAS_VIPS:
        raise RuntimeError("Streaming Prepare export requires the native libvips runtime")
    model = str(settings.get("model") or os.environ.get("MERCHFLOW_REMBG_MODEL", "u2net"))
    remove = bool(settings.get("remove", True))
    mode = str(settings.get("mode", settings.get("cutoutMode", "with_ai"))).strip().lower()
    if mode not in {"with_ai", "without_ai"}:
        raise ValueError("Cutout mode must be with_ai or without_ai")
    precision = str(settings.get("precision", "keep_holes")).strip().lower().replace("-", "_")
    image = _decode_native_rgba(raw) if remove and mode == "without_ai" else decode_image(raw)
    native_w, native_h = image.size
    detected_bg: tuple[int, int, int] | None = None
    if remove:
        if mode == "without_ai":
            image, detected_bg = remove_background_color(
                image, precision=precision, tolerance=int(settings.get("colorTolerance", 24)), consume=True,
            )
        else:
            image = remove_background(image, model)
        bounds = image.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError(EMPTY_FOREGROUND_ERROR)
        # Crop only to the native foreground bounds before despeckle; exterior
        # pixels are already transparent and cannot connect separate components.
        image = _crop_cutout(image, bounds)
    specks = 0
    if settings.get("despeckle", True):
        image, specks = _despeckle(image, int(settings.get("despeckleMin", 4)))
    coverage: float | None = None
    if remove:
        bounds = image.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError(EMPTY_FOREGROUND_ERROR)
        image = _crop_cutout(image, bounds)
        image = choke_alpha(image, int(settings.get("defringe", 1)))
        if image.getchannel("A").getbbox() is None:
            raise ValueError(EMPTY_FOREGROUND_ERROR)
        coverage = _coverage(image, denominator=native_w * native_h)

    resize_for_print = bool(settings.get("resizeForPrint", settings.get("resize_for_print", True)))
    upscale_enabled = bool(settings.get("upscale", False))
    upscale_factor = int(settings.get("upscaleFactor", 2)) if upscale_enabled else 1
    if upscale_factor not in {1, 2, 4, 8}:
        raise ValueError("Upscale factor must be 2, 4, or 8")
    if resize_for_print:
        w, h = settings.get("dimensions", (EXPORT_W, EXPORT_H))
        if (w, h) not in {(4500, 5400), (4500, 4500), (4050, 5400)}:
            raise ValueError("Unsupported print canvas")
        top = 350 if settings.get("placement", "top") == "top" else 0
        max_w, max_h = int(w * 0.92), h - top - (220 if top else int(h * 0.10))
        scale = min(max_w / image.width, max_h / image.height)
        dw, dh = max(1, round(image.width * scale)), max(1, round(image.height * scale))
        x, y = (w - dw) // 2, top if top else (h - dh) // 2
        base_w, base_h = w, h
    else:
        # With print resizing off, retain the source/cutout pixel dimensions.
        # This makes the independent 2x/4x/8x upscale useful for smaller art.
        dw, dh = image.size
        x = y = 0
        base_w, base_h = dw, dh

    streaming = not return_bytes and HAS_VIPS and not settings.get("halftone")
    if streaming:
        source, source_buffer = _vips_from_image(image)
        image.close()  # the source graph now owns/retains the native backing buffer
        if (dw, dh) != (source.width, source.height):
            source = (source.premultiply()
                      .resize(dw / source.width, vscale=dh / source.height, kernel="lanczos3")
                      .unpremultiply().cast("uchar"))
        if resize_for_print:
            final = source.embed(x, y, base_w, base_h, extend="background", background=[0, 0, 0, 0])
        else:
            final = source
        if upscale_factor > 1:
            final = (final.premultiply().resize(upscale_factor, kernel="lanczos3")
                     .unpremultiply().cast("uchar"))
        try:
            if not remove:
                coverage = _vips_coverage(final)
            bg = str((metadata or {}).get("background", "transparent"))
            if bg in {"black", "white"}:
                final = _vips_flatten_rgba(final, bg)
            meta = {
                **(metadata or {}),
                "source": str((metadata or {}).get("source", "prepare")),
                "style": str((metadata or {}).get("style", "Prepared print")),
                "background": bg,
                "upscaled": upscale_factor > 1,
                "upscaleFactor": upscale_factor,
                "resizeForPrint": resize_for_print,
                "cutoutMode": mode if remove else "disabled",
                "detectedBg": list(detected_bg) if detected_bg else None,
                "nativeW": native_w, "nativeH": native_h,
            }
            design = output_store.save_vips_image(
                final, out_dir, name, meta,
                ms=round((time.perf_counter() - t0) * 1000),
                compress=bool(settings.get("compress", True)),
            )
        finally:
            del source_buffer
    else:
        placed = vips_resize(image, dw, dh)
        if resize_for_print:
            final = Image.new("RGBA", (base_w, base_h))
            final.alpha_composite(placed, (x, y))
        else:
            final = placed
        del image, placed
        if upscale_factor > 1:
            final = vips_resize(final, final.width * upscale_factor, final.height * upscale_factor)
        if settings.get("halftone"):
            final = _halftone(final, int(settings.get("lpi", 48)), settings.get("ink", "#E8E2D2"), final.width, final.height)
        if not remove:
            coverage = _coverage(final)
        bg = str((metadata or {}).get("background", "transparent"))
        if bg in {"black", "white"}:
            solid = Image.new("RGBA", final.size, "black" if bg == "black" else "white")
            solid.alpha_composite(final)
            final = solid
        save_meta = {**(metadata or {}), "nativeW": native_w, "nativeH": native_h}
        png, design = output_store.save_image(
            final, out_dir, name, save_meta,
            ms=round((time.perf_counter() - t0) * 1000),
            compress=bool(settings.get("compress", True)),
        )
        report = {
            **design, "design": design, "coverage": coverage, "specks": specks,
            "nativeW": native_w, "nativeH": native_h,
            "engine": ENGINE_NAME, "cutoutModel": model if remove and mode == "with_ai" else None,
            "cutoutMode": mode if remove else "disabled",
            "detectedBg": list(detected_bg) if detected_bg else None,
            "upscaled": upscale_factor > 1, "upscaleFactor": upscale_factor,
            "resizeForPrint": resize_for_print,
        }
        return png, report

    report = {
        **design, "design": design, "coverage": coverage, "specks": specks,
        "nativeW": native_w, "nativeH": native_h,
        "engine": "pyvips-streaming", "cutoutModel": model if remove and mode == "with_ai" else None,
        "cutoutMode": mode if remove else "disabled",
        "detectedBg": list(detected_bg) if detected_bg else None,
        "upscaled": upscale_factor > 1, "upscaleFactor": upscale_factor,
        "resizeForPrint": resize_for_print,
    }
    return b"", report


def process_design(raw: bytes, settings: dict, out_dir: Path, base_name: str = "design",
                   metadata: dict | None = None, *, return_bytes: bool = True) -> tuple[bytes, dict]:
    with PIPELINE_LOCK:
        return _prepare(raw, settings, out_dir, base_name, metadata, return_bytes=return_bytes)


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
