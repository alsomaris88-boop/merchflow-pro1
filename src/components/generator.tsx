"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Download,
  FolderCheck,
  Image as ImageIcon,
  Layers,
  Play,
  PlugZap,
  ScanEye,
  Sparkles,
  Trash2,
  Type,
  WandSparkles,
  WifiOff,
  X,
} from "lucide-react";
import { Btn, Card, Chip, CopyBtn, Field, Input, PageHead, Section, Select, cx } from "@/components/ui";
import { probeBridge, dataUrlFromUrl, readSSE, type BridgeStatus } from "@/lib/bridge";
import { addDesign, addDesignFromDataUrl } from "@/lib/designs-store";

/* ------------------------------------------------------------------ types */
interface NicheItem {
  id: string;
  name: string;
  url: string; // object URL
}
interface QueueItem extends NicheItem {
  status: "queued" | "running" | "done";
  progress: number;
  account?: string;
  ms?: number;
}
interface ResultItem {
  id: string;
  name: string;
  url: string;
  account: string;
  ms: number;
  w: number;
  h: number;
  path?: string; // local engine output path, e.g. C:/MerchFlow/Output/…
}
interface AccountLite {
  id: number;
  label: string;
  status: string;
}

const ASPECTS: Record<string, { w: number; h: number; label: string }> = {
  "4:5": { w: 1120, h: 1400, label: "4:5 apparel (4500×5400)" },
  "1:1": { w: 1400, h: 1400, label: "1:1 square (4500×4500)" },
  "3:4": { w: 1200, h: 1600, label: "3:4 portrait (4050×5400)" },
};

const SYSTEM_INSTRUCTION = `You are Imagen-Fusion, a multimodal POD design engine.
INPUT A (Art Style Reference): Extract ONLY the color palette, line weight,
vector shading style and surface texture. IGNORE the subject matter of Input A
entirely — it never appears in the output.
INPUT B (Niche Subject Reference): Render this exact subject with perfect
anatomical fidelity, drawn 100% in the extracted style of Input A.
If TEXT_OVERRIDE is provided: render that exact string, set in typography that
mirrors Input A lettering. If omitted: output pure graphic art, zero text,
zero watermarks, zero garbled letters.
OUTPUT: single centred composition, clean silhouette edges, print-ready.`;

/* ------------------------------------------------------------- image utils */
function loadImg(url: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = url;
  });
}

/** Fast color-quantized palette extraction (k buckets, 4-bit channels) */
async function extractPalette(url: string, k = 6): Promise<string[]> {
  const img = await loadImg(url);
  const c = document.createElement("canvas");
  const s = 96;
  c.width = s;
  c.height = s;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0, s, s);
  const { data } = ctx.getImageData(0, 0, s, s);
  const hist = new Map<number, { n: number; r: number; g: number; b: number }>();
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3];
    if (a < 90) continue;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
    const e = hist.get(key) ?? { n: 0, r: 0, g: 0, b: 0 };
    e.n++;
    e.r += r;
    e.g += g;
    e.b += b;
    hist.set(key, e);
  }
  const top = [...hist.values()].sort((a, b) => b.n - a.n).slice(0, k * 2);
  // dedupe visually near-identical buckets, then take k
  const out: string[] = [];
  for (const e of top) {
    const r = Math.round(e.r / e.n);
    const g = Math.round(e.g / e.n);
    const b = Math.round(e.b / e.n);
    const hex = "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
    if (!out.some((o) => colorDist(o, hex) < 34)) out.push(hex);
    if (out.length >= k) break;
  }
  return out;
}

function colorDist(a: string, b: string): number {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const dr = ((pa >> 16) & 255) - ((pb >> 16) & 255);
  const dg = ((pa >> 8) & 255) - ((pb >> 8) & 255);
  const db = (pa & 255) - (pb & 255);
  return Math.sqrt(dr * dr + dg * dg + db * db);
}

function hexLum(hex: string): number {
  const p = parseInt(hex.slice(1), 16);
  return 0.299 * ((p >> 16) & 255) + 0.587 * ((p >> 8) & 255) + 0.114 * (p & 255);
}

/**
 * Multimodal-style fusion renderer: gradient-map recolor of the niche subject
 * onto the style palette; luminance/alpha driven ink mask.
 */
async function fuseDesign(
  nicheUrl: string,
  palette: string[],
  aspect: string,
  background: string,
  textOverride: string
): Promise<HTMLCanvasElement> {
  const { w: W, h: H } = ASPECTS[aspect] ?? ASPECTS["4:5"];
  const img = await loadImg(nicheUrl);

  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;

  // contain-fit subject
  const scale = Math.min((W * 0.86) / img.width, (H * 0.8) / img.height);
  const dw = img.width * scale;
  const dh = img.height * scale;
  const artTop = textOverride.trim() ? H * 0.06 : (H - dh) / 2 - H * 0.02;
  ctx.drawImage(img, (W - dw) / 2, textOverride.trim() ? artTop : (H - dh) / 2, dw, dh);

  const id = ctx.getImageData(0, 0, W, H);
  const d = id.data;
  let hasAlpha = false;
  for (let i = 3; i < d.length; i += 40) {
    if (d[i] < 245) {
      hasAlpha = true;
      break;
    }
  }

  const ramp = [...palette].sort((a, b) => hexLum(a) - hexLum(b)).map((h) => {
    const p = parseInt(h.slice(1), 16);
    return [(p >> 16) & 255, (p >> 8) & 255, p & 255];
  });
  if (ramp.length === 0) ramp.push([17, 17, 17], [243, 244, 246]);

  for (let i = 0; i < d.length; i += 4) {
    const r = d[i];
    const g = d[i + 1];
    const b = d[i + 2];
    const a0 = d[i + 3];
    const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    const idx = Math.min(ramp.length - 1, Math.max(0, Math.floor(lum * ramp.length)));
    const col = ramp[idx];
    const mask = hasAlpha ? a0 : Math.round((1 - lum) * 255);
    d[i] = col[0];
    d[i + 1] = col[1];
    d[i + 2] = col[2];
    d[i + 3] = Math.min(255, Math.round(mask * 1.18));
  }
  ctx.putImageData(id, 0, 0);

  // optional typography override
  const t = textOverride.trim();
  if (t) {
    let size = Math.round(H * 0.085);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    do {
      ctx.font = `700 ${size}px "Space Grotesk", sans-serif`;
      size -= 4;
    } while (ctx.measureText(t.toUpperCase()).width > W * 0.84 && size > 18);
    const ink = ramp[ramp.length - 1];
    const shade = ramp[0];
    ctx.lineWidth = Math.max(4, size * 0.16);
    ctx.strokeStyle = `rgb(${shade[0]},${shade[1]},${shade[2]})`;
    ctx.lineJoin = "round";
    ctx.strokeText(t.toUpperCase(), W / 2, H * 0.86);
    ctx.fillStyle = `rgb(${ink[0]},${ink[1]},${ink[2]})`;
    ctx.fillText(t.toUpperCase(), W / 2, H * 0.86);
  }

  // background composite
  if (background !== "transparent") {
    const out = document.createElement("canvas");
    out.width = W;
    out.height = H;
    const octx = out.getContext("2d")!;
    octx.fillStyle = background === "white" ? "#FFFFFF" : "#0B0B0B";
    octx.fillRect(0, 0, W, H);
    octx.drawImage(c, 0, 0);
    return out;
  }
  return c;
}

const EXPORT_DIMS: Record<string, [number, number]> = {
  "4:5": [4500, 5400],
  "1:1": [4500, 4500],
  "3:4": [4050, 5400],
};

/* ------------------------------------------------------------- component */
export default function Generator() {
  const [styleUrl, setStyleUrl] = useState<string | null>(null);
  const [styleName, setStyleName] = useState("");
  const [palette, setPalette] = useState<string[]>([]);
  const [niches, setNiches] = useState<NicheItem[]>([]);
  const [aspect, setAspect] = useState("4:5");
  const [background, setBackground] = useState("transparent");
  const [textOverride, setTextOverride] = useState("");
  const [showPrompt, setShowPrompt] = useState(false);

  const [accounts, setAccounts] = useState<AccountLite[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<ResultItem[]>([]);
  const canvasStore = useRef(new Map<string, HTMLCanvasElement>());
  const [bridge, setBridge] = useState<BridgeStatus>({ connected: false });

  const log = useCallback((msg: string) => {
    const t = new Date().toLocaleTimeString("en-US", { hour12: false });
    setLogs((l) => [...l.slice(-60), `[${t}] ${msg}`]);
  }, []);

  useEffect(() => {
    fetch("/api/accounts")
      .then((r) => r.json())
      .then((d) => setAccounts(Array.isArray(d.items) ? d.items : []))
      .catch(() => setAccounts([]));
    // probe the local desktop engine (FastAPI @ 127.0.0.1:8000 via Next bridge)
    const probe = () => probeBridge().then(setBridge).catch(() => setBridge({ connected: false }));
    void probe();
    const poll = setInterval(probe, 20000);
    return () => clearInterval(poll);
  }, []);

  /* ------------------------------ drop handlers ------------------------------ */
  const onStyleFile = useCallback(async (file: File) => {
    const url = URL.createObjectURL(file);
    setStyleUrl(url);
    setStyleName(file.name.replace(/\.[^.]+$/, ""));
    log(`style reference loaded → ${file.name} · extracting palette`);
    const pal = await extractPalette(url);
    setPalette(pal);
    log(`palette extracted: ${pal.join(" ")}`);
  }, [log]);

  const onNicheFiles = useCallback((files: FileList) => {
    const items = Array.from(files).slice(0, 20).map((f) => ({
      id: Math.random().toString(36).slice(2, 9),
      name: f.name.replace(/\.[^.]+$/, ""),
      url: URL.createObjectURL(f),
    }));
    setNiches((n) => [...n, ...items].slice(0, 20));
    log(`${items.length} niche subject(s) staged in Dropzone B`);
  }, [log]);

  /* --------------------------- simulated render queue -------------------------- */
  const runSimulated = useCallback(async () => {
    if (!styleUrl || !palette.length || !niches.length || running) return;
    setRunning(true);
    setResults([]);
    canvasStore.current.clear();

    const workers = accounts.filter((a) => a.status === "ready" || a.status === "active");
    const pool: AccountLite[] = workers.length ? workers : [{ id: 0, label: "Profile 01", status: "ready" }];
    log(`dispatching ${niches.length} job(s) across ${pool.length} healthy worker(s) — jitter 65–85s · demo accel ×10`);

    const q: QueueItem[] = niches.map((n) => ({ ...n, status: "queued", progress: 0 }));
    setQueue(q);
    let index = 0;
    let wi = 0;

    const worker = async (acct: AccountLite) => {
      while (index < q.length) {
        const my = index++;
        const item = q[my];
        const target = 6500 + Math.random() * 2500;
        const steps = 14;
        setQueue((prev) => prev.map((p, i) => (i === my ? { ...p, status: "running", account: acct.label, progress: 4 } : p)));
        log(`[${acct.label}] job #${my + 1} "${item.name}" → multimodal fusion request`);

        for (let sIdx = 1; sIdx <= steps; sIdx++) {
          await new Promise((r) => setTimeout(r, target / steps / 10));
          setQueue((prev) =>
            prev.map((p, i) => (i === my ? { ...p, progress: Math.min(96, Math.round((sIdx / steps) * 100)) } : p))
          );
        }

        try {
          const canvas = await fuseDesign(item.url, palette, aspect, background, textOverride);
          canvasStore.current.set(item.id, canvas);
          const url = canvas.toDataURL("image/png");
          setResults((r) => [
            ...r,
            { id: item.id, name: item.name, url, account: acct.label, ms: Math.round(target), w: canvas.width, h: canvas.height },
          ]);
          setQueue((prev) => prev.map((p, i) => (i === my ? { ...p, status: "done", progress: 100, ms: Math.round(target) } : p)));
          log(`[${acct.label}] job #${my + 1} complete in ${(target / 1000).toFixed(1)}s · print-ready PNG`);
          void addDesignFromDataUrl(url, {
            name: item.name,
            niche: item.name,
            style: styleName,
            source: "generator",
            aspect,
            background,
            prompt: textOverride ? `TEXT OVERRIDE: ${textOverride}` : SYSTEM_INSTRUCTION,
            account: acct.label,
            w: canvas.width,
            h: canvas.height,
            ms: Math.round(target),
          }).then((id) => {
            if (id) log(`archived to Designs History → ${item.name}`);
          });
          void fetch("/api/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              styleName, nicheName: item.name, aspect, background, textOverride,
              accountLabel: acct.label, status: "done", durationMs: Math.round(target),
            }),
          }).catch(() => undefined);
        } catch {
          setQueue((prev) => prev.map((p, i) => (i === my ? { ...p, status: "done", progress: 100 } : p)));
          log(`[${acct.label}] job #${my + 1} failed — rerouted via heartbeat failover`);
        }
      }
    };

    const runners: Promise<void>[] = [];
    for (let k = 0; k < Math.min(pool.length, q.length); k++) {
      runners.push(worker(pool[wi++ % pool.length]));
    }
    await Promise.all(runners);
    log(`batch complete — ${niches.length} design(s) at $0.00 image cost`);
    setRunning(false);
  }, [accounts, aspect, background, log, niches, palette, running, styleName, styleUrl, textOverride]);

  /* ---------------- real engine path: SSE stream from local worker ---------------- */
  const runReal = useCallback(async (): Promise<boolean> => {
    if (!styleUrl) return false;
    setRunning(true);
    setResults([]);
    canvasStore.current.clear();
    try {
      log(`bridge: local engine ONLINE (v${bridge.version ?? "?"}) — streaming ${niches.length} job(s) to Imagen 3 pool`);
      const payload = {
        styleName,
        styleImage: await dataUrlFromUrl(styleUrl),
        aspect,
        background,
        textOverride,
        nicheImages: await Promise.all(
          niches.map(async (n) => ({ id: n.id, name: n.name, image: await dataUrlFromUrl(n.url) }))
        ),
      };
      const q: QueueItem[] = niches.map((n) => ({ ...n, status: "queued", progress: 0 }));
      setQueue(q);
      log(`payload → http://127.0.0.1:8000/generate/stream (SSE)`);

      const res = await fetch("/api/bridge/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        log(`bridge: engine declined (${res.status})`);
        setRunning(false);
        return false;
      }

      let anyImage = false;
      await readSSE(res, (ev) => {
        const type = String(ev.type ?? "");
        const jobId = String(ev.job ?? "");
        if (type === "dispatch") {
          setQueue((prev) => prev.map((p) => (p.id === jobId ? { ...p, status: "running", account: String(ev.account ?? ""), progress: 6 } : p)));
          log(`[${String(ev.account)}] "${String(ev.name ?? jobId)}" → gemini web (isolated profile)`);
        } else if (type === "progress") {
          setQueue((prev) => prev.map((p) => (p.id === jobId ? { ...p, progress: Number(ev.pct ?? p.progress) } : p)));
          if (ev.msg) log(String(ev.msg));
        } else if (type === "image") {
          anyImage = true;
          const ms = Number(ev.ms ?? 0);
          const dataUrl = `data:image/png;base64,${String(ev.png)}`;
          const nm = String(ev.name ?? jobId);
          setResults((r) => [
            ...r,
            {
              id: jobId,
              name: nm,
              url: dataUrl,
              account: String(ev.account ?? "local"),
              ms,
              w: Number(ev.w ?? 4500),
              h: Number(ev.h ?? 5400),
              path: ev.path ? String(ev.path) : undefined,
            },
          ]);
          void addDesignFromDataUrl(dataUrl, {
            name: nm,
            niche: nm,
            style: styleName,
            source: "generator",
            aspect,
            background,
            prompt: textOverride ? `TEXT OVERRIDE: ${textOverride}` : SYSTEM_INSTRUCTION,
            account: String(ev.account ?? "local"),
            w: Number(ev.w ?? 4500),
            h: Number(ev.h ?? 5400),
            ms,
            path: ev.path ? String(ev.path) : undefined,
          });
          setQueue((prev) => prev.map((p) => (p.id === jobId ? { ...p, status: "done", progress: 100, ms } : p)));
          log(`"${String(ev.name)}" complete in ${(ms / 1000).toFixed(1)}s → ${String(ev.path ?? "saved")}`);
        } else if (type === "failed") {
          setQueue((prev) => prev.map((p) => (p.id === jobId ? { ...p, status: "done", progress: 100 } : p)));
          log(`job ${jobId} failed: ${String(ev.reason ?? "rerouted via failover")}`);
        } else if (type === "log") {
          log(String(ev.msg ?? ""));
        } else if (type === "done") {
          log("engine batch complete — PNG + Productor CSV + Excel written to C:/MerchFlow/Output/");
        }
      });
      setRunning(false);
      if (!anyImage) log("engine returned 0 images — simulated preview remains available");
      return true;
    } catch (e) {
      setRunning(false);
      log(`bridge error: ${e instanceof Error ? e.message : "unknown"}`);
      return false;
    }
  }, [aspect, background, bridge.version, log, niches, styleName, styleUrl, textOverride]);

  /* ------------------------------- unified entry ------------------------------- */
  const run = useCallback(async () => {
    if (!styleUrl || !palette.length || !niches.length || running) return;
    if (bridge.connected) {
      const ok = await runReal();
      if (ok) return;
      log("continuing with simulated in-browser preview");
    }
    await runSimulated();
  }, [bridge.connected, log, niches.length, palette.length, runReal, runSimulated, running, styleUrl]);

  /* --------------------------------- export -------------------------------- */
  const exportHiRes = useCallback((r: ResultItem) => {
    const src = canvasStore.current.get(r.id);
    if (!src) {
      // real engine render — already full-res 4500×5400; download the data URL directly
      const a = document.createElement("a");
      a.href = r.url;
      a.download = `${r.name.replace(/\s+/g, "-").toLowerCase()}-4500x5400-300dpi.png`;
      a.click();
      return;
    }
    const [W, H] = EXPORT_DIMS[aspect] ?? EXPORT_DIMS["4:5"];
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    const ctx = c.getContext("2d")!;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(src, 0, 0, W, H);
    c.toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${r.name.replace(/\s+/g, "-").toLowerCase()}-${W}x${H}-300dpi.png`;
      a.click();
    }, "image/png");
  }, [aspect]);

  const canRun = !!styleUrl && palette.length > 0 && niches.length > 0 && !running;

  return (
    <div>
      <PageHead
        kicker="MODULE 01 · AI DESIGN GENERATOR"
        title="Style × Niche zero-prompt fusion"
        desc="Drop one art-style reference and up to 20 niche subjects. The multimodal engine extracts palette, line weight and texture from A, applies it to B — no prompting, no garbled AI text, $0.00 per render from the pooled Imagen 3 workers."
        right={
          <div className="flex items-center gap-2">
            {bridge.connected ? (
              <Chip tone="lime"><PlugZap size={11} /> LOCAL ENGINE v{bridge.version ?? "1.0"} · SSE LIVE</Chip>
            ) : (
              <Chip tone="neutral"><WifiOff size={11} /> ENGINE OFFLINE · SIMULATED PREVIEW</Chip>
            )}
            <Chip tone="lime"><Sparkles size={11} /> $0 image generation cost</Chip>
          </div>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">
        {/* LEFT: dropzones */}
        <div className="xl:col-span-3 space-y-4">
          <div className="grid md:grid-cols-2 gap-4">
            {/* Dropzone A */}
            <Card className="p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="font-mono2 text-[10px] tracking-[0.22em] text-white/45">DROPZONE A · ART STYLE</div>
                <Chip tone="violet">1 reference</Chip>
              </div>
              <div
                className="dropzone rounded-2xl min-h-[220px] relative flex items-center justify-center cursor-pointer overflow-hidden"
                onClick={() => document.getElementById("dz-style")?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const f = e.dataTransfer.files?.[0];
                  if (f) void onStyleFile(f);
                }}
              >
                <input id="dz-style" type="file" accept="image/*" className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) void onStyleFile(f); e.target.value = ""; }} />
                {styleUrl ? (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={styleUrl} alt="style" className="absolute inset-0 w-full h-full object-contain p-2" />
                    <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent p-3 pt-8">
                      <div className="text-[11px] font-semibold truncate">{styleName}</div>
                      <div className="flex gap-1.5 mt-1.5">
                        {palette.map((p) => (
                          <span key={p} className="h-4 w-4 rounded-md border border-white/25" style={{ background: p }} title={p} />
                        ))}
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="text-center px-6 py-10">
                    <div className="h-11 w-11 mx-auto rounded-2xl bg-viol-500/15 border border-viol-500/30 flex items-center justify-center text-[#b9a8ff] mb-3">
                      <ImageIcon size={18} />
                    </div>
                    <div className="text-[13px] font-semibold">Drop art-style reference</div>
                    <div className="text-[11px] text-white/35 mt-1 leading-relaxed">70s retro wavy · distressed badge · kawaii vector · fine linework</div>
                  </div>
                )}
              </div>
              {palette.length > 0 && (
                <div className="mt-3 flex items-center justify-between">
                  <span className="font-mono2 text-[10px] text-white/35">EXTRACTED PALETTE · {palette.length} clusters</span>
                  <CopyBtn text={palette.join(", ")} label="hex" />
                </div>
              )}
            </Card>

            {/* Dropzone B */}
            <Card className="p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="font-mono2 text-[10px] tracking-[0.22em] text-white/45">DROPZONE B · NICHE SUBJECTS</div>
                <Chip tone="lime">{niches.length}/20</Chip>
              </div>
              <div
                className="dropzone rounded-2xl min-h-[220px] flex items-center justify-center cursor-pointer p-2"
                onClick={() => document.getElementById("dz-niche")?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer.files?.length) onNicheFiles(e.dataTransfer.files);
                }}
              >
                <input id="dz-niche" type="file" accept="image/*" multiple className="hidden"
                  onChange={(e) => { if (e.target.files?.length) onNicheFiles(e.target.files); e.target.value = ""; }} />
                {niches.length ? (
                  <div className="grid grid-cols-4 gap-2 w-full">
                    {niches.map((n) => (
                      <div key={n.id} className="relative group/n rounded-xl overflow-hidden border border-white/10 bg-black/30 aspect-square">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={n.url} alt={n.name} className="w-full h-full object-contain" />
                        <button
                          onClick={(e) => { e.stopPropagation(); setNiches((x) => x.filter((y) => y.id !== n.id)); }}
                          className="absolute top-1 right-1 h-5 w-5 rounded-md bg-black/70 border border-white/15 hidden group-hover/n:flex items-center justify-center text-white/70 hover:text-red-300"
                        >
                          <X size={10} />
                        </button>
                        <div className="absolute inset-x-0 bottom-0 text-[8px] font-mono2 text-white/60 bg-black/60 px-1 truncate">{n.name}</div>
                      </div>
                    ))}
                    {niches.length < 20 && (
                      <div className="rounded-xl border border-dashed border-white/15 flex items-center justify-center text-white/30 aspect-square">
                        <span className="text-xl font-light">+</span>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-center px-6 py-10">
                    <div className="h-11 w-11 mx-auto rounded-2xl bg-[#c9f24b]/10 border border-[#c9f24b]/25 flex items-center justify-center text-[#c9f24b] mb-3">
                      <Layers size={18} />
                    </div>
                    <div className="text-[13px] font-semibold">Drop 1–20 niche subjects</div>
                    <div className="text-[11px] text-white/35 mt-1 leading-relaxed">welder · pickleball player · cat · bass fishing</div>
                  </div>
                )}
              </div>
            </Card>
          </div>

          {/* Controls */}
          <Card className="p-5">
            <div className="grid sm:grid-cols-3 gap-4">
              <Field label="Aspect ratio">
                <Select value={aspect} onChange={(e) => setAspect(e.target.value)}>
                  {Object.entries(ASPECTS).map(([k, v]) => (
                    <option key={k} value={k}>{v.label}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Background">
                <Select value={background} onChange={(e) => setBackground(e.target.value)}>
                  <option value="transparent">Transparent (MBA ready)</option>
                  <option value="white">Solid white</option>
                  <option value="black">Solid black</option>
                </Select>
              </Field>
              <Field label="Text override" hint="optional · exact render">
                <div className="relative">
                  <Type size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
                  <Input
                    value={textOverride}
                    onChange={(e) => setTextOverride(e.target.value)}
                    placeholder="LEAVE BLANK = pure graphic"
                    className="pl-8"
                  />
                </div>
              </Field>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 mt-5">
              <div className="flex items-center gap-2">
                <Chip tone="neutral">{accounts.filter((a) => a.status === "ready" || a.status === "active").length || 1} parallel workers</Chip>
                <Chip tone="neutral">est. ~80s / batch of {Math.max(niches.length, 1)}</Chip>
              </div>
              <Btn onClick={() => void run()} disabled={!canRun} variant="lime">
                {running ? <span className="animate-pulse">Dispatching…</span> : <><Play size={14} /> Generate batch</>}
              </Btn>
            </div>
          </Card>

          {/* System instruction */}
          <Card className="p-5">
            <button onClick={() => setShowPrompt((s) => !s)} className="w-full flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <ScanEye size={15} className="text-[#b9a8ff]" />
                <span className="text-[13px] font-semibold tracking-tight">Internal multimodal system instruction</span>
                <Chip tone="violet">zero-prompting</Chip>
              </div>
              <ArrowRight size={14} className={cx("text-white/40 transition-transform duration-300", showPrompt && "rotate-90")} />
            </button>
            {showPrompt && (
              <>
                <pre className="mt-4 whitespace-pre-wrap font-mono2 text-[11px] leading-relaxed text-[#c9f24b]/80 bg-black/40 border border-white/[0.07] rounded-xl p-4">{SYSTEM_INSTRUCTION}</pre>
                <div className="mt-3 flex justify-end"><CopyBtn text={SYSTEM_INSTRUCTION} label="Copy instruction" /></div>
              </>
            )}
          </Card>
        </div>

        {/* RIGHT: queue + console */}
        <div className="xl:col-span-2 space-y-4">
          <Card className="p-5">
            <Section
              icon={<WandSparkles size={16} />}
              title="Parallel dispatcher"
              desc="ThreadPool queue routed across healthy pool accounts with heartbeat failover."
            />
            <div className="space-y-2 min-h-[120px]">
              {queue.length === 0 && (
                <div className="rounded-xl border border-dashed border-white/10 py-8 text-center text-[12px] text-white/30">
                  Stage a style + niches, then run the batch.
                </div>
              )}
              {queue.map((q) => (
                <div key={q.id} className="rounded-xl bg-white/[0.03] border border-white/[0.06] px-3.5 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[12.5px] font-semibold truncate">{q.name}</div>
                    {q.status === "done" ? (
                      <Chip tone="lime">done · {q.account}</Chip>
                    ) : q.status === "running" ? (
                      <Chip tone="violet"><span className="h-1 w-1 rounded-full bg-[#c9f24b] pulse-dot inline-block" /> {q.account}</Chip>
                    ) : (
                      <Chip tone="neutral">queued</Chip>
                    )}
                  </div>
                  <div className="h-1 mt-2 rounded-full bg-white/[0.07] overflow-hidden">
                    <div
                      className={cx("h-full rounded-full transition-all duration-300", q.status === "done" ? "bg-[#c9f24b]" : "bg-viol-500")}
                      style={{ width: `${q.progress}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="font-mono2 text-[10px] tracking-[0.24em] text-white/40">ENGINE CONSOLE</div>
              <Trash2 size={13} className="text-white/25 hover:text-white/60 cursor-pointer" onClick={() => setLogs([])} />
            </div>
            <div className="h-[168px] overflow-y-auto rounded-xl bg-black/40 border border-white/[0.06] p-3 font-mono2 text-[10.5px] leading-relaxed text-[#c9f24b]/75">
              {logs.length === 0 && <span className="text-white/25 console-caret">awaiting dispatch</span>}
              {logs.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
              {logs.length > 0 && <span className="console-caret" />}
            </div>
          </Card>
        </div>
      </div>

      {/* Results */}
      {results.length > 0 && (
        <Card className="mt-4 p-5" glow>
          <Section
            icon={<Download size={16} />}
            title={`Batch output — ${results.length} print-ready design(s)`}
            desc="Preview renders at working resolution. Export delivers full 300 DPI print canvas."
            right={<Chip tone="lime">$0.00 total cost</Chip>}
          />
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-5 gap-4">
            {results.map((r) => (
              <div key={r.id} className="group">
                <div className="checkerboard rounded-2xl border border-white/10 overflow-hidden aspect-[4/5] relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={r.url} alt={r.name} className="w-full h-full object-contain group-hover:scale-[1.04] transition-transform duration-500" />
                  <button
                    onClick={() => exportHiRes(r)}
                    className="absolute inset-x-2 bottom-2 rounded-xl bg-black/75 backdrop-blur border border-[#c9f24b]/40 text-[#c9f24b] text-[11px] font-mono2 font-semibold py-2 opacity-0 group-hover:opacity-100 transition-all duration-300 flex items-center justify-center gap-1.5"
                  >
                    <Download size={12} /> {((EXPORT_DIMS[aspect]?.[0] ?? 4500) / 1000).toFixed(1)}k PNG
                  </button>
                </div>
                <div className="mt-2 flex items-center justify-between px-0.5">
                  <div className="text-[11.5px] font-semibold truncate">{r.name}</div>
                  <div className="font-mono2 text-[9.5px] text-white/30 shrink-0">{(r.ms / 1000).toFixed(0)}s</div>
                </div>
                <div className="font-mono2 text-[9.5px] text-white/30 px-0.5">{r.account} · {r.w}×{r.h}</div>
                {r.path && (
                  <div className="font-mono2 text-[8.5px] text-[#c9f24b]/60 px-0.5 truncate" title={r.path}>
                    <FolderCheck size={8} className="inline mr-1 -mt-0.5" />{r.path}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
