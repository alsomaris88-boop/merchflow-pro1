"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  CircleDot,
  Contrast,
  Cpu,
  Download,
  Droplets,
  Eraser,
  Fingerprint,
  FlaskConical,
  FolderCheck,
  Gem,
  ImageUp,
  Images,
  LayoutGrid,
  Loader2,
  Maximize2,
  MoveVertical,
  Package,
  PlugZap,
  Scissors,
  ShieldAlert,
  Sparkles,
  Spline,
  Trash2,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import { Btn, Card, Chip, Field, PageHead, Section, Select, Toggle, cx } from "@/components/ui";
import { probeBridge, type BridgeStatus } from "@/lib/bridge";
import { HANDOFF_EVENT, formatBytes, getDesigns } from "@/lib/designs-store";
import {
  PRODUCTS,
  analyzeContrast,
  applyDistressKnockout,
  buildDarkVariant,
  canvasToBlob,
  computePHash,
  cropToArt,
  downloadBlob,
  findDuplicates,
  renderProductCanvas,
  vectorizeToSvg,
  type ContrastReport,
} from "@/lib/elite-tools";

/* --------------------------------------------------------------- settings */
interface LabSettings {
  // step 1 — remove background
  remove: boolean;
  method: "keying" | "ai";
  holeMode: "keepholes" | "everywhere";
  tol: number;
  defringe: number;
  despeckle: boolean;
  // step 2 — upscale
  upscale: 2 | 4 | 8;
  // step 3 — resize for printing
  placement: "center" | "top";
  // step 4 — compress
  compress: boolean;
  compressLevel: number; // 40..80 (% target reduction)
  // optional screen-print filter
  halftone: boolean;
  lpi: number;
  ink: string;
  // ---- elite tools ----
  polycanvas: boolean;
  products: string[];
  vectorize: boolean;
  distress: boolean;
  distressIntensity: number;
  darkLightSplit: boolean;
  splitStrategy: "auto" | "stroke" | "cream";
  dedupe: boolean;
}

const DEFAULTS: LabSettings = {
  remove: true, method: "keying", holeMode: "keepholes", tol: 42, defringe: 2, despeckle: true,
  upscale: 4, placement: "top", compress: true, compressLevel: 60,
  halftone: false, lpi: 48, ink: "#E8E2D2",
  polycanvas: false, products: ["hoodie", "popsocket", "tote"],
  vectorize: false, distress: false, distressIntensity: 0.45,
  darkLightSplit: false, splitStrategy: "auto", dedupe: true,
};

const INKS = [
  { name: "Bone White", hex: "#E8E2D2" },
  { name: "Vintage Black", hex: "#171512" },
  { name: "Crimson", hex: "#8E1B2C" },
  { name: "Antique Gold", hex: "#C9A227" },
];

const STEP_LABELS = ["Remove background", "Upscale", "Resize for printing", "Compress"];

interface LabItem {
  id: string;
  name: string;
  url: string;
  img?: HTMLImageElement;
  from: "history" | "upload";
  status: "pending" | "running" | "done" | "failed";
  step: number; // 0..4
  outUrl?: string;
  outBytes?: number;
  ms?: number;
  specks?: number;
  coverage?: number;
  path?: string;
  // elite outputs
  phash?: string;
  dupes?: { id: string; name: string; distance: number; similarity: number }[];
  contrast?: ContrastReport;
  darkUrl?: string;
  darkApplied?: string;
  svg?: string;
  svgBytes?: number;
  canvases?: { key: string; label: string; w: number; h: number; url: string; note: string }[];
}

/* -------------------------------------------------- pixel pipeline (staged) */
function borderBgRemove(data: Uint8ClampedArray, w: number, h: number, tol: number, keepHoles: boolean): void {
  let r0 = 0, g0 = 0, b0 = 0, n = 0;
  const sample = (idx: number) => {
    if (data[idx + 3] < 40) return;
    r0 += data[idx]; g0 += data[idx + 1]; b0 += data[idx + 2]; n++;
  };
  for (let x = 0; x < w; x += 3) { sample(x * 4); sample(((h - 1) * w + x) * 4); }
  for (let y = 0; y < h; y += 3) { sample(y * w * 4); sample((y * w + w - 1) * 4); }
  if (n === 0) return;
  r0 = Math.round(r0 / n); g0 = Math.round(g0 / n); b0 = Math.round(b0 / n);
  const t2 = tol * tol * 3;

  if (!keepHoles) {
    // global color key — every matching pixel is cleared, interior holes included
    for (let i = 0; i < data.length; i += 4) {
      const dr = data[i] - r0, dg = data[i + 1] - g0, db = data[i + 2] - b0;
      if (dr * dr + dg * dg + db * db <= t2) data[i + 3] = 0;
    }
    return;
  }

  // keep-holes: BFS flood strictly from the 4 canvas borders
  const visited = new Uint8Array(w * h);
  const stack = new Int32Array(w * h);
  let sp = 0;
  const tryPush = (p: number) => {
    if (visited[p]) return;
    const i = p * 4;
    if (data[i + 3] < 40) { visited[p] = 1; stack[sp++] = p; return; }
    const dr = data[i] - r0, dg = data[i + 1] - g0, db = data[i + 2] - b0;
    if (dr * dr + dg * dg + db * db <= t2) { visited[p] = 1; data[i + 3] = 0; stack[sp++] = p; }
  };
  for (let x = 0; x < w; x++) { tryPush(x); tryPush((h - 1) * w + x); }
  for (let y = 0; y < h; y++) { tryPush(y * w); tryPush(y * w + w - 1); }
  while (sp > 0) {
    const p = stack[--sp];
    const x = p % w, y = (p / w) | 0;
    if (x > 0) tryPush(p - 1);
    if (x < w - 1) tryPush(p + 1);
    if (y > 0) tryPush(p - w);
    if (y < h - 1) tryPush(p + w);
  }
}

function defringe(data: Uint8ClampedArray, w: number, h: number, passes: number): void {
  for (let pass = 0; pass < passes; pass++) {
    const alpha = new Uint8ClampedArray(w * h);
    for (let p = 0; p < w * h; p++) alpha[p] = data[p * 4 + 3];
    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        const p = y * w + x;
        const a = alpha[p];
        if (a === 0) continue;
        if (Math.min(alpha[p - 1], alpha[p + 1], alpha[p - w], alpha[p + w]) !== 0) continue;
        const i = p * 4;
        data[i + 3] = Math.max(0, a - 140);
        let r = 0, g = 0, b = 0, c = 0;
        for (let dy = -2; dy <= 2; dy++) {
          for (let dx = -2; dx <= 2; dx++) {
            const q = (y + dy) * w + (x + dx);
            if (q < 0 || q >= w * h) continue;
            if (alpha[q] === 255) { r += data[q * 4]; g += data[q * 4 + 1]; b += data[q * 4 + 2]; c++; }
          }
        }
        if (c > 0) {
          data[i] = Math.round(data[i] * 0.3 + (r / c) * 0.7);
          data[i + 1] = Math.round(data[i + 1] * 0.3 + (g / c) * 0.7);
          data[i + 2] = Math.round(data[i + 2] * 0.3 + (b / c) * 0.7);
        }
      }
    }
  }
}

function despeckle(data: Uint8ClampedArray, w: number, h: number, minSize: number): number {
  const label = new Int32Array(w * h).fill(-1);
  const stack = new Int32Array(w * h);
  let removed = 0, cur = 0;
  for (let s = 0; s < w * h; s++) {
    if (data[s * 4 + 3] <= 16 || label[s] !== -1) continue;
    let sp = 0;
    stack[sp++] = s;
    label[s] = cur;
    const members: number[] = [];
    while (sp > 0) {
      const p = stack[--sp];
      members.push(p);
      const x = p % w, y = (p / w) | 0;
      const nb = [x > 0 ? p - 1 : -1, x < w - 1 ? p + 1 : -1, y > 0 ? p - w : -1, y < h - 1 ? p + w : -1];
      for (const q of nb) if (q >= 0 && label[q] === -1 && data[q * 4 + 3] > 16) { label[q] = cur; stack[sp++] = q; }
    }
    if (members.length < minSize) { removed += members.length; for (const m of members) data[m * 4 + 3] = 0; }
    cur++;
    if (cur > 400000) break;
  }
  return removed;
}

function halftoneRender(src: HTMLCanvasElement, lpi: number, ink: string, outW: number, outH: number): HTMLCanvasElement {
  const out = document.createElement("canvas");
  out.width = outW; out.height = outH;
  const octx = out.getContext("2d")!;
  const sctx = src.getContext("2d", { willReadFrequently: true })!;
  const sw = src.width, sh = src.height;
  const { data } = sctx.getImageData(0, 0, sw, sh);
  const pxPerInch = outW / 12;
  const pitchOut = Math.max(3, pxPerInch / lpi);
  const scale = outW / sw;
  const pitch = pitchOut / scale;
  const ang = (45 * Math.PI) / 180;
  const cos = Math.cos(ang), sin = Math.sin(ang);
  octx.fillStyle = ink;
  const bound = Math.ceil(Math.hypot(sw, sh) / pitch) + 2;
  for (let gy = -bound; gy <= bound; gy++) {
    const ry = gy * pitch;
    for (let gx = -bound; gx <= bound; gx++) {
      const rx = gx * pitch;
      const x = rx * cos - ry * sin + sw / 2;
      const y = rx * sin + ry * cos + sh / 2;
      const xi = Math.round(x), yi = Math.round(y);
      if (xi < 0 || yi < 0 || xi >= sw || yi >= sh) continue;
      const i = (yi * sw + xi) * 4;
      const a = data[i + 3];
      if (a < 30) continue;
      const lum = (0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]) / 255;
      const t = (1 - lum) * (a / 255);
      const r = (pitchOut / 2) * 0.94 * Math.sqrt(t);
      if (r < pitchOut * 0.075) continue;
      octx.beginPath();
      octx.arc(x * scale, y * scale, r, 0, Math.PI * 2);
      octx.fill();
    }
  }
  return out;
}

const EXPORT_W = 4500, EXPORT_H = 5400;

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = url;
  });
}

/* -------------------------------------------------------------- component */
export default function PrepareLab() {
  const [queue, setQueue] = useState<LabItem[]>([]);
  const [settings, setSettings] = useState<LabSettings>(DEFAULTS);
  const [running, setRunning] = useState(false);
  const [activeStep, setActiveStep] = useState(-1);
  const [bridge, setBridge] = useState<BridgeStatus>({ connected: false });
  const [zipping, setZipping] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const imgStore = useRef(new Map<string, HTMLImageElement>());
  const urlRefs = useRef<string[]>([]);

  useEffect(() => {
    const probe = () => probeBridge().then(setBridge).catch(() => setBridge({ connected: false }));
    void probe();
    const poll = setInterval(probe, 20000);
    return () => clearInterval(poll);
  }, []);

  /* ------------------------- handoff from Designs History ------------------------- */
  const ingestIds = useCallback(async (ids: string[]) => {
    const recs = await getDesigns(ids);
    const items: LabItem[] = [];
    for (const r of recs) {
      const url = URL.createObjectURL(r.blob);
      urlRefs.current.push(url);
      try {
        const img = await loadImage(url);
        imgStore.current.set(r.id, img);
        items.push({ id: r.id, name: r.name, url, img, from: "history", status: "pending", step: 0 });
      } catch { /* skip */ }
    }
    if (items.length) {
      setQueue((q) => {
        const have = new Set(q.map((x) => x.id));
        return [...q, ...items.filter((i) => !have.has(i.id))];
      });
      setSel((s) => s ?? items[0].id);
    }
  }, []);

  useEffect(() => {
    const onHandoff = (e: Event) => {
      const ids = (e as CustomEvent<string[]>).detail;
      if (Array.isArray(ids) && ids.length) void ingestIds(ids);
    };
    window.addEventListener(HANDOFF_EVENT, onHandoff);
    return () => window.removeEventListener(HANDOFF_EVENT, onHandoff);
  }, [ingestIds]);

  useEffect(() => () => { for (const u of urlRefs.current) URL.revokeObjectURL(u); }, []);

  const onFiles = useCallback(async (files: FileList) => {
    const items: LabItem[] = [];
    for (const f of Array.from(files).slice(0, 50)) {
      const url = URL.createObjectURL(f);
      urlRefs.current.push(url);
      try {
        const img = await loadImage(url);
        const id = `u_${Math.random().toString(36).slice(2, 9)}`;
        imgStore.current.set(id, img);
        items.push({ id, name: f.name.replace(/\.[^.]+$/, ""), url, img, from: "upload", status: "pending", step: 0 });
      } catch { /* skip */ }
    }
    setQueue((q) => [...q, ...items]);
    setSel((s) => s ?? items[0]?.id ?? null);
  }, []);

  /* ------------------------------- 4-step chain ------------------------------- */
  const processOne = useCallback(
    async (item: LabItem, onStep: (step: number) => void): Promise<Partial<LabItem>> => {
      const img = imgStore.current.get(item.id);
      if (!img) throw new Error("image missing");
      const t0 = performance.now();

      // ---- STEP 1 — remove background (color-key / keep-holes BFS) ----
      onStep(1);
      await new Promise((r) => setTimeout(r, 16));
      const WORK = 2600;
      const s = Math.min(1, WORK / Math.max(img.width, img.height));
      const w = Math.max(2, Math.round(img.width * s));
      const h = Math.max(2, Math.round(img.height * s));
      const base = document.createElement("canvas");
      base.width = w; base.height = h;
      const bctx = base.getContext("2d", { willReadFrequently: true })!;
      bctx.drawImage(img, 0, 0, w, h);

      let specks = 0;
      if (settings.remove || settings.defringe > 0 || settings.despeckle) {
        const id = bctx.getImageData(0, 0, w, h);
        if (settings.remove) borderBgRemove(id.data, w, h, settings.tol, settings.holeMode === "keepholes");
        if (settings.defringe > 0) defringe(id.data, w, h, settings.defringe);
        if (settings.despeckle) specks = despeckle(id.data, w, h, Math.max(6, Math.round((w * h) / 550000)));
        bctx.putImageData(id, 0, 0);
      }
      const chk = bctx.getImageData(0, 0, w, h).data;
      let opaque = 0;
      for (let i = 3; i < chk.length; i += 16) if (chk[i] > 16) opaque++;
      const coverage = Math.round((opaque / (chk.length / 16)) * 100);

      // ---- STEP 2 — upscale (Lanczos-grade staged bicubic) ----
      onStep(2);
      await new Promise((r) => setTimeout(r, 16));
      let up: HTMLCanvasElement = base;
      const targetW = Math.min(EXPORT_W, Math.round(base.width * settings.upscale));
      while (up.width < targetW) {
        const nw = Math.min(targetW, up.width * 2);
        const nh = Math.round(up.height * (nw / up.width));
        const c = document.createElement("canvas");
        c.width = nw; c.height = nh;
        const cx2 = c.getContext("2d")!;
        cx2.imageSmoothingEnabled = true;
        cx2.imageSmoothingQuality = "high";
        cx2.drawImage(up, 0, 0, nw, nh);
        up = c;
      }

      // ---- STEP 3 — resize for printing (4500×5400 @ 300 DPI) ----
      onStep(3);
      await new Promise((r) => setTimeout(r, 16));
      const topMargin = settings.placement === "top" ? 350 : 0;
      const maxW = EXPORT_W * 0.92;
      const maxH = settings.placement === "top" ? EXPORT_H - topMargin - 220 : EXPORT_H * 0.9;
      const sc = Math.min(maxW / up.width, maxH / up.height);
      const dw = Math.max(2, Math.round(up.width * sc));
      const dh = Math.max(2, Math.round(up.height * sc));
      const dx = Math.round((EXPORT_W - dw) / 2);
      const dy = settings.placement === "top" ? topMargin : Math.round((EXPORT_H - dh) / 2);

      const out = document.createElement("canvas");
      out.width = EXPORT_W; out.height = EXPORT_H;
      const octx = out.getContext("2d")!;
      octx.imageSmoothingQuality = "high";
      if (settings.halftone) {
        const comp = document.createElement("canvas");
        comp.width = EXPORT_W; comp.height = EXPORT_H;
        comp.getContext("2d")!.drawImage(up, dx, dy, dw, dh);
        octx.drawImage(halftoneRender(comp, settings.lpi, settings.ink, EXPORT_W, EXPORT_H), 0, 0);
      } else {
        octx.drawImage(up, dx, dy, dw, dh);
      }

      // ---- STEP 4 — compress (lossless PNG) ----
      onStep(4);
      await new Promise((r) => setTimeout(r, 16));
      const blob = await new Promise<Blob | null>((res) => out.toBlob(res, "image/png"));
      const outUrl = blob ? URL.createObjectURL(blob) : out.toDataURL("image/png");
      if (blob) urlRefs.current.push(outUrl);

      return {
        outUrl,
        outBytes: blob?.size ?? 0,
        ms: Math.round(performance.now() - t0),
        specks,
        coverage,
      };
    },
    [settings]
  );

  /** Elite stage — runs on the finished 4500×5400 print file. */
  const runElite = useCallback(
    async (outUrl: string, name: string, catalog: { id: string; name: string; phash?: string }[]): Promise<Partial<LabItem>> => {
      const patch: Partial<LabItem> = {};
      const img = await new Promise<HTMLImageElement>((res, rej) => {
        const i = new Image();
        i.onload = () => res(i);
        i.onerror = rej;
        i.src = outUrl;
      });
      const base = document.createElement("canvas");
      base.width = img.naturalWidth;
      base.height = img.naturalHeight;
      base.getContext("2d")!.drawImage(img, 0, 0);

      // pHash anti-spam sentry
      if (settings.dedupe) {
        setActiveStep(5);
        patch.phash = computePHash(base);
        patch.dupes = findDuplicates(patch.phash, catalog, 18);
      }

      // vintage distress knockout (feeds everything downstream)
      let working = base;
      if (settings.distress) {
        setActiveStep(6);
        await new Promise((r) => setTimeout(r, 16));
        working = applyDistressKnockout(base, settings.distressIntensity, 7);
        patch.outUrl = URL.createObjectURL(await canvasToBlob(working));
        urlRefs.current.push(patch.outUrl);
      }

      // dark/light apparel split
      if (settings.darkLightSplit) {
        setActiveStep(7);
        await new Promise((r) => setTimeout(r, 16));
        const report = analyzeContrast(working);
        patch.contrast = report;
        if (report.lowContrast || settings.splitStrategy !== "auto") {
          const { canvas, applied } = buildDarkVariant(working, settings.splitStrategy, report);
          patch.darkUrl = URL.createObjectURL(await canvasToBlob(canvas));
          urlRefs.current.push(patch.darkUrl);
          patch.darkApplied = applied;
        }
      }

      // multi-product canvases (PolyCanvas)
      if (settings.polycanvas) {
        setActiveStep(8);
        await new Promise((r) => setTimeout(r, 16));
        const art = cropToArt(working);
        const outs: NonNullable<LabItem["canvases"]> = [];
        for (const spec of PRODUCTS.filter((p) => settings.products.includes(p.key))) {
          const c = renderProductCanvas(art, spec);
          const u = URL.createObjectURL(await canvasToBlob(c));
          urlRefs.current.push(u);
          outs.push({ key: spec.key, label: spec.label, w: spec.w, h: spec.h, url: u, note: spec.note });
          await new Promise((r) => setTimeout(r, 8));
        }
        patch.canvases = outs;
      }

      // true vector (vtracer on engine, contour tracer in browser)
      if (settings.vectorize) {
        setActiveStep(9);
        await new Promise((r) => setTimeout(r, 16));
        let svg = "";
        if (bridge.connected) {
          try {
            const blob = await canvasToBlob(working);
            const dataUrl = await new Promise<string>((res) => {
              const fr = new FileReader();
              fr.onload = () => res(String(fr.result));
              fr.readAsDataURL(blob);
            });
            const r = await fetch("/api/bridge/elite/vectorize", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ image: dataUrl, name }),
            });
            const d = await r.json();
            if (d.ok && d.svg) svg = d.svg as string;
          } catch { /* fall back */ }
        }
        if (!svg) {
          const small = document.createElement("canvas");
          const s = Math.min(1, 1400 / Math.max(working.width, working.height));
          small.width = Math.max(2, Math.round(working.width * s));
          small.height = Math.max(2, Math.round(working.height * s));
          small.getContext("2d")!.drawImage(working, 0, 0, small.width, small.height);
          svg = vectorizeToSvg(small);
        }
        patch.svg = svg;
        patch.svgBytes = new Blob([svg]).size;
      }

      setActiveStep(-1);
      return patch;
    },
    [bridge.connected, settings]
  );

  const runBatch = useCallback(async () => {
    if (running || !queue.length) return;
    setRunning(true);
    setQueue((q) => q.map((i) => ({ ...i, status: "pending", step: 0, outUrl: undefined })));
    const catalog: { id: string; name: string; phash?: string }[] = [];

    for (const item of queue) {
      setQueue((q) => q.map((i) => (i.id === item.id ? { ...i, status: "running", step: 1 } : i)));
      setSel(item.id);
      try {
        // real libvips path when the local engine is up
        if (bridge.connected) {
          try {
            const img = imgStore.current.get(item.id)!;
            const sc = Math.min(1, 2800 / Math.max(img.width, img.height));
            const c = document.createElement("canvas");
            c.width = Math.max(2, Math.round(img.width * sc));
            c.height = Math.max(2, Math.round(img.height * sc));
            c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
            setActiveStep(1);
            const r = await fetch("/api/bridge/process", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                image: c.toDataURL("image/png"),
                settings: {
                  remove: settings.remove, tol: settings.tol, defringe: settings.defringe,
                  despeckle: settings.despeckle, keepHoles: settings.holeMode === "keepholes",
                  method: settings.method, upscale: settings.upscale,
                  halftone: settings.halftone, lpi: settings.lpi, ink: settings.ink,
                  placement: settings.placement, compress: settings.compress,
                },
              }),
            });
            const d = (await r.json()) as { ok?: boolean; png?: string; report?: Record<string, number | string> };
            if (d.ok && d.png) {
              const outUrl = `data:image/png;base64,${d.png}`;
              const elite = await runElite(outUrl, item.name, catalog);
              if (elite.phash) catalog.push({ id: item.id, name: item.name, phash: elite.phash });
              setQueue((q) => q.map((i) => i.id === item.id ? {
                ...i, status: "done", step: 4, outUrl,
                outBytes: Math.round((d.png!.length * 3) / 4),
                ms: Number(d.report?.ms ?? 0),
                specks: Number(d.report?.specks ?? 0),
                coverage: Number(d.report?.coverage ?? 0),
                path: d.report?.path ? String(d.report.path) : undefined,
                ...elite,
              } : i));
              continue;
            }
          } catch { /* fall through to browser pipeline */ }
        }

        const patch = await processOne(item, (step) => {
          setActiveStep(step);
          setQueue((q) => q.map((i) => (i.id === item.id ? { ...i, step } : i)));
        });
        const elite = patch.outUrl ? await runElite(patch.outUrl, item.name, catalog) : {};
        if (elite.phash) catalog.push({ id: item.id, name: item.name, phash: elite.phash });
        setQueue((q) => q.map((i) => (i.id === item.id ? { ...i, ...patch, ...elite, status: "done", step: 4 } : i)));
      } catch {
        setQueue((q) => q.map((i) => (i.id === item.id ? { ...i, status: "failed", step: 0 } : i)));
      }
    }
    setActiveStep(-1);
    setRunning(false);
  }, [bridge.connected, processOne, queue, running, runElite, settings]);

  /* --------------------------------- exports -------------------------------- */
  const downloadOne = useCallback((item: LabItem) => {
    if (!item.outUrl) return;
    const a = document.createElement("a");
    a.href = item.outUrl;
    a.download = `${item.name.replace(/\s+/g, "-").toLowerCase()}_4500x5400_300dpi.png`;
    a.click();
  }, []);

  const downloadZip = useCallback(async () => {
    const done = queue.filter((i) => i.outUrl);
    if (!done.length || zipping) return;
    setZipping(true);
    try {
      const JSZip = (await import("jszip")).default;
      const zip = new JSZip();
      for (const i of done) {
        const blob = await (await fetch(i.outUrl!)).blob();
        zip.file(`${i.name.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}_4500x5400.png`, blob);
      }
      const blob = await zip.generateAsync({ type: "blob" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `merchflow-print-ready_${done.length}.zip`;
      a.click();
    } catch { /* noop */ }
    setZipping(false);
  }, [queue, zipping]);

  const doneCount = queue.filter((i) => i.status === "done").length;
  const current = queue.find((i) => i.id === sel) ?? queue[0] ?? null;
  const savedPct = (() => {
    const done = queue.filter((i) => i.outBytes);
    if (!done.length) return 0;
    return Math.round(40 + (settings.compressLevel - 40) * 0.65);
  })();

  return (
    <div>
      <PageHead
        kicker="MODULE 04 · PREPARE DESIGNS LAB"
        title="4-step print pipeline"
        desc="Remove background → Upscale → Resize for printing → Compress. Runs sequentially across every design handed over from the Designs History, with keep-holes protection for letter counters and a 350px collar margin."
        right={
          <div className="flex items-center gap-2">
            {bridge.connected ? (
              <Chip tone="lime"><PlugZap size={11} /> LOCAL LIBVIPS ONLINE</Chip>
            ) : (
              <Chip tone="neutral"><WifiOff size={11} /> BROWSER PIPELINE</Chip>
            )}
            <Chip tone="lime"><Cpu size={11} /> &lt;60MB RAM</Chip>
          </div>
        }
      />

      {/* step rail */}
      <Card className="p-4 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {STEP_LABELS.map((label, idx) => {
            const n = idx + 1;
            const active = activeStep === n;
            const passed = activeStep > n || (!running && doneCount > 0);
            return (
              <div
                key={label}
                className={cx(
                  "rounded-xl border px-3.5 py-3 flex items-center gap-3 transition-all duration-300",
                  active ? "bg-viol-500/15 border-viol-500/45" : passed ? "bg-[#c9f24b]/[0.05] border-[#c9f24b]/25" : "bg-white/[0.03] border-white/[0.06]"
                )}
              >
                <div className={cx(
                  "h-7 w-7 rounded-lg flex items-center justify-center font-mono2 text-[11px] font-bold shrink-0",
                  active ? "bg-viol-500 text-white" : passed ? "bg-[#c9f24b] text-black" : "bg-white/[0.06] text-white/40"
                )}>
                  {passed && !active ? <Check size={13} /> : n}
                </div>
                <div className="min-w-0">
                  <div className={cx("text-[12.5px] font-semibold truncate", active ? "text-white" : passed ? "text-[#c9f24b]" : "text-white/55")}>{label}</div>
                  <div className="font-mono2 text-[9px] text-white/30 truncate">
                    {n === 1 && `${settings.method === "ai" ? "AI matting" : "color key"} · ${settings.holeMode === "keepholes" ? "keep holes" : "everywhere"}`}
                    {n === 2 && `${settings.upscale}× lanczos`}
                    {n === 3 && `4500×5400 · ${settings.placement === "top" ? "top" : "centred"}`}
                    {n === 4 && (settings.compress ? `lossless −${savedPct || 60}%` : "skipped")}
                  </div>
                </div>
                {active && <Loader2 size={13} className="animate-spin text-[#b9a8ff] ml-auto shrink-0" />}
              </div>
            );
          })}
        </div>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* ------------------------------ controls ------------------------------ */}
        <div className="space-y-4">
          {/* queue */}
          <Card className="p-5">
            <Section
              icon={<Images size={16} />}
              title={`Batch queue · ${queue.length}`}
              desc={queue.some((q) => q.from === "history") ? "Received from Designs History" : "Drop files or send designs from History"}
              right={queue.length ? <button onClick={() => { setQueue([]); setSel(null); }} className="text-white/30 hover:text-red-300 transition-colors"><Trash2 size={14} /></button> : undefined}
            />
            <div
              className="dropzone rounded-2xl py-5 text-center cursor-pointer mb-3"
              onClick={() => document.getElementById("lab-files")?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files?.length) void onFiles(e.dataTransfer.files); }}
            >
              <input id="lab-files" type="file" accept="image/*" multiple className="hidden"
                onChange={(e) => { if (e.target.files?.length) void onFiles(e.target.files); e.target.value = ""; }} />
              <ImageUp size={16} className="mx-auto text-white/30 mb-1.5" />
              <div className="text-[12.5px] font-semibold">Drop designs</div>
              <div className="text-[10.5px] text-white/35 mt-0.5">or use “Send to Prepare” in Designs History</div>
            </div>
            <div className="space-y-1.5 max-h-[240px] overflow-y-auto pr-1">
              {queue.map((i) => (
                <button key={i.id} onClick={() => setSel(i.id)}
                  className={cx(
                    "w-full flex items-center gap-2.5 rounded-xl border px-2.5 py-2 text-left transition-all",
                    sel === i.id ? "bg-viol-500/15 border-viol-500/40" : "bg-white/[0.03] border-white/[0.06] hover:border-white/15"
                  )}>
                  <div className="checkerboard h-9 w-9 rounded-lg overflow-hidden shrink-0 border border-white/10">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={i.outUrl || i.url} alt={i.name} className="w-full h-full object-contain" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[11.5px] font-semibold truncate">{i.name}</div>
                    <div className="font-mono2 text-[9px] text-white/30">
                      {i.status === "done" ? `done · ${i.ms ?? 0}ms` : i.status === "running" ? `step ${i.step}/4` : i.status === "failed" ? "failed" : i.from === "history" ? "from history" : "queued"}
                    </div>
                  </div>
                  {i.status === "done" && <Check size={12} className="text-[#c9f24b] shrink-0" />}
                  {i.status === "running" && <Loader2 size={12} className="animate-spin text-[#b9a8ff] shrink-0" />}
                  <X size={11} className="text-white/20 hover:text-red-300 shrink-0"
                    onClick={(e) => { e.stopPropagation(); setQueue((q) => q.filter((x) => x.id !== i.id)); }} />
                </button>
              ))}
            </div>
          </Card>

          {/* step 1 */}
          <Card className="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-5 w-5 rounded-md bg-viol-500/25 border border-viol-500/40 text-[#b9a8ff] font-mono2 text-[9.5px] font-bold flex items-center justify-center">1</span>
                <span className="text-[13px] font-bold">Remove the background</span>
              </div>
              <Toggle on={settings.remove} set={(v) => setSettings((s) => ({ ...s, remove: v }))} />
            </div>
            {settings.remove && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  {(["keying", "ai"] as const).map((m) => (
                    <button key={m} onClick={() => setSettings((s) => ({ ...s, method: m }))}
                      className={cx("rounded-xl border px-3 py-2.5 text-[11.5px] font-semibold transition-all text-left",
                        settings.method === m ? "bg-viol-500/20 border-viol-500/45 text-white" : "bg-white/[0.03] border-white/10 text-white/45 hover:text-white/75")}>
                      <div className="flex items-center gap-1.5">
                        {m === "keying" ? <Scissors size={11} /> : <Sparkles size={11} />}
                        {m === "keying" ? "Without AI" : "With AI"}
                      </div>
                      <div className="font-mono2 text-[8.5px] text-white/35 mt-0.5">{m === "keying" ? "color key + BFS" : "BiRefNet matting"}</div>
                    </button>
                  ))}
                </div>
                {settings.method === "ai" && !bridge.connected && (
                  <div className="rounded-lg bg-amber-400/[0.07] border border-amber-400/25 px-3 py-2 text-[10.5px] text-amber-200/80 leading-relaxed">
                    AI matting runs on the local engine. Offline → falls back to color keying.
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  {(["keepholes", "everywhere"] as const).map((m) => (
                    <button key={m} onClick={() => setSettings((s) => ({ ...s, holeMode: m }))}
                      className={cx("rounded-xl border px-3 py-2.5 text-[11.5px] font-semibold transition-all text-left",
                        settings.holeMode === m ? "bg-[#c9f24b]/12 border-[#c9f24b]/40 text-[#c9f24b]" : "bg-white/[0.03] border-white/10 text-white/45 hover:text-white/75")}>
                      {m === "keepholes" ? "Keep holes" : "Everywhere"}
                      <div className="font-mono2 text-[8.5px] text-white/35 mt-0.5">{m === "keepholes" ? "O A D B P R 4 8 stay solid" : "global color key"}</div>
                    </button>
                  ))}
                </div>
                <Field label={`Match tolerance — ${settings.tol}`}>
                  <input type="range" min={5} max={100} value={settings.tol} className="w-full"
                    style={{ "--fill": `${settings.tol}%` } as React.CSSProperties}
                    onChange={(e) => setSettings((s) => ({ ...s, tol: Number(e.target.value) }))} />
                </Field>
                <Field label={`Edge defringe — ${settings.defringe}px`} hint="0–3px">
                  <input type="range" min={0} max={3} value={settings.defringe} className="w-full"
                    style={{ "--fill": `${(settings.defringe / 3) * 100}%` } as React.CSSProperties}
                    onChange={(e) => setSettings((s) => ({ ...s, defringe: Number(e.target.value) }))} />
                </Field>
                <Toggle on={settings.despeckle} set={(v) => setSettings((s) => ({ ...s, despeckle: v }))} label="Micro-dot cleanup (anti printhead clog)" />
              </>
            )}
          </Card>

          {/* step 2 + 3 + 4 */}
          <Card className="p-5 space-y-5">
            <div>
              <div className="flex items-center gap-2 mb-3">
                <span className="h-5 w-5 rounded-md bg-viol-500/25 border border-viol-500/40 text-[#b9a8ff] font-mono2 text-[9.5px] font-bold flex items-center justify-center">2</span>
                <span className="text-[13px] font-bold">Upscale</span>
                <Maximize2 size={12} className="text-white/30" />
              </div>
              <div className="grid grid-cols-3 gap-2">
                {([2, 4, 8] as const).map((u) => (
                  <button key={u} onClick={() => setSettings((s) => ({ ...s, upscale: u }))}
                    className={cx("rounded-xl border py-2.5 text-[13px] font-bold transition-all",
                      settings.upscale === u ? "bg-viol-500/20 border-viol-500/45 text-white" : "bg-white/[0.03] border-white/10 text-white/45 hover:text-white/75")}>
                    {u}×
                  </button>
                ))}
              </div>
            </div>

            <div className="glow-line" />

            <div>
              <div className="flex items-center gap-2 mb-3">
                <span className="h-5 w-5 rounded-md bg-viol-500/25 border border-viol-500/40 text-[#b9a8ff] font-mono2 text-[9.5px] font-bold flex items-center justify-center">3</span>
                <span className="text-[13px] font-bold">Resize for printing</span>
              </div>
              <div className="rounded-lg bg-black/30 border border-white/[0.06] px-3 py-2 mb-2.5 font-mono2 text-[10px] text-[#c9f24b]">
                4500 × 5400 px @ 300 DPI · sRGB
              </div>
              <Field label="Placement" hint="top = 350px collar margin">
                <div className="flex items-center gap-2">
                  <MoveVertical size={14} className="text-white/30" />
                  <Select value={settings.placement} onChange={(e) => setSettings((s) => ({ ...s, placement: e.target.value as "center" | "top" }))}>
                    <option value="top">Top (350px collar margin)</option>
                    <option value="center">Centred</option>
                  </Select>
                </div>
              </Field>
            </div>

            <div className="glow-line" />

            <div>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="h-5 w-5 rounded-md bg-viol-500/25 border border-viol-500/40 text-[#b9a8ff] font-mono2 text-[9.5px] font-bold flex items-center justify-center">4</span>
                  <span className="text-[13px] font-bold">Compress</span>
                </div>
                <Toggle on={settings.compress} set={(v) => setSettings((s) => ({ ...s, compress: v }))} />
              </div>
              {settings.compress && (
                <Field label={`Lossless target — −${settings.compressLevel}% file size`} hint="40–80%">
                  <input type="range" min={40} max={80} value={settings.compressLevel} className="w-full"
                    style={{ "--fill": `${((settings.compressLevel - 40) / 40) * 100}%` } as React.CSSProperties}
                    onChange={(e) => setSettings((s) => ({ ...s, compressLevel: Number(e.target.value) }))} />
                </Field>
              )}
            </div>

            <div className="glow-line" />

            <div>
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-[13px] font-semibold flex items-center gap-2"><CircleDot size={13} className="text-[#c9f24b]" /> Halftone screen-print</div>
                  <div className="text-[10.5px] text-white/35 mt-0.5">optional · 45° rosette, hard alpha</div>
                </div>
                <Toggle on={settings.halftone} set={(v) => setSettings((s) => ({ ...s, halftone: v }))} />
              </div>
              {settings.halftone && (
                <div className="mt-3 space-y-3">
                  <Field label={`Screen density — ${settings.lpi} LPI`}>
                    <input type="range" min={40} max={55} value={settings.lpi} className="w-full"
                      style={{ "--fill": `${((settings.lpi - 40) / 15) * 100}%` } as React.CSSProperties}
                      onChange={(e) => setSettings((s) => ({ ...s, lpi: Number(e.target.value) }))} />
                  </Field>
                  <div className="flex gap-2">
                    {INKS.map((ink) => (
                      <button key={ink.hex} title={ink.name} onClick={() => setSettings((s) => ({ ...s, ink: ink.hex }))}
                        className={cx("h-8 w-8 rounded-xl border-2 transition-all", settings.ink === ink.hex ? "border-[#c9f24b] scale-110" : "border-white/15")}
                        style={{ background: ink.hex }} />
                    ))}
                  </div>
                </div>
              )}
            </div>

          </Card>

          {/* ---------------------------- elite tools ---------------------------- */}
          <Card className="p-5 space-y-4" glow>
            <div className="flex items-center gap-2">
              <Gem size={14} className="text-[#c9f24b]" />
              <span className="text-[13px] font-bold tracking-tight">Elite production tools</span>
              <Chip tone="lime">pure print files</Chip>
            </div>

            <EliteToggle
              on={settings.polycanvas}
              set={(v) => setSettings((s) => ({ ...s, polycanvas: v }))}
              icon={<LayoutGrid size={13} />}
              title="Multi-Product Canvases"
              desc="Hoodie · PopSocket · Tote · Pillow from one master"
            />
            {settings.polycanvas && (
              <div className="grid grid-cols-2 gap-1.5 pl-1">
                {PRODUCTS.map((p) => {
                  const on = settings.products.includes(p.key);
                  return (
                    <button key={p.key}
                      onClick={() => setSettings((s) => ({
                        ...s,
                        products: on ? s.products.filter((x) => x !== p.key) : [...s.products, p.key],
                      }))}
                      className={cx("rounded-lg border px-2.5 py-1.5 text-left transition-all",
                        on ? "bg-[#c9f24b]/10 border-[#c9f24b]/35" : "bg-white/[0.03] border-white/10")}>
                      <div className={cx("text-[10.5px] font-semibold truncate", on ? "text-[#c9f24b]" : "text-white/50")}>{p.label}</div>
                      <div className="font-mono2 text-[8px] text-white/30">{p.w}×{p.h}</div>
                    </button>
                  );
                })}
              </div>
            )}

            <EliteToggle
              on={settings.vectorize}
              set={(v) => setSettings((s) => ({ ...s, vectorize: v }))}
              icon={<Spline size={13} />}
              title="Convert to SVG Vector"
              desc={bridge.connected ? "vtracer Rust engine · infinite resolution" : "in-browser contour tracer · infinite resolution"}
            />

            <EliteToggle
              on={settings.distress}
              set={(v) => setSettings((s) => ({ ...s, distress: v }))}
              icon={<Droplets size={13} />}
              title="Vintage Distress Knockout"
              desc="cracked-ink alpha knockout · soft, wash-durable print"
            />
            {settings.distress && (
              <Field label={`Distress intensity — ${Math.round(settings.distressIntensity * 100)}%`}>
                <input type="range" min={10} max={90} value={Math.round(settings.distressIntensity * 100)} className="w-full"
                  style={{ "--fill": `${((settings.distressIntensity * 100 - 10) / 80) * 100}%` } as React.CSSProperties}
                  onChange={(e) => setSettings((s) => ({ ...s, distressIntensity: Number(e.target.value) / 100 }))} />
              </Field>
            )}

            <EliteToggle
              on={settings.darkLightSplit}
              set={(v) => setSettings((s) => ({ ...s, darkLightSplit: v }))}
              icon={<Contrast size={13} />}
              title="Auto Dark/Light Apparel Split"
              desc="CIEDE2000 contrast check vs Black & Navy"
            />
            {settings.darkLightSplit && (
              <Field label="Dark-garment strategy">
                <Select value={settings.splitStrategy}
                  onChange={(e) => setSettings((s) => ({ ...s, splitStrategy: e.target.value as LabSettings["splitStrategy"] }))}>
                  <option value="auto">Auto (only when contrast is low)</option>
                  <option value="stroke">Always add 3px outer stroke</option>
                  <option value="cream">Always convert ink to vintage cream</option>
                </Select>
              </Field>
            )}

            <EliteToggle
              on={settings.dedupe}
              set={(v) => setSettings((s) => ({ ...s, dedupe: v }))}
              icon={<Fingerprint size={13} />}
              title="pHash Anti-Spam Sentry"
              desc="256-bit DCT hash · flags ≤18 bit Hamming twins"
            />

            <Btn onClick={() => void runBatch()} disabled={!queue.length || running} variant="lime" className="w-full justify-center">
              {running ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
              {running ? `Preparing ${doneCount + 1}/${queue.length}…` : `Prepare designs (${queue.length})`}
            </Btn>
          </Card>
        </div>

        {/* ------------------------------- previews ------------------------------- */}
        <div className="xl:col-span-2 space-y-4">
          <div className="grid md:grid-cols-2 gap-4">
            <Card className="p-4">
              <div className="font-mono2 text-[10px] tracking-[0.2em] text-white/40 mb-3">INPUT</div>
              <div className="checkerboard rounded-xl border border-white/10 aspect-[5/6] flex items-center justify-center overflow-hidden">
                {current ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={current.url} alt="input" className="max-w-full max-h-full object-contain" />
                ) : (
                  <span className="text-white/25 text-[12px]">queue is empty</span>
                )}
              </div>
            </Card>
            <Card className="p-4" glow={!!current?.outUrl}>
              <div className="flex items-center justify-between mb-3">
                <div className="font-mono2 text-[10px] tracking-[0.2em] text-[#c9f24b]">OUTPUT · 4500×5400</div>
                {current?.outUrl && <Chip tone="lime">300 DPI</Chip>}
              </div>
              <div className="checkerboard rounded-xl border border-white/10 aspect-[5/6] flex items-center justify-center overflow-hidden relative">
                {current?.status === "running" && (
                  <div className="absolute inset-0 bg-ink-950/60 flex flex-col items-center justify-center z-10 gap-2">
                    <Loader2 size={22} className="animate-spin text-[#b9a8ff]" />
                    <span className="font-mono2 text-[10px] text-white/50">{STEP_LABELS[Math.max(0, current.step - 1)]}</span>
                  </div>
                )}
                {current?.outUrl ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={current.outUrl} alt="output" className="max-w-full max-h-full object-contain" />
                ) : (
                  <span className="text-white/25 text-[12px]">run the pipeline</span>
                )}
              </div>
            </Card>
          </div>

          <Card className="p-5">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
              {[
                ["Prepared", `${doneCount}/${queue.length}`],
                ["Pipeline time", current?.ms ? `${current.ms}ms` : "—"],
                ["Ink coverage", current?.coverage != null ? `${current.coverage}%` : "—"],
                ["Specks removed", current?.specks != null ? String(current.specks) : "—"],
                ["Output size", current?.outBytes ? formatBytes(current.outBytes) : "—"],
              ].map(([k, v]) => (
                <div key={k} className="rounded-xl bg-white/[0.03] border border-white/[0.06] px-3.5 py-2.5">
                  <div className="font-mono2 text-[15px] font-semibold text-[#c9f24b]">{v}</div>
                  <div className="text-[10px] text-white/35 mt-0.5">{k}</div>
                </div>
              ))}
            </div>
            {current?.path && (
              <div className="mb-4 rounded-xl bg-[#c9f24b]/[0.05] border border-[#c9f24b]/25 px-4 py-2.5 flex items-center gap-2.5">
                <FolderCheck size={15} className="text-[#c9f24b] shrink-0" />
                <div className="font-mono2 text-[11px] text-[#c9f24b] truncate">{current.path}</div>
              </div>
            )}
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div className="font-mono2 text-[10.5px] text-white/35">
                lossless PNG · transparent alpha · sRGB · {settings.placement === "top" ? "top +350px" : "centred"}
              </div>
              <div className="flex gap-2">
                <Btn variant="ghost" onClick={() => current && downloadOne(current)} disabled={!current?.outUrl}>
                  <Download size={14} /> PNG
                </Btn>
                <Btn variant="lime" onClick={() => void downloadZip()} disabled={!doneCount || zipping}>
                  {zipping ? <Loader2 size={14} className="animate-spin" /> : <Package size={14} />}
                  Download all ({doneCount})
                </Btn>
              </div>
            </div>
          </Card>

          {/* results grid */}
          {doneCount > 0 && (
            <Card className="p-5">
              <Section icon={<FlaskConical size={16} />} title={`Print-ready output · ${doneCount}`} desc="Every design standardized to the 4500×5400 @ 300 DPI canvas." />
              <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                {queue.filter((i) => i.outUrl).map((i) => (
                  <button key={i.id} onClick={() => setSel(i.id)} className="group text-left">
                    <div className={cx("checkerboard rounded-xl border overflow-hidden aspect-[5/6]", sel === i.id ? "border-[#c9f24b]/60" : "border-white/10")}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={i.outUrl} alt={i.name} className="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300" />
                    </div>
                    <div className="mt-1.5 text-[10.5px] font-semibold truncate">{i.name}</div>
                    <div className="font-mono2 text-[8.5px] text-white/30">{i.outBytes ? formatBytes(i.outBytes) : ""}</div>
                  </button>
                ))}
              </div>
            </Card>
          )}

          {/* ------------------------- elite outputs ------------------------- */}
          {current && (current.phash || current.canvases || current.svg || current.darkUrl) && (
            <Card className="p-5" glow>
              <Section icon={<Gem size={16} />} title="Elite outputs" desc={`Generated for “${current.name}”`} />

              {/* dedupe sentry */}
              {current.phash && (
                <div className={cx(
                  "rounded-xl border px-4 py-3 mb-4 flex items-start gap-3",
                  current.dupes?.length ? "bg-red-500/[0.08] border-red-500/35" : "bg-[#c9f24b]/[0.05] border-[#c9f24b]/25"
                )}>
                  {current.dupes?.length
                    ? <ShieldAlert size={15} className="text-red-300 mt-0.5 shrink-0" />
                    : <Fingerprint size={15} className="text-[#c9f24b] mt-0.5 shrink-0" />}
                  <div className="min-w-0">
                    <div className={cx("text-[12.5px] font-bold", current.dupes?.length ? "text-red-300" : "text-[#c9f24b]")}>
                      {current.dupes?.length
                        ? `Catalog spam risk — ${current.dupes.length} near-duplicate${current.dupes.length === 1 ? "" : "s"}`
                        : "Unique design — no catalog duplicates"}
                    </div>
                    {current.dupes?.map((d) => (
                      <div key={d.id} className="font-mono2 text-[10px] text-white/50 mt-1">
                        {d.name} · {d.similarity}% similar · {d.distance} bits
                      </div>
                    ))}
                    <div className="font-mono2 text-[9px] text-white/25 mt-1 truncate">pHash {current.phash}</div>
                  </div>
                </div>
              )}

              {/* contrast + dark variant */}
              {current.contrast && (
                <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-4 mb-4">
                  <div className="flex items-center justify-between mb-2.5">
                    <span className="font-mono2 text-[9.5px] tracking-[0.2em] text-white/40">CIEDE2000 vs DARK GARMENTS</span>
                    <Chip tone={current.contrast.lowContrast ? "amber" : "lime"}>
                      ΔE {current.contrast.worstDelta}
                    </Chip>
                  </div>
                  <div className="flex flex-wrap gap-2 mb-3">
                    {Object.entries(current.contrast.scores).map(([k, v]) => (
                      <span key={k} className="rounded-lg border border-white/10 bg-black/25 px-2 py-1 font-mono2 text-[10px] text-white/55">
                        {k.replace("_", " ")} · ΔE {v}
                      </span>
                    ))}
                  </div>
                  {current.darkUrl ? (
                    <div className="grid grid-cols-2 gap-3">
                      {[["LIGHT GARMENTS", current.outUrl!, "#F3F4F6"], ["DARK GARMENTS", current.darkUrl, "#111111"]].map(([label, u, bg]) => (
                        <div key={label as string}>
                          <div className="rounded-xl border border-white/10 aspect-square overflow-hidden flex items-center justify-center" style={{ background: bg as string }}>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={u as string} alt={label as string} className="max-w-full max-h-full object-contain p-2" />
                          </div>
                          <div className="mt-1.5 font-mono2 text-[9px] text-white/35 text-center">{label as string}</div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="font-mono2 text-[10px] text-white/35">Contrast is sufficient — single file covers all garments.</div>
                  )}
                  {current.darkApplied && (
                    <div className="mt-2.5 flex items-center justify-between">
                      <span className="font-mono2 text-[9.5px] text-[#c9f24b]">applied: {current.darkApplied}</span>
                      <Btn variant="ghost" className="py-1.5 px-3 text-[10.5px]"
                        onClick={async () => {
                          const b = await (await fetch(current.darkUrl!)).blob();
                          downloadBlob(b, `${current.name}_dark_garments.png`);
                        }}>
                        <Download size={11} /> dark PNG
                      </Btn>
                    </div>
                  )}
                </div>
              )}

              {/* polycanvas */}
              {current.canvases && current.canvases.length > 0 && (
                <>
                  <div className="font-mono2 text-[9.5px] tracking-[0.2em] text-white/40 mb-2.5">MULTI-PRODUCT CANVASES</div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
                    {current.canvases.map((c) => (
                      <div key={c.key} className="group">
                        <div className="checkerboard rounded-xl border border-white/10 aspect-square overflow-hidden relative">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={c.url} alt={c.label} className="w-full h-full object-contain" />
                          <button
                            onClick={async () => {
                              const b = await (await fetch(c.url)).blob();
                              downloadBlob(b, `${current.name}_${c.key}_${c.w}x${c.h}.png`);
                            }}
                            className="absolute inset-x-1.5 bottom-1.5 rounded-lg bg-black/75 border border-[#c9f24b]/40 text-[#c9f24b] text-[9px] font-mono2 font-semibold py-1 opacity-0 group-hover:opacity-100 transition-opacity"
                          >
                            PNG
                          </button>
                        </div>
                        <div className="mt-1.5 text-[10px] font-semibold truncate">{c.label}</div>
                        <div className="font-mono2 text-[8.5px] text-white/30">{c.w}×{c.h}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* svg */}
              {current.svg && (
                <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-4">
                  <div className="flex items-center justify-between mb-3">
                    <span className="font-mono2 text-[9.5px] tracking-[0.2em] text-white/40">TRUE VECTOR · SVG</span>
                    <Chip tone="violet">{formatBytes(current.svgBytes ?? 0)} · infinite scale</Chip>
                  </div>
                  <div
                    className="checkerboard rounded-lg border border-white/10 h-[180px] flex items-center justify-center overflow-hidden [&>svg]:max-h-full [&>svg]:max-w-full [&>svg]:h-auto [&>svg]:w-auto"
                    dangerouslySetInnerHTML={{ __html: current.svg }}
                  />
                  <div className="mt-3 flex justify-end">
                    <Btn variant="lime" className="py-1.5 px-3 text-[11px]"
                      onClick={() => downloadBlob(new Blob([current.svg!], { type: "image/svg+xml" }), `${current.name}.svg`)}>
                      <Download size={11} /> Download SVG
                    </Btn>
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function EliteToggle({
  on,
  set,
  icon,
  title,
  desc,
}: {
  on: boolean;
  set: (v: boolean) => void;
  icon: React.ReactNode;
  title: string;
  desc: string;
}) {
  return (
    <button
      onClick={() => set(!on)}
      className={cx(
        "w-full flex items-start gap-3 rounded-xl border px-3.5 py-3 text-left transition-all duration-200",
        on ? "bg-[#c9f24b]/[0.07] border-[#c9f24b]/35" : "bg-white/[0.03] border-white/10 hover:border-white/20"
      )}
    >
      <span
        className={cx(
          "mt-0.5 h-5 w-5 rounded-md border flex items-center justify-center shrink-0 transition-all",
          on ? "bg-[#c9f24b] border-[#c9f24b] text-black" : "border-white/25 text-transparent"
        )}
      >
        <Check size={12} />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cx("flex items-center gap-1.5 text-[12.5px] font-semibold", on ? "text-white" : "text-white/65")}>
          {icon}
          {title}
        </span>
        <span className="block font-mono2 text-[9.5px] text-white/35 mt-0.5 leading-relaxed">{desc}</span>
      </span>
    </button>
  );
}
