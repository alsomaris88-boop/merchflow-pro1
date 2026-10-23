"""
MerchFlow Pro — Elite design production tools (pure print files, zero mockups).

  1. adapt_to_products()        multi-product canvas layout engine (libvips bbox)
  2. scan_graphic_ocr()         in-graphic OCR trademark sentry (tesseract)
  3. compute_phash()            256-bit DCT perceptual hash + catalog dedupe
  4. vectorize_svg()            offline raster→SVG (vtracer, Rust)
  5. apply_distress_knockout()  vintage cracked-ink alpha knockout
  6. generate_dark_light_split() CIEDE2000 contrast-aware garment split

Every routine streams through libvips where available and degrades to numpy /
Pillow so the engine still runs on a bare Python install.
"""
from __future__ import annotations

import io
import math
import os
import re
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from . import output_store

try:
    import pyvips

    HAS_VIPS = True
except Exception:
    HAS_VIPS = False

try:
    import pytesseract

    HAS_TESS = True
except Exception:
    HAS_TESS = False

try:
    import vtracer

    HAS_VTRACER = True
except Exception:
    HAS_VTRACER = False


# ===========================================================================
# 1 — PolyCanvas: multi-product canvas layout engine
# ===========================================================================

PRODUCTS: dict[str, dict] = {
    "tshirt": {
        "label": "Standard T-Shirt / Long Sleeve",
        "w": 4500, "h": 5400, "top_margin": 350, "mode": "top",
        "fill": 0.92, "note": "top 350px collar margin, centered",
    },
    "hoodie": {
        "label": "Hoodie / Sweatshirt",
        "w": 4500, "h": 4050, "top_margin": 300, "mode": "top",
        "bottom_safe": 800, "fill": 0.88, "note": "800px bottom kangaroo-pocket safe zone",
    },
    "popsocket": {
        "label": "PopSockets Grip",
        "w": 2400, "h": 2400, "mode": "center", "circle_mask": 1000,
        "fill": 0.72, "note": "circular alpha mask dist <= 1000px",
    },
    "tote": {
        "label": "Tote Bag",
        "w": 2925, "h": 2925, "mode": "center", "fill": 0.80, "note": "square scaling",
    },
    "pillow": {
        "label": "Throw Pillow",
        "w": 4500, "h": 4500, "mode": "center", "fill": 0.84, "note": "centered composition",
    },
}


def tight_bbox(img: Image.Image, threshold: int = 12) -> tuple[int, int, int, int]:
    """
    Tight non-transparent bounding box.
    libvips path uses project() row/column histograms (streamed, no full decode).
    """
    if HAS_VIPS:
        try:
            arr = np.ascontiguousarray(np.asarray(img))
            vi = pyvips.Image.new_from_memory(arr.tobytes(), img.width, img.height, 4, "uchar")
            alpha = vi.extract_band(3) > threshold
            cols, rows = alpha.project()
            cmem = np.frombuffer(cols.write_to_memory(), dtype=np.uint32 if cols.format == "uint" else np.float64)
            rmem = np.frombuffer(rows.write_to_memory(), dtype=np.uint32 if rows.format == "uint" else np.float64)
            xs = np.nonzero(np.asarray(cmem).reshape(-1)[: img.width])[0]
            ys = np.nonzero(np.asarray(rmem).reshape(-1)[: img.height])[0]
            if xs.size and ys.size:
                return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1
        except Exception:
            pass
    a = np.asarray(img)[..., 3]
    ys, xs = np.nonzero(a > threshold)
    if xs.size == 0 or ys.size == 0:
        return 0, 0, img.width, img.height
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _circle_mask(size: int, radius: int) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size]
    c = (size - 1) / 2.0
    dist = np.sqrt((xx - c) ** 2 + (yy - c) ** 2)
    # 2px feather so the vector edge stays clean, not jagged
    m = np.clip((radius - dist) / 2.0 + 0.5, 0.0, 1.0)
    return m.astype(np.float32)


def adapt_to_products(
    raw: bytes,
    out_dir: Path,
    base_name: str = "design",
    products: list[str] | None = None,
) -> list[dict]:
    """Take one master transparent PNG → every official Amazon canvas."""
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    x0, y0, x1, y1 = tight_bbox(img)
    art = img.crop((x0, y0, x1, y1))
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9_-]+", "-", base_name.lower())[:48] or "design"
    results: list[dict] = []

    for key in products or list(PRODUCTS.keys()):
        spec = PRODUCTS.get(key)
        if not spec:
            continue
        t0 = time.perf_counter()
        W, H = spec["w"], spec["h"]
        top = spec.get("top_margin", 0)
        bottom_safe = spec.get("bottom_safe", 0)

        avail_w = W * spec.get("fill", 0.9)
        if spec["mode"] == "top":
            avail_h = H - top - bottom_safe - int(H * 0.04)
        else:
            avail_h = H * spec.get("fill", 0.9)
        scale = min(avail_w / art.width, avail_h / art.height)
        dw, dh = max(2, int(art.width * scale)), max(2, int(art.height * scale))
        placed = art.resize((dw, dh), Image.LANCZOS)

        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dx = (W - dw) // 2
        dy = top if spec["mode"] == "top" else (H - dh) // 2
        canvas.alpha_composite(placed, (dx, dy))

        if spec.get("circle_mask"):
            arr = np.asarray(canvas).astype(np.float32)
            mask = _circle_mask(W, int(spec["circle_mask"]))
            arr[..., 3] *= mask
            canvas = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGBA")

        _, design = output_store.save_image(canvas, out_dir, f"{base_name}-{key}", {"source": "prepare", "style": spec["label"]})
        path = Path(design["path"])
        buf = io.BytesIO()
        canvas.resize((max(1, W // 6), max(1, H // 6)), Image.LANCZOS).save(buf, "PNG")
        results.append(
            {
                "design": design,
                "product": key,
                "label": spec["label"],
                "w": W, "h": H,
                "note": spec["note"],
                "path": str(path),
                "bytes": path.stat().st_size,
                "ms": round((time.perf_counter() - t0) * 1000),
                "preview": buf.getvalue(),
            }
        )
    return results


# ===========================================================================
# 2 — In-Graphic OCR trademark scanner
# ===========================================================================

def scan_graphic_ocr(raw: bytes, min_conf: int = 55) -> dict:
    """Extract visible text tokens baked INTO the artwork."""
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    # flatten onto white, boost contrast — OCR hates transparency
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, mask=img.split()[3])
    g = np.asarray(flat.convert("L")).astype(np.float32)
    g = np.clip((g - g.min()) / max(1.0, (g.max() - g.min())) * 255.0, 0, 255)
    prepared = Image.fromarray(g.astype(np.uint8))
    if max(prepared.size) < 1200:
        s = 1200 / max(prepared.size)
        prepared = prepared.resize((int(prepared.width * s), int(prepared.height * s)), Image.LANCZOS)

    if not HAS_TESS:
        return {"ok": False, "available": False, "tokens": [],
                "reason": "pytesseract/tesseract not installed on this machine"}

    tokens: list[dict] = []
    try:
        data = pytesseract.image_to_data(prepared, output_type=pytesseract.Output.DICT)
        n = len(data.get("text", []))
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            try:
                conf = int(float(data["conf"][i]))
            except Exception:
                conf = -1
            if len(txt) < 2 or conf < min_conf:
                continue
            if not re.search(r"[A-Za-z]{2,}", txt):
                continue
            tokens.append({
                "text": txt, "conf": conf,
                "x": int(data["left"][i]), "y": int(data["top"][i]),
                "w": int(data["width"][i]), "h": int(data["height"][i]),
            })
    except Exception as e:
        return {"ok": False, "available": False, "tokens": [], "reason": str(e)}

    return {
        "ok": True,
        "available": True,
        "tokens": tokens,
        "text": " ".join(t["text"] for t in tokens),
        "imageW": prepared.width,
        "imageH": prepared.height,
    }


# ===========================================================================
# 3 — pHash anti-spam sentry
# ===========================================================================

def _dct2(a: np.ndarray) -> np.ndarray:
    n = a.shape[0]
    k = np.arange(n)
    basis = np.cos(np.pi * (2 * k[:, None] + 1) * k[None, :] / (2 * n))
    basis[0, :] = basis[0, :] * (1 / math.sqrt(2))
    return basis @ a @ basis.T


def compute_phash(raw: bytes, hash_bits: int = 256) -> str:
    """
    256-bit DCT perceptual hash (16×16 low-frequency block of a 64×64 DCT).
    Alpha is flattened onto white so transparent art hashes consistently.
    """
    side = int(math.sqrt(hash_bits))  # 16 for 256 bits
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, mask=img.split()[3])
    small = np.asarray(flat.convert("L").resize((64, 64), Image.LANCZOS)).astype(np.float64)
    d = _dct2(small)[:side, :side]
    flat_d = d.flatten()
    med = np.median(flat_d[1:])  # skip DC term
    bits = (flat_d > med).astype(np.uint8)
    return "".join(f"{int(''.join(map(str, bits[i:i + 4])), 2):x}" for i in range(0, len(bits), 4))


def hamming(a: str, b: str) -> int:
    if len(a) != len(b):
        return 10_000
    return sum(bin(int(x, 16) ^ int(y, 16)).count("1") for x, y in zip(a, b))


def check_duplicate(raw: bytes, catalog: list[dict], threshold: int = 18) -> dict:
    """catalog: [{id,name,phash}] — flags >85% visual similarity."""
    ph = compute_phash(raw)
    matches = []
    for entry in catalog:
        other = entry.get("phash") or ""
        if not other:
            continue
        d = hamming(ph, other)
        if d <= threshold:
            matches.append({
                "id": entry.get("id"), "name": entry.get("name"),
                "distance": d, "similarity": round(100 * (1 - d / 256), 1),
            })
    matches.sort(key=lambda m: m["distance"])
    return {"phash": ph, "duplicate": bool(matches), "threshold": threshold, "matches": matches[:8]}


# ===========================================================================
# 4 — Offline true vectorizer (vtracer)
# ===========================================================================

def vectorize_svg(raw: bytes, out_dir: Path, base_name: str = "design", mode: str = "spline") -> dict:
    t0 = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9_-]+", "-", base_name.lower())[:48] or "design"
    svg_path = out_dir / f"{safe}_{uuid.uuid4().hex}.svg"

    if not HAS_VTRACER:
        return {"ok": False, "available": False,
                "reason": "vtracer not installed — pip install vtracer (Rust wheel)"}

    tmp_png = out_dir / f".{safe}_{uuid.uuid4().hex}_trace_src.png"
    tmp_png.write_bytes(raw)
    try:
        vtracer.convert_image_to_svg_py(
            str(tmp_png), str(svg_path),
            colormode="color",
            hierarchical="stacked",
            mode=mode,                 # spline | polygon | none
            filter_speckle=4,
            color_precision=6,
            layer_difference=16,
            corner_threshold=60,
            length_threshold=4.0,
            splice_threshold=45,
            path_precision=3,
        )
    except Exception as e:
        return {"ok": False, "available": True, "reason": str(e)}
    finally:
        try:
            tmp_png.unlink()
        except Exception:
            pass

    svg = svg_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "ok": True, "available": True, "path": str(svg_path),
        "bytes": svg_path.stat().st_size,
        "paths": svg.count("<path"),
        "ms": round((time.perf_counter() - t0) * 1000),
        "svg": svg if len(svg) < 2_000_000 else "",
    }


# ===========================================================================
# 5 — Authentic vintage grunge distress knockout
# ===========================================================================

def _distress_texture(w: int, h: int, intensity: float, seed: int = 7) -> np.ndarray:
    """Procedural cracked-ink field: multi-octave value noise + fiber streaks."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    for octave in range(5):
        gh = max(2, int(h / (2 ** (6 - octave))))
        gw = max(2, int(w / (2 ** (6 - octave))))
        grid = rng.random((gh, gw)).astype(np.float32)
        layer = np.asarray(
            Image.fromarray((grid * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC),
            dtype=np.float32,
        ) / 255.0
        acc += layer * amp
        total += amp
        amp *= 0.55
    acc /= max(1e-6, total)

    # horizontal fiber striations (classic screen-print wear)
    fibers = rng.random((h, max(8, w // 3))).astype(np.float32)
    fibers = np.asarray(
        Image.fromarray((fibers * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    field = acc * 0.78 + fibers * 0.22

    # intensity drives the knockout threshold curve
    k = 0.30 + 0.55 * float(np.clip(intensity, 0.0, 1.0))
    knock = np.clip((field - (1.0 - k)) / max(1e-6, k), 0.0, 1.0)
    return (knock * 255.0).astype(np.uint8)


def apply_distress_knockout(raw: bytes, intensity: float = 0.45, seed: int = 7) -> bytes:
    """alpha_out = min(alpha_in, 255 - distress_texture)."""
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    w, h = img.size
    texture = _distress_texture(w, h, intensity, seed)

    if HAS_VIPS:
        try:
            arr = np.ascontiguousarray(np.asarray(img))
            vi = pyvips.Image.new_from_memory(arr.tobytes(), w, h, 4, "uchar")
            tex = pyvips.Image.new_from_memory(np.ascontiguousarray(texture).tobytes(), w, h, 1, "uchar")
            alpha = vi.extract_band(3)
            knocked = (255 - tex).copy(interpretation="b-w")
            new_alpha = alpha.math2(knocked, "min") if hasattr(alpha, "math2") else (alpha < knocked).ifthenelse(alpha, knocked)
            out = vi.extract_band(0, n=3).bandjoin(new_alpha)
            mem = out.write_to_memory()
            res = np.frombuffer(mem, dtype=np.uint8).reshape(h, w, 4)
            buf = io.BytesIO()
            Image.fromarray(res, "RGBA").save(buf, "PNG", optimize=True)
            return buf.getvalue()
        except Exception:
            pass

    arr = np.asarray(img).copy()
    arr[..., 3] = np.minimum(arr[..., 3], 255 - texture)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ===========================================================================
# 6 — Auto dark & light apparel splitter (CIEDE2000)
# ===========================================================================

def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T
    white = np.array([0.95047, 1.0, 1.08883])
    xyz = xyz / white
    e, k = 216 / 24389, 24389 / 27
    f = np.where(xyz > e, np.cbrt(xyz), (k * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) if Cb > 0 else 0.0
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (b1 or a1p) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (b2 or a2p) else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = 0.0
    if C1p * C2p != 0:
        diff = h2p - h1p
        dhp = diff - 360 if diff > 180 else diff + 360 if diff < -180 else diff
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2)
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    else:
        s = h1p + h2p
        hbp = (s + 360) / 2 if abs(h1p - h2p) > 180 and s < 360 else (s - 360) / 2 if abs(h1p - h2p) > 180 else s / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dTheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp > 0 else 0.0
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dTheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


GARMENTS = {"black": (0, 0, 0), "navy": (21, 31, 48), "dark_heather": (40, 40, 40)}
VINTAGE_CREAM = (232, 226, 210)


def analyze_contrast(raw: bytes) -> dict:
    """Perceptual contrast of the design's edge + dominant ink vs dark garments."""
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    small = img.resize((min(256, img.width), min(256, img.height)), Image.LANCZOS)
    arr = np.asarray(small)
    alpha = arr[..., 3]
    vis = alpha > 40
    if not vis.any():
        return {"lowContrast": False, "reason": "empty artwork", "scores": {}}

    # edge band = visible pixels adjacent to transparency (what reads on fabric)
    a = vis.astype(np.uint8)
    shifted = (
        np.roll(a, 1, 0) & np.roll(a, -1, 0) & np.roll(a, 1, 1) & np.roll(a, -1, 1)
    )
    edge = vis & (shifted == 0)
    pool = arr[edge][:, :3] if edge.sum() > 50 else arr[vis][:, :3]

    lab_pool = _srgb_to_lab(pool.reshape(-1, 3))
    mean_lab = lab_pool.mean(axis=0)
    dom = pool.reshape(-1, 3).mean(axis=0).astype(int)

    scores: dict[str, float] = {}
    for name, rgb in GARMENTS.items():
        g_lab = _srgb_to_lab(np.array(rgb, dtype=np.float64).reshape(1, 3))[0]
        scores[name] = round(ciede2000(mean_lab, g_lab), 2)

    worst = min(scores.values())
    return {
        "lowContrast": worst < 25.0,
        "worstDelta": worst,
        "scores": scores,
        "dominant": "#%02x%02x%02x" % tuple(int(v) for v in dom),
        "meanL": round(float(mean_lab[0]), 1),
    }


def generate_dark_light_split(
    raw: bytes, out_dir: Path, base_name: str = "design", strategy: str = "auto"
) -> dict:
    """
    Outputs design_light_garments.png (original) and, when contrast is poor,
    design_dark_garments.png with a 3px outer stroke or cream ink conversion.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9_-]+", "-", base_name.lower())[:48] or "design"
    report = analyze_contrast(raw)

    _, light = output_store.save_raw(raw, out_dir, f"{base_name}-light-garments", {"source": "prepare", "style": "Light garment"})
    out = {"analysis": report, "light": light}

    needs = report.get("lowContrast") or strategy in {"stroke", "cream"}
    if not needs:
        out["dark"] = None
        out["applied"] = "none"
        return out

    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    arr = np.asarray(img).copy()
    alpha = arr[..., 3]
    use_cream = strategy == "cream" or (strategy == "auto" and float(report.get("meanL", 50)) < 42)

    if use_cream:
        lum = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]) / 255.0
        dark_ink = (lum < 0.45) & (alpha > 20)
        for ch, val in enumerate(VINTAGE_CREAM):
            band = arr[..., ch]
            band[dark_ink] = np.clip(val - (lum[dark_ink] * 46), 0, 255).astype(np.uint8)
            arr[..., ch] = band
        applied = "vintage cream ink #E8E2D2"
        result = Image.fromarray(arr, "RGBA")
    else:
        # clean 3px outer stroke (scaled with canvas) in cream
        base = Image.fromarray(arr, "RGBA")
        radius = max(3, int(round(min(img.width, img.height) * 0.0014)) * 3)
        a_f = (alpha > 24).astype(np.float32)
        grown = a_f.copy()
        step = np.zeros_like(a_f)
        for _ in range(radius):
            step[:] = grown
            step[1:, :] = np.maximum(step[1:, :], grown[:-1, :])
            step[:-1, :] = np.maximum(step[:-1, :], grown[1:, :])
            step[:, 1:] = np.maximum(step[:, 1:], grown[:, :-1])
            step[:, :-1] = np.maximum(step[:, :-1], grown[:, 1:])
            grown = step.copy()
        stroke = np.zeros_like(arr)
        stroke[..., 0], stroke[..., 1], stroke[..., 2] = VINTAGE_CREAM
        stroke[..., 3] = (grown * 255).astype(np.uint8)
        layer = Image.fromarray(stroke, "RGBA")
        layer.alpha_composite(base)
        result = layer
        applied = f"{radius}px outer stroke"

    _, dark = output_store.save_image(result, out_dir, f"{base_name}-dark-garments", {"source": "prepare", "style": "Dark garment"})
    out["dark"] = dark
    out["applied"] = applied
    return out


CAPABILITIES = {
    "libvips": HAS_VIPS,
    "tesseract": HAS_TESS,
    "vtracer": HAS_VTRACER,
}
