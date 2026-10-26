"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Download, FlaskConical, FolderCheck, ImageUp, Images, Loader2, Package, PlugZap, Trash2, WifiOff, X, Zap } from "lucide-react";
import { Btn, Card, Chip, Field, PageHead, Section, Select, Toggle, cx } from "@/components/ui";
import { probeBridge, type BridgeStatus } from "@/lib/bridge";
import { HANDOFF_EVENT, announceDesignsChanged, blobToDataUrl, downloadDesignsZip, formatBytes, getDesigns, isSavedDesign, responseJson, saveDesignBlob, takePrepareHandoff, type DesignRecord } from "@/lib/designs-store";
import { PRODUCTS, analyzeContrast, applyDistressKnockout, buildDarkVariant, canvasToBlob, computePHash, cropToArt, findDuplicates, imageToCanvas, renderProductCanvas } from "@/lib/elite-tools";

interface Settings {
  remove: boolean;
  mode: "without_ai" | "with_ai";
  precision: "keep_holes" | "everywhere";
  model: "u2net" | "birefnet-general";
  defringe: 1 | 2;
  despeckle: boolean;
  placement: "center" | "top";
  compress: boolean;
  halftone: boolean;
  lpi: number;
  ink: string;
  upscale: boolean;
  upscaleFactor: 2 | 4 | 8;
  resizeForPrint: boolean;
  distress: boolean;
  distressIntensity: number;
  darkLightSplit: boolean;
  splitStrategy: "auto" | "stroke" | "cream";
  polycanvas: boolean;
  products: string[];
  vectorize: boolean;
  dedupe: boolean;
}
const DEFAULTS: Settings = {
  remove: true, mode: "with_ai", precision: "keep_holes", model: "u2net",
  defringe: 1, despeckle: false, placement: "top", compress: true,
  halftone: false, lpi: 48, ink: "#E8E2D2", upscale: false,
  upscaleFactor: 2, resizeForPrint: true,
  distress: false, distressIntensity: 0.45, darkLightSplit: false, splitStrategy: "auto",
  polycanvas: false, products: ["hoodie", "popsocket", "tote"], vectorize: false, dedupe: true,
};
interface LabItem {
  id: string;
  name: string;
  url: string;
  thumb?: string;
  file?: File;
  from: "history" | "upload";
  status: "pending" | "running" | "done" | "failed";
  error?: string;
  saved?: DesignRecord;
  coverage?: number;
  specks?: number;
  phash?: string;
  duplicates?: string[];
  variants?: DesignRecord[];
  svg?: string;
  svgPath?: string;
}

function downloadPng(design: DesignRecord) {
  const anchor = document.createElement("a");
  anchor.href = design.fileUrl;
  anchor.download = `${design.name.replace(/[^\w-]+/g, "-")}_${design.w}x${design.h}_300dpi.png`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function SettingChoice({ selected, onClick, label, disabled = false }: { selected: boolean; onClick: () => void; label: string; disabled?: boolean }) {
  return <button type="button" aria-pressed={selected} onClick={onClick} disabled={disabled}
    className={cx("rounded-lg border px-3 py-2 text-xs font-semibold transition-colors disabled:pointer-events-none disabled:opacity-35",
      selected ? "border-[#c9f24b]/60 bg-[#c9f24b]/10 text-[#c9f24b]" : "border-white/10 bg-black/20 text-white/55 hover:text-white/85")}>
    {label}
  </button>;
}

export default function PrepareLab() {
  const [queue, setQueue] = useState<LabItem[]>([]);
  const [settings, setSettings] = useState<Settings>(DEFAULTS);
  const [running, setRunning] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [bridge, setBridge] = useState<BridgeStatus>({ connected: false });
  const [zipping, setZipping] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const busyRef = useRef(false);
  const ingestRef = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const objectUrls = useRef(new Set<string>());

  useEffect(() => {
    const probe = () => probeBridge().then(setBridge);
    void probe();
    const poll = setInterval(() => void probe(), 15000);
    return () => clearInterval(poll);
  }, []);
  useEffect(() => () => { for (const url of objectUrls.current) URL.revokeObjectURL(url); }, []);

  const ingestHistory = useCallback(async (ids: string[]) => {
    if (!ids.length) return;
    if (busyRef.current) { setError("Wait for the active batch before adding more designs."); return; }
    ingestRef.current = true;
    setIngesting(true);
    try {
      const records = await getDesigns(ids);
      if (records.length !== ids.length) throw new Error("Some selected designs are no longer available in History.");
      const items: LabItem[] = records.map((r) => ({ id: r.id, name: r.name, url: r.fileUrl, thumb: r.previewUrl, from: "history", status: "pending" }));
      setQueue((previous) => {
        const have = new Set(previous.map((item) => item.id));
        return [...previous, ...items.filter((item) => !have.has(item.id))];
      });
      setSelected((id) => id ?? items[0]?.id ?? null);
    } catch (e) { setError(e instanceof Error ? e.message : "History import failed"); }
    finally { ingestRef.current = false; setIngesting(false); }
  }, []);

  useEffect(() => {
    void ingestHistory(takePrepareHandoff());
    const handoff = () => void ingestHistory(takePrepareHandoff());
    window.addEventListener(HANDOFF_EVENT, handoff);
    return () => window.removeEventListener(HANDOFF_EVENT, handoff);
  }, [ingestHistory]);

  const onFiles = useCallback((files: FileList) => {
    if (busyRef.current || ingestRef.current) { setError("Wait for this batch to finish before adding files."); return; }
    setError(""); setNotice("");
    const items: LabItem[] = Array.from(files).map((file) => {
      const url = URL.createObjectURL(file);
      objectUrls.current.add(url);
      return { id: crypto.randomUUID(), name: file.name.replace(/\.[^.]+$/, ""), url, file, from: "upload", status: "pending" };
    });
    // No .slice(0, 50): one or fifty-plus files are all queued, never dropped.
    setQueue((previous) => [...previous, ...items]);
    setSelected((id) => id ?? items[0]?.id ?? null);
  }, []);

  /** Optional production transforms also save real PNGs BEFORE exposing them. */
  const runElite = useCallback(async (master: DesignRecord, catalog: { id: string; name: string; phash?: string }[]): Promise<Partial<LabItem>> => {
    const patch: Partial<LabItem> = { saved: master };
    const variants: DesignRecord[] = [];
    const needsPixels = settings.distress || settings.darkLightSplit || settings.polycanvas || settings.vectorize;
    let working: HTMLCanvasElement | undefined;
    if (needsPixels) working = await imageToCanvas(master.fileUrl);
    const persist = (blob: Blob, suffix: string, background = "transparent") => saveDesignBlob(blob, {
      name: `${master.name} · ${suffix}`, source: "prepare", style: suffix,
      niche: master.niche, background, account: "local",
    });
    if (settings.distress && working) {
      working = applyDistressKnockout(working, settings.distressIntensity, 7);
      patch.saved = await persist(await canvasToBlob(working), "Distress knockout");
    }
    if (settings.dedupe) {
      const small = working ?? await imageToCanvas(master.previewUrl);
      patch.phash = computePHash(small);
      patch.duplicates = findDuplicates(patch.phash, catalog, 18).map((d) => `${d.name} (${d.similarity}% similar)`);
    }
    if (settings.darkLightSplit && working) {
      const contrast = analyzeContrast(working);
      if (contrast.lowContrast || settings.splitStrategy !== "auto") {
        const { canvas } = buildDarkVariant(working, settings.splitStrategy, contrast);
        variants.push(await persist(await canvasToBlob(canvas), "Dark garment"));
      }
    }
    if (settings.polycanvas && working) {
      const art = cropToArt(working);
      for (const spec of PRODUCTS.filter((p) => settings.products.includes(p.key))) {
        const canvas = renderProductCanvas(art, spec);
        variants.push(await persist(await canvasToBlob(canvas), spec.label));
        canvas.width = canvas.height = 1;
      }
    }
    if (settings.vectorize && working) {
      const res = await fetch("/api/bridge/elite/vectorize", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: await blobToDataUrl(await canvasToBlob(working)), name: master.name }),
      });
      const vector = await responseJson<{ svg?: string; path?: string }>(res);
      if (!vector.svg || !vector.path) throw new Error("Vector engine did not confirm a saved SVG");
      patch.svg = vector.svg; patch.svgPath = vector.path;
    }
    if (working) working.width = working.height = 1;
    patch.variants = variants;
    return patch;
  }, [settings]);

  const runBatch = useCallback(async () => {
    if (busyRef.current || ingesting) return;
    const pending = queue.filter((item) => item.status === "pending" || item.status === "failed");
    if (!pending.length) return;
    busyRef.current = true;
    setRunning(true); setError(""); setNotice("");
    const catalog = queue.filter((item) => item.status === "done").map((item) => ({ id: item.id, name: item.name, phash: item.phash }));
    try {
      const status = await probeBridge();
      setBridge(status);
      if (!status.connected) throw new Error("Local engine offline. Start run_windows.bat; processing never falls back to simulated output.");
      for (const item of pending) {
        setSelected(item.id);
        setQueue((q) => q.map((i) => i.id === item.id ? { ...i, status: "running", error: undefined } : i));
        try {
          if (item.file && item.file.size > 40 * 1024 * 1024) throw new Error("Image exceeds the 40 MB per-file limit.");
          let payload: { name: string; settings: Settings } & ({ designId: string } | { image: string });
          if (item.from === "history") {
            payload = { designId: item.id, name: item.name, settings };
          } else {
            if (!item.file) throw new Error("The queued upload file is no longer available.");
            payload = { image: await blobToDataUrl(item.file), name: item.name, settings };
          }
          const res = await fetch("/api/bridge/process", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const result = await responseJson<{ design: DesignRecord; report: { coverage?: number; specks?: number } }>(res);
          if (!isSavedDesign(result.design)) throw new Error("Engine did not verify a 300 DPI PNG on disk.");
          if (settings.resizeForPrint) {
            const factor = settings.upscale ? settings.upscaleFactor : 1;
            if (result.design.w !== 4500 * factor || result.design.h !== 5400 * factor) {
              throw new Error("Engine output dimensions do not match the selected print/upscale settings.");
            }
          }
          announceDesignsChanged();
          setQueue((q) => q.map((i) => i.id === item.id ? { ...i, saved: result.design } : i));
          const elite = await runElite(result.design, catalog);
          if (elite.phash) catalog.push({ id: item.id, name: item.name, phash: elite.phash });
          setQueue((q) => q.map((i) => i.id === item.id ? { ...i, ...elite, coverage: result.report.coverage, specks: result.report.specks, status: "done" } : i));
        } catch (e) {
          setQueue((q) => q.map((i) => i.id === item.id ? { ...i, status: "failed", error: e instanceof Error ? e.message : "Processing failed" } : i));
        }
        // Await each complete pipeline, including disk/history and variants.
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Batch failed"); }
    finally { busyRef.current = false; setRunning(false); }
  }, [ingesting, queue, runElite, settings]);

  const done = queue.filter((item) => item.status === "done" && item.saved);
  const waiting = queue.filter((item) => item.status === "pending" || item.status === "failed").length;
  const failed = queue.filter((item) => item.status === "failed").length;
  const current = queue.find((item) => item.id === selected) ?? queue[0];

  const downloadAll = async () => {
    if (!done.length || zipping) return;
    setZipping(true); setError("");
    try {
      await downloadDesignsZip(done.map((item) => item.saved!.id));
      setNotice("ZIP download started. Your browser's download manager shows streaming progress.");
    } catch (e) { setError(e instanceof Error ? e.message : "ZIP download failed"); }
    finally { setZipping(false); }
  };
  const removeItem = (item: LabItem) => {
    if (busyRef.current) return;
    if (objectUrls.current.delete(item.url)) URL.revokeObjectURL(item.url);
    setQueue((q) => q.filter((i) => i.id !== item.id));
    if (selected === item.id) setSelected(null);
  };
  const clearQueue = () => {
    if (busyRef.current) return;
    for (const url of objectUrls.current) URL.revokeObjectURL(url);
    objectUrls.current.clear(); setQueue([]); setSelected(null);
  };

  return <div>
    <PageHead kicker="MODULE 04 · PREPARE DESIGNS LAB" title="Prepare Lab · three-card print pipeline"
      desc="Queue 1–50+ designs, choose background removal, optional 2×/4×/8× Lanczos resampling, and print sizing. The worker processes sequentially and streams verified PNGs to History."
      right={<Chip tone={bridge.connected ? "lime" : "neutral"}>{bridge.connected ? <PlugZap size={11} /> : <WifiOff size={11} />}{bridge.connected ? "LOCAL ENGINE" : "ENGINE OFFLINE"}</Chip>} />
    {error && <div role="alert" className="mb-4 rounded-xl border border-red-400/30 bg-red-500/10 p-4 text-sm text-red-200">{error}</div>}
    {notice && <div role="status" className="mb-4 rounded-xl border border-[#c9f24b]/30 p-3 text-xs text-[#c9f24b]">{notice}</div>}
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div className="space-y-4">
        <Card className="p-5">
          <Section icon={<Images size={16} />} title={`Batch queue · ${queue.length}`} desc="Every dropped file stays in the queue."
            right={<button aria-label="Clear queue" disabled={running || ingesting} onClick={clearQueue} className="text-white/40 disabled:opacity-25"><Trash2 size={14} /></button>} />
          <div role="button" aria-label="Upload designs" tabIndex={0} aria-disabled={running || ingesting}
            className={cx("dropzone rounded-2xl p-6 text-center mb-3", running ? "opacity-50" : "cursor-pointer")}
            onClick={() => { if (!running && !ingesting) fileInput.current?.click(); }}
            onKeyDown={(e) => { if (e.key === "Enter" && !running && !ingesting) fileInput.current?.click(); }}
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files); }}>
            <input ref={fileInput} id="lab-files" type="file" accept="image/*" multiple disabled={running || ingesting} className="hidden"
              onChange={(e) => { if (e.target.files) onFiles(e.target.files); e.target.value = ""; }} />
            <ImageUp size={20} className="mx-auto mb-2 text-[#b9a8ff]" />
            <div className="text-sm font-semibold">Drop 1–50+ designs</div>
            <div className="text-[11px] text-white/40 mt-1">PNG · JPEG · WebP · 40 MB per file</div>
          </div>
          <div className="space-y-2 max-h-[320px] overflow-y-auto">
            {queue.map((item) => <div key={item.id} className={cx("flex items-center gap-2 border rounded-xl p-2", current?.id === item.id ? "border-viol-500/50 bg-viol-500/10" : "border-white/10")}>
              <button onClick={() => setSelected(item.id)} className="flex items-center gap-2 flex-1 min-w-0 text-left">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={item.saved?.previewUrl ?? item.thumb ?? item.url} alt="" loading="lazy" className="checkerboard w-9 h-10 object-contain rounded-md" />
                <span className="min-w-0"><span className="block text-xs font-semibold truncate">{item.name}</span>
                  <span className={cx("block text-[10px]", item.status === "failed" ? "text-red-300" : "text-white/40")}>{item.status === "running" ? "Processing → verifying disk write…" : item.status}</span></span>
              </button>
              {item.status === "done" && <Check size={13} className="text-[#c9f24b]" />}
              {item.status === "running" && <Loader2 size={13} className="animate-spin" />}
              <button aria-label={`Remove ${item.name}`} disabled={running || ingesting} onClick={() => removeItem(item)} className="text-white/30 disabled:opacity-20"><X size={13} /></button>
            </div>)}
          </div>
        </Card>
        <fieldset disabled={running} className="space-y-4 disabled:opacity-60">
          <Card className="p-5 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <Section icon={<ImageUp size={16} />} title="Remove the background" desc="Choose fast border-colour removal or neural matting." />
              <Toggle ariaLabel="Remove background" on={settings.remove} set={(remove) => setSettings((s) => ({ ...s, remove }))} label={settings.remove ? "ON" : "OFF"} />
            </div>
            {settings.remove && <div className="space-y-4">
              <div>
                <div className="text-[11px] font-mono2 uppercase tracking-[0.14em] text-white/45 mb-1.5">Mode</div>
                <div role="group" aria-label="Background removal mode" className="grid grid-cols-2 gap-2">
                  <SettingChoice selected={settings.mode === "without_ai"} onClick={() => setSettings((s) => ({ ...s, mode: "without_ai" }))} label="Without AI · Fast ⚡" />
                  <SettingChoice selected={settings.mode === "with_ai"} onClick={() => setSettings((s) => ({ ...s, mode: "with_ai" }))} label="With AI · Neural 🧠" />
                </div>
              </div>
              <div>
                <div className="flex items-baseline justify-between mb-1.5">
                  <span className="text-[11px] font-mono2 uppercase tracking-[0.14em] text-white/45">Precision</span>
                  <span className="text-[10px] font-mono2 text-white/30">{settings.mode === "without_ai" ? "solid-colour mode" : "AI matting preserves natural counters"}</span>
                </div>
                <div role="group" aria-label="Cutout precision" className="grid grid-cols-2 gap-2">
                  <SettingChoice selected={settings.precision === "keep_holes"} disabled={settings.mode !== "without_ai"} onClick={() => setSettings((s) => ({ ...s, precision: "keep_holes" }))} label="Keep holes" />
                  <SettingChoice selected={settings.precision === "everywhere"} disabled={settings.mode !== "without_ai"} onClick={() => setSettings((s) => ({ ...s, precision: "everywhere" }))} label="Everywhere" />
                </div>
              </div>
              {settings.mode === "with_ai" && <Field label="Neural model">
                <Select aria-label="Neural matting model" value={settings.model} onChange={(e) => setSettings((s) => ({ ...s, model: e.target.value as Settings["model"] }))}>
                  <option value="u2net">U2Net · universal</option>
                  <option value="birefnet-general">BiRefNet · general / fine detail</option>
                </Select>
              </Field>}
              <Field label="Alpha edge choke" hint="at native/source resolution">
                <Select aria-label="Alpha choke" value={settings.defringe} onChange={(e) => setSettings((s) => ({ ...s, defringe: Number(e.target.value) as 1 | 2 }))}>
                  <option value={1}>1 px · fine lettering</option>
                  <option value={2}>2 px · stronger cleanup</option>
                </Select>
              </Field>
              <p className="text-[11px] text-white/45 leading-relaxed">
                {settings.mode === "without_ai"
                  ? "Auto-samples solid black, white, orange, navy, lime, or other flat borders. Use With AI for textured or uneven backdrops."
                  : "U2Net neural matting runs locally; its model weights may download the first time. Existing transparent counters are retained."}
              </p>
            </div>}
          </Card>

          <Card className="p-5 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <Section icon={<Zap size={16} />} title="Upscale" desc="Optional 2×, 4×, or 8× Lanczos resampling." />
              <Toggle ariaLabel="Upscale" on={settings.upscale} set={(upscale) => setSettings((s) => ({ ...s, upscale }))} label={settings.upscale ? "ON" : "OFF"} />
            </div>
            {settings.upscale ? <>
              <div>
                <div className="text-[11px] font-mono2 uppercase tracking-[0.14em] text-white/45 mb-1.5">Scale</div>
                <div role="group" aria-label="Upscale factor" className="grid grid-cols-3 gap-2">
                  {([2, 4, 8] as const).map((factor) => <SettingChoice key={factor} selected={settings.upscaleFactor === factor}
                    onClick={() => setSettings((s) => ({ ...s, upscaleFactor: factor }))} label={`${factor}×`} />)}
                </div>
              </div>
              <p className="text-[11px] text-white/45 leading-relaxed">Lanczos interpolation resamples pixels; it does not invent AI detail. Very large print canvases may take longer and use more disk space.</p>
            </> : <p className="text-[11px] text-white/45">Skipped while OFF for faster batching.</p>}
          </Card>

          <Card className="p-5 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <Section icon={<FlaskConical size={16} />} title="Resize for printing" desc="Print-ready PNG canvas with embedded 300 DPI metadata." />
              <Toggle ariaLabel="Resize for printing" on={settings.resizeForPrint} set={(resizeForPrint) => setSettings((s) => ({ ...s, resizeForPrint }))} label={settings.resizeForPrint ? "ON" : "OFF"} />
            </div>
            <div className="rounded-lg border border-[#c9f24b]/25 p-3 font-mono2 text-[11px] text-[#c9f24b]">
              {settings.resizeForPrint
                ? `Base target 4500 × 5400 px @ 300 DPI · 350 px collar margin${settings.upscale ? ` · final ${4500 * settings.upscaleFactor} × ${5400 * settings.upscaleFactor} px` : ""}`
                : `OFF · preserve source dimensions${settings.upscale ? ` · then ${settings.upscaleFactor}×` : ""}`}
            </div>
          </Card>
        </fieldset>
        <Btn variant="lime" className="w-full justify-center" onClick={() => void runBatch()} disabled={!waiting || running || ingesting || !bridge.connected}>
          {running ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
          {running ? `Preparing · ${done.length} saved / ${queue.length}` : `Prepare designs (${waiting})`}
        </Btn>
        {!bridge.connected && <p className="text-xs text-amber-200/80">Start run_windows.bat to process and save real files.</p>}
      </div>
      <div className="xl:col-span-2 space-y-4">
        <div className="grid sm:grid-cols-2 gap-4">{[false, true].map((output) => <Card key={String(output)} className="p-4">
          <div className="font-mono2 text-[10px] tracking-widest text-white/45 mb-3">
            {output ? `SAVED OUTPUT · ${current?.saved ? `${current.saved.w}×${current.saved.h}` : "PRINT READY"}` : "INPUT"}
          </div>
          <div className="checkerboard border border-white/10 rounded-xl aspect-[5/6] flex items-center justify-center relative overflow-hidden">
            {(output ? current?.saved : current) ? <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={output ? current!.saved!.previewUrl : (current!.thumb ?? current!.url)} alt={output ? "Prepared output" : "Original input"} className="max-w-full max-h-full object-contain" />
            </> : <span className="text-xs text-white/30">{output ? "Run the pipeline" : "Queue is empty"}</span>}
            {output && current?.status === "running" && <div className="absolute inset-0 bg-black/60 flex items-center justify-center"><Loader2 size={24} className="animate-spin text-[#b9a8ff]" /></div>}
          </div>
        </Card>)}</div>
        <Card className="p-5 space-y-4">
          <div className="flex flex-wrap gap-3 text-xs text-white/50">
            <Chip tone="lime">{done.length}/{queue.length} prepared</Chip>{failed > 0 && <Chip tone="amber">{failed} failed</Chip>}
            {current?.saved && <><span>{formatBytes(current.saved.bytes)}</span><span>{current.saved.w}×{current.saved.h} · {current.saved.dpi} DPI</span></>}
            {current?.coverage != null && <span>Ink coverage {current.coverage}%</span>}
            {current?.specks != null && <span>{current.specks} micro-dot pixels removed</span>}
          </div>
          {current?.error && <div role="alert" className="text-xs text-red-300 break-words">{current.error}{current.saved && " The base PNG remains saved in History."}</div>}
          {current?.saved && <div className="flex items-center gap-2 text-[#c9f24b] font-mono2 text-[11px] break-all"><FolderCheck size={15} className="shrink-0" />{current.saved.path}</div>}
          {current?.duplicates?.length ? <div className="text-xs text-amber-200">Near-duplicates: {current.duplicates.join(", ")}</div> : current?.phash ? <div className="text-xs text-white/40">No near-duplicates among earlier designs in this batch.</div> : null}
          <div className="flex flex-wrap gap-2 justify-end">
            <Btn variant="ghost" disabled={!current?.saved} onClick={() => current?.saved && downloadPng(current.saved)}><Download size={14} /> PNG</Btn>
            <Btn variant="lime" disabled={!done.length || zipping || running} onClick={() => void downloadAll()}>{zipping ? <Loader2 size={14} className="animate-spin" /> : <Package size={14} />}Download all ({done.length})</Btn>
          </div>
        </Card>
        {done.length > 0 && <Card className="p-5"><Section icon={<Images size={16} />} title={`Print-ready files · ${done.length}`} desc="These previews refer to verified files on disk, not browser-only buffers." />
          <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">{done.map((item) => <button key={item.id} onClick={() => setSelected(item.id)} className="text-left">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={item.saved!.previewUrl} alt={item.name} loading="lazy" className="checkerboard rounded-xl border border-white/10 aspect-[5/6] w-full object-contain" />
            <div className="text-[11px] truncate mt-2">{item.name}</div><div className="text-[10px] text-white/35">{formatBytes(item.saved!.bytes)}</div>
          </button>)}</div>
        </Card>}
        {!!current?.variants?.length && <Card className="p-5"><Section icon={<Images size={15} />} title="Saved variants" desc="Each PNG is also in Designs History." />
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">{current.variants.map((variant) => <button key={variant.id} className="text-left" onClick={() => downloadPng(variant)}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={variant.previewUrl} alt={variant.name} className="checkerboard aspect-square rounded-xl object-contain w-full" /><div className="text-xs mt-2 truncate">{variant.name}</div><div className="text-[10px] text-white/35">{variant.w}×{variant.h} · PNG</div>
          </button>)}</div>
        </Card>}
        {current?.svg && <Card className="p-5"><div className="text-xs text-[#c9f24b] break-all">SVG saved: {current.svgPath}</div>
          <Btn variant="ghost" className="mt-3" onClick={() => {
            const url = URL.createObjectURL(new Blob([current.svg!], { type: "image/svg+xml" }));
            const a = document.createElement("a"); a.href = url; a.download = `${current.name}.svg`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
          }}><Download size={13} /> Download SVG</Btn>
        </Card>}
      </div>
    </div>
  </div>;
}
