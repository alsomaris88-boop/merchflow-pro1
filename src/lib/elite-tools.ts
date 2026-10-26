/**
 * MerchFlow Pro — Elite design tools, browser-side implementations.
 * Mirrors desktop/worker/elite_tools.py so every feature works with the local
 * engine offline. Pure print files only (PNG @300DPI / SVG) — zero mockups.
 */

/* =========================== 1 · PolyCanvas =========================== */

export interface ProductSpec {
  key: string;
  label: string;
  w: number;
  h: number;
  mode: "top" | "center";
  topMargin?: number;
  bottomSafe?: number;
  circleMask?: number;
  fill: number;
  note: string;
}

export const PRODUCTS: ProductSpec[] = [
  { key: "tshirt", label: "T-Shirt / Long Sleeve", w: 4500, h: 5400, mode: "top", topMargin: 180, fill: 0.92, note: "180px collar margin" },
  { key: "hoodie", label: "Hoodie / Sweatshirt", w: 4500, h: 4050, mode: "top", topMargin: 300, bottomSafe: 800, fill: 0.88, note: "800px pocket safe zone" },
  { key: "popsocket", label: "PopSockets Grip", w: 2400, h: 2400, mode: "center", circleMask: 1000, fill: 0.72, note: "circular mask r=1000px" },
  { key: "tote", label: "Tote Bag", w: 2925, h: 2925, mode: "center", fill: 0.8, note: "square scaling" },
  { key: "pillow", label: "Throw Pillow", w: 4500, h: 4500, mode: "center", fill: 0.84, note: "centered composition" },
];

export function tightBBox(
  data: Uint8ClampedArray,
  w: number,
  h: number,
  threshold = 12
): { x0: number; y0: number; x1: number; y1: number } {
  let x0 = w, y0 = h, x1 = 0, y1 = 0;
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let x = 0; x < w; x++) {
      if (data[(row + x) * 4 + 3] > threshold) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < x0 || y1 < y0) return { x0: 0, y0: 0, x1: w, y1: h };
  return { x0, y0, x1: x1 + 1, y1: y1 + 1 };
}

/** Crop a source canvas to its tight non-transparent bounds. */
export function cropToArt(src: HTMLCanvasElement): HTMLCanvasElement {
  const ctx = src.getContext("2d", { willReadFrequently: true })!;
  const { data } = ctx.getImageData(0, 0, src.width, src.height);
  const b = tightBBox(data, src.width, src.height);
  const out = document.createElement("canvas");
  out.width = Math.max(1, b.x1 - b.x0);
  out.height = Math.max(1, b.y1 - b.y0);
  out.getContext("2d")!.drawImage(src, b.x0, b.y0, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}

export function renderProductCanvas(art: HTMLCanvasElement, spec: ProductSpec): HTMLCanvasElement {
  const out = document.createElement("canvas");
  out.width = spec.w;
  out.height = spec.h;
  const ctx = out.getContext("2d")!;
  ctx.imageSmoothingQuality = "high";

  const top = spec.topMargin ?? 0;
  const bottomSafe = spec.bottomSafe ?? 0;
  const availW = spec.w * spec.fill;
  const availH =
    spec.mode === "top" ? spec.h - top - bottomSafe - spec.h * 0.04 : spec.h * spec.fill;
  const scale = Math.min(availW / art.width, availH / art.height);
  const dw = Math.max(2, Math.round(art.width * scale));
  const dh = Math.max(2, Math.round(art.height * scale));
  const dx = Math.round((spec.w - dw) / 2);
  const dy = spec.mode === "top" ? top : Math.round((spec.h - dh) / 2);
  ctx.drawImage(art, dx, dy, dw, dh);

  if (spec.circleMask) {
    const id = ctx.getImageData(0, 0, spec.w, spec.h);
    const d = id.data;
    const c = (spec.w - 1) / 2;
    const r = spec.circleMask;
    for (let y = 0; y < spec.h; y++) {
      for (let x = 0; x < spec.w; x++) {
        const dist = Math.hypot(x - c, y - c);
        const m = Math.max(0, Math.min(1, (r - dist) / 2 + 0.5));
        if (m < 1) {
          const i = (y * spec.w + x) * 4 + 3;
          d[i] = Math.round(d[i] * m);
        }
      }
    }
    ctx.putImageData(id, 0, 0);
  }
  return out;
}

/* ====================== 3 · pHash anti-spam sentry ====================== */

function dct1d(vec: Float64Array): Float64Array {
  const n = vec.length;
  const out = new Float64Array(n);
  for (let k = 0; k < n; k++) {
    let s = 0;
    for (let i = 0; i < n; i++) s += vec[i] * Math.cos((Math.PI * (2 * i + 1) * k) / (2 * n));
    out[k] = k === 0 ? s / Math.SQRT2 : s;
  }
  return out;
}

/** 256-bit DCT perceptual hash (16×16 of a 64×64 DCT), alpha flattened to white. */
export function computePHash(src: HTMLCanvasElement | HTMLImageElement): string {
  const N = 64;
  const SIDE = 16;
  const c = document.createElement("canvas");
  c.width = N;
  c.height = N;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, N, N);
  ctx.drawImage(src, 0, 0, N, N);
  const { data } = ctx.getImageData(0, 0, N, N);

  const g: Float64Array[] = [];
  for (let y = 0; y < N; y++) {
    const row = new Float64Array(N);
    for (let x = 0; x < N; x++) {
      const i = (y * N + x) * 4;
      row[x] = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    }
    g.push(row);
  }
  const rows = g.map(dct1d);
  const cols: Float64Array[] = [];
  for (let x = 0; x < N; x++) {
    const col = new Float64Array(N);
    for (let y = 0; y < N; y++) col[y] = rows[y][x];
    cols.push(dct1d(col));
  }
  const vals: number[] = [];
  for (let y = 0; y < SIDE; y++) for (let x = 0; x < SIDE; x++) vals.push(cols[x][y]);
  const sorted = [...vals].slice(1).sort((a, b) => a - b);
  const med = sorted[Math.floor(sorted.length / 2)];
  let hex = "";
  for (let i = 0; i < vals.length; i += 4) {
    let nib = 0;
    for (let k = 0; k < 4; k++) nib = (nib << 1) | (vals[i + k] > med ? 1 : 0);
    hex += nib.toString(16);
  }
  return hex;
}

const POP = new Uint8Array(16);
for (let i = 0; i < 16; i++) POP[i] = (i & 1) + ((i >> 1) & 1) + ((i >> 2) & 1) + ((i >> 3) & 1);

export function hamming(a: string, b: string): number {
  if (!a || !b || a.length !== b.length) return 10000;
  let d = 0;
  for (let i = 0; i < a.length; i++) d += POP[parseInt(a[i], 16) ^ parseInt(b[i], 16)];
  return d;
}

export interface DupeMatch {
  id: string;
  name: string;
  distance: number;
  similarity: number;
}

export function findDuplicates(
  phash: string,
  catalog: { id: string; name: string; phash?: string }[],
  threshold = 18
): DupeMatch[] {
  const out: DupeMatch[] = [];
  for (const e of catalog) {
    if (!e.phash) continue;
    const d = hamming(phash, e.phash);
    if (d <= threshold) {
      out.push({ id: e.id, name: e.name, distance: d, similarity: Math.round((1 - d / 256) * 1000) / 10 });
    }
  }
  return out.sort((a, b) => a.distance - b.distance).slice(0, 8);
}

/* ==================== 4 · Vectorizer (marching squares) ==================== */

/**
 * Offline contour tracer: alpha → closed polygon paths → SVG.
 * When the local engine is up, vtracer (Rust) produces the production file;
 * this is the in-browser equivalent so the feature always works.
 */
export function vectorizeToSvg(src: HTMLCanvasElement, opts?: { step?: number; threshold?: number }): string {
  const step = Math.max(1, opts?.step ?? Math.max(1, Math.round(Math.max(src.width, src.height) / 420)));
  const threshold = opts?.threshold ?? 110;
  const ctx = src.getContext("2d", { willReadFrequently: true })!;
  const { data } = ctx.getImageData(0, 0, src.width, src.height);
  const W = Math.floor(src.width / step);
  const H = Math.floor(src.height / step);

  const solid = (gx: number, gy: number): boolean => {
    if (gx < 0 || gy < 0 || gx >= W || gy >= H) return false;
    const x = Math.min(src.width - 1, gx * step);
    const y = Math.min(src.height - 1, gy * step);
    return data[(y * src.width + x) * 4 + 3] >= threshold;
  };

  // average ink color for the fill
  let r = 0, g = 0, b = 0, n = 0;
  for (let i = 0; i < data.length; i += 4 * 97) {
    if (data[i + 3] > 128) { r += data[i]; g += data[i + 1]; b += data[i + 2]; n++; }
  }
  const fill = n ? `rgb(${Math.round(r / n)},${Math.round(g / n)},${Math.round(b / n)})` : "#111111";

  // boundary edges between solid and empty cells
  type Pt = [number, number];
  const edges = new Map<string, Pt[]>();
  const key = (p: Pt) => `${p[0]},${p[1]}`;
  const addEdge = (a: Pt, bb: Pt) => {
    const k = key(a);
    const list = edges.get(k);
    if (list) list.push(bb);
    else edges.set(k, [bb]);
  };
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (!solid(x, y)) continue;
      if (!solid(x, y - 1)) addEdge([x, y], [x + 1, y]);
      if (!solid(x + 1, y)) addEdge([x + 1, y], [x + 1, y + 1]);
      if (!solid(x, y + 1)) addEdge([x + 1, y + 1], [x, y + 1]);
      if (!solid(x - 1, y)) addEdge([x, y + 1], [x, y]);
    }
  }

  const paths: string[] = [];
  const used = new Set<string>();
  for (const [startKey, outs] of edges) {
    for (const first of outs) {
      const ek = `${startKey}>${key(first)}`;
      if (used.has(ek)) continue;
      const start: Pt = startKey.split(",").map(Number) as Pt;
      const loop: Pt[] = [start];
      let cur = first;
      let prevKey = startKey;
      used.add(ek);
      for (let guard = 0; guard < 200000; guard++) {
        loop.push(cur);
        if (key(cur) === startKey) break;
        const nexts = edges.get(key(cur));
        if (!nexts || !nexts.length) break;
        let picked: Pt | null = null;
        for (const cand of nexts) {
          const ck = `${key(cur)}>${key(cand)}`;
          if (!used.has(ck) && key(cand) !== prevKey) { picked = cand; used.add(ck); break; }
        }
        if (!picked) break;
        prevKey = key(cur);
        cur = picked;
      }
      if (loop.length < 4) continue;

      // Douglas-Peucker simplify
      const simplified = simplify(loop, 0.62);
      if (simplified.length < 3) continue;
      const d = simplified
        .map((p, i) => `${i === 0 ? "M" : "L"}${(p[0] * step).toFixed(1)} ${(p[1] * step).toFixed(1)}`)
        .join(" ");
      paths.push(`${d}Z`);
    }
  }

  const body = paths.length
    ? `<path fill="${fill}" fill-rule="evenodd" d="${paths.join(" ")}"/>`
    : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${src.width} ${src.height}" width="${src.width}" height="${src.height}" shape-rendering="geometricPrecision">${body}</svg>`;
}

function simplify(pts: [number, number][], eps: number): [number, number][] {
  if (pts.length < 3) return pts;
  const keep = new Uint8Array(pts.length);
  keep[0] = keep[pts.length - 1] = 1;
  const stack: [number, number][] = [[0, pts.length - 1]];
  while (stack.length) {
    const [s, e] = stack.pop()!;
    let maxD = 0;
    let idx = -1;
    const [x1, y1] = pts[s];
    const [x2, y2] = pts[e];
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    for (let i = s + 1; i < e; i++) {
      const d = Math.abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / len;
      if (d > maxD) { maxD = d; idx = i; }
    }
    if (maxD > eps && idx > 0) {
      keep[idx] = 1;
      stack.push([s, idx], [idx, e]);
    }
  }
  return pts.filter((_, i) => keep[i]);
}

/* =================== 5 · Vintage distress knockout =================== */

function valueNoise(w: number, h: number, cells: number, rand: () => number): Float32Array {
  const gw = Math.max(2, Math.round(w / cells));
  const gh = Math.max(2, Math.round(h / cells));
  const grid = new Float32Array(gw * gh);
  for (let i = 0; i < grid.length; i++) grid[i] = rand();
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    const fy = (y / h) * (gh - 1);
    const y0 = Math.floor(fy);
    const ty = fy - y0;
    const y1 = Math.min(gh - 1, y0 + 1);
    for (let x = 0; x < w; x++) {
      const fx = (x / w) * (gw - 1);
      const x0 = Math.floor(fx);
      const tx = fx - x0;
      const x1 = Math.min(gw - 1, x0 + 1);
      const a = grid[y0 * gw + x0], b = grid[y0 * gw + x1];
      const c = grid[y1 * gw + x0], d = grid[y1 * gw + x1];
      const sx = tx * tx * (3 - 2 * tx);
      const sy = ty * ty * (3 - 2 * ty);
      out[y * w + x] = (a + (b - a) * sx) * (1 - sy) + (c + (d - c) * sx) * sy;
    }
  }
  return out;
}

function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** alpha_out = min(alpha_in, 255 - distress_texture) */
export function applyDistressKnockout(src: HTMLCanvasElement, intensity = 0.45, seed = 7): HTMLCanvasElement {
  const w = src.width, h = src.height;
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(src, 0, 0);
  const id = ctx.getImageData(0, 0, w, h);
  const d = id.data;

  const rand = mulberry32(seed);
  const n1 = valueNoise(w, h, 64, rand);
  const n2 = valueNoise(w, h, 22, rand);
  const n3 = valueNoise(w, h, 7, rand);
  const fibers = valueNoise(w, h, 3, rand);

  const k = 0.3 + 0.55 * Math.max(0, Math.min(1, intensity));
  for (let i = 0, p = 0; i < d.length; i += 4, p++) {
    if (d[i + 3] === 0) continue;
    const field = (n1[p] * 0.45 + n2[p] * 0.28 + n3[p] * 0.12 + fibers[p] * 0.15);
    const knock = Math.max(0, Math.min(1, (field - (1 - k)) / k));
    const limit = 255 - Math.round(knock * 255);
    if (limit < d[i + 3]) d[i + 3] = limit;
  }
  ctx.putImageData(id, 0, 0);
  return out;
}

/* ============== 6 · Dark/Light splitter (CIEDE2000) ============== */

function srgbToLab(r: number, g: number, b: number): [number, number, number] {
  const f = (v: number) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  const R = f(r), G = f(g), B = f(b);
  let X = (R * 0.4124 + G * 0.3576 + B * 0.1805) / 0.95047;
  let Y = R * 0.2126 + G * 0.7152 + B * 0.0722;
  let Z = (R * 0.0193 + G * 0.1192 + B * 0.9505) / 1.08883;
  const e = 216 / 24389, k = 24389 / 27;
  const t = (v: number) => (v > e ? Math.cbrt(v) : (k * v + 16) / 116);
  X = t(X); Y = t(Y); Z = t(Z);
  return [116 * Y - 16, 500 * (X - Y), 200 * (Y - Z)];
}

export function ciede2000(l1: [number, number, number], l2: [number, number, number]): number {
  const [L1, a1, b1] = l1;
  const [L2, a2, b2] = l2;
  const C1 = Math.hypot(a1, b1), C2 = Math.hypot(a2, b2);
  const Cb = (C1 + C2) / 2;
  const G = Cb > 0 ? 0.5 * (1 - Math.sqrt(Math.pow(Cb, 7) / (Math.pow(Cb, 7) + Math.pow(25, 7)))) : 0;
  const a1p = (1 + G) * a1, a2p = (1 + G) * a2;
  const C1p = Math.hypot(a1p, b1), C2p = Math.hypot(a2p, b2);
  const h1p = ((Math.atan2(b1, a1p) * 180) / Math.PI + 360) % 360;
  const h2p = ((Math.atan2(b2, a2p) * 180) / Math.PI + 360) % 360;
  const dLp = L2 - L1;
  const dCp = C2p - C1p;
  let dhp = 0;
  if (C1p * C2p !== 0) {
    const diff = h2p - h1p;
    dhp = diff > 180 ? diff - 360 : diff < -180 ? diff + 360 : diff;
  }
  const dHp = 2 * Math.sqrt(C1p * C2p) * Math.sin((dhp * Math.PI) / 360);
  const Lbp = (L1 + L2) / 2;
  const Cbp = (C1p + C2p) / 2;
  let hbp: number;
  const s = h1p + h2p;
  if (C1p * C2p === 0) hbp = s;
  else if (Math.abs(h1p - h2p) > 180) hbp = s < 360 ? (s + 360) / 2 : (s - 360) / 2;
  else hbp = s / 2;
  const rad = (d: number) => (d * Math.PI) / 180;
  const T = 1 - 0.17 * Math.cos(rad(hbp - 30)) + 0.24 * Math.cos(rad(2 * hbp))
    + 0.32 * Math.cos(rad(3 * hbp + 6)) - 0.2 * Math.cos(rad(4 * hbp - 63));
  const dTheta = 30 * Math.exp(-Math.pow((hbp - 275) / 25, 2));
  const Rc = Cbp > 0 ? 2 * Math.sqrt(Math.pow(Cbp, 7) / (Math.pow(Cbp, 7) + Math.pow(25, 7))) : 0;
  const Sl = 1 + (0.015 * Math.pow(Lbp - 50, 2)) / Math.sqrt(20 + Math.pow(Lbp - 50, 2));
  const Sc = 1 + 0.045 * Cbp;
  const Sh = 1 + 0.015 * Cbp * T;
  const Rt = -Math.sin(rad(2 * dTheta)) * Rc;
  return Math.sqrt(
    Math.pow(dLp / Sl, 2) + Math.pow(dCp / Sc, 2) + Math.pow(dHp / Sh, 2) + Rt * (dCp / Sc) * (dHp / Sh)
  );
}

export const DARK_GARMENTS: Record<string, [number, number, number]> = {
  black: [0, 0, 0],
  navy: [21, 31, 48],
  dark_heather: [40, 40, 40],
};

export const VINTAGE_CREAM: [number, number, number] = [232, 226, 210];

export interface ContrastReport {
  lowContrast: boolean;
  worstDelta: number;
  scores: Record<string, number>;
  dominant: string;
  meanL: number;
}

export function analyzeContrast(src: HTMLCanvasElement): ContrastReport {
  const S = 256;
  const c = document.createElement("canvas");
  c.width = Math.min(S, src.width);
  c.height = Math.min(S, src.height);
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(src, 0, 0, c.width, c.height);
  const { data } = ctx.getImageData(0, 0, c.width, c.height);
  const w = c.width, h = c.height;

  const vis = (x: number, y: number) =>
    x >= 0 && y >= 0 && x < w && y < h && data[(y * w + x) * 4 + 3] > 40;

  let r = 0, g = 0, b = 0, n = 0;
  let er = 0, eg = 0, eb = 0, en = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (data[i + 3] <= 40) continue;
      r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
      if (!vis(x - 1, y) || !vis(x + 1, y) || !vis(x, y - 1) || !vis(x, y + 1)) {
        er += data[i]; eg += data[i + 1]; eb += data[i + 2]; en++;
      }
    }
  }
  if (!n) return { lowContrast: false, worstDelta: 100, scores: {}, dominant: "#000000", meanL: 50 };
  const use = en > 50 ? { r: er / en, g: eg / en, b: eb / en } : { r: r / n, g: g / n, b: b / n };
  const lab = srgbToLab(use.r, use.g, use.b);

  const scores: Record<string, number> = {};
  for (const [name, rgb] of Object.entries(DARK_GARMENTS)) {
    scores[name] = Math.round(ciede2000(lab, srgbToLab(rgb[0], rgb[1], rgb[2])) * 100) / 100;
  }
  const worst = Math.min(...Object.values(scores));
  const hex =
    "#" + [r / n, g / n, b / n].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  return {
    lowContrast: worst < 25,
    worstDelta: worst,
    scores,
    dominant: hex,
    meanL: Math.round(lab[0] * 10) / 10,
  };
}

/** Dark-garment variant: cream ink conversion or clean outer stroke. */
export function buildDarkVariant(
  src: HTMLCanvasElement,
  strategy: "auto" | "stroke" | "cream" = "auto",
  report?: ContrastReport
): { canvas: HTMLCanvasElement; applied: string } {
  const rep = report ?? analyzeContrast(src);
  const useCream = strategy === "cream" || (strategy === "auto" && rep.meanL < 42);
  const w = src.width, h = src.height;
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d", { willReadFrequently: true })!;

  if (useCream) {
    ctx.drawImage(src, 0, 0);
    const id = ctx.getImageData(0, 0, w, h);
    const d = id.data;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] <= 20) continue;
      const lum = (0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]) / 255;
      if (lum < 0.45) {
        d[i] = Math.max(0, VINTAGE_CREAM[0] - lum * 46);
        d[i + 1] = Math.max(0, VINTAGE_CREAM[1] - lum * 46);
        d[i + 2] = Math.max(0, VINTAGE_CREAM[2] - lum * 46);
      }
    }
    ctx.putImageData(id, 0, 0);
    return { canvas: out, applied: "vintage cream ink #E8E2D2" };
  }

  const radius = Math.max(3, Math.round(Math.min(w, h) * 0.0014) * 3);
  // silhouette → cream stroke underlay
  const sil = document.createElement("canvas");
  sil.width = w;
  sil.height = h;
  const sctx = sil.getContext("2d")!;
  sctx.drawImage(src, 0, 0);
  sctx.globalCompositeOperation = "source-in";
  sctx.fillStyle = `rgb(${VINTAGE_CREAM.join(",")})`;
  sctx.fillRect(0, 0, w, h);

  const steps = 16;
  for (let i = 0; i < steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    ctx.drawImage(sil, Math.cos(a) * radius, Math.sin(a) * radius);
  }
  ctx.drawImage(src, 0, 0);
  return { canvas: out, applied: `${radius}px outer stroke` };
}

/* ------------------------------- helpers ------------------------------- */

export function canvasToBlob(c: HTMLCanvasElement, type = "image/png"): Promise<Blob> {
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("toBlob failed"))), type));
}

export async function imageToCanvas(url: string): Promise<HTMLCanvasElement> {
  const img = await new Promise<HTMLImageElement>((res, rej) => {
    const i = new Image();
    i.onload = () => res(i);
    i.onerror = rej;
    i.src = url;
  });
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  c.getContext("2d")!.drawImage(img, 0, 0);
  return c;
}

export function downloadBlob(blob: Blob, filename: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
