"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, History, ImageUp, Loader2, Palette, Sparkles, X } from "lucide-react";
import { Btn, Card, Chip, PageHead, Section, cx } from "@/components/ui";
import { probeBridge, type BridgeStatus } from "@/lib/bridge";
import { COLOUR_SWATCHES, generationLabel, normaliseHex, type PixelInput } from "@/lib/p2p-client";
import { usePixelBatch } from "@/lib/use-p2p-batch";
import { formatBytes } from "@/lib/designs-store";
import type { ViewKey } from "@/components/shell";

export default function PixelToPixel({ setView }: { setView: (view: ViewKey) => void }) {
  const [inputs, setInputs] = useState<PixelInput[]>([]);
  const [colour, setColour] = useState("#000000");
  const [bridge, setBridge] = useState<BridgeStatus>({ connected: false });
  const [inputError, setInputError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const urls = useRef(new Set<string>());
  const generation = usePixelBatch();
  const batch = generation.batch;

  useEffect(() => {
    const probe = () => probeBridge().then(setBridge);
    void probe(); const timer = setInterval(() => void probe(), 15000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => () => { for (const url of urls.current) URL.revokeObjectURL(url); }, []);


  const addFiles = (files: FileList) => {
    if (generation.running) return;
    const accepted: PixelInput[] = [];
    let skipped = 0;
    for (const file of Array.from(files)) {
      if ((!file.type.startsWith("image/") && !/\.(png|jpe?g|webp|avif|gif|tiff?|bmp|heic)$/i.test(file.name)) || file.size > 40 * 1024 * 1024 || !file.size) { skipped++; continue; }
      const url = URL.createObjectURL(file); urls.current.add(url);
      accepted.push({ id: crypto.randomUUID(), file, url });
    }
    setInputs((previous) => [...previous, ...accepted]);
    setInputError(skipped ? `${skipped} empty, unsupported or over-40-MB files were skipped; other images were added.` : "");
  };
  const remove = (item: PixelInput) => {
    URL.revokeObjectURL(item.url); urls.current.delete(item.url);
    setInputs((previous) => previous.filter((input) => input.id !== item.id));
  };
  const clear = () => {
    for (const url of urls.current) URL.revokeObjectURL(url);
    urls.current.clear(); setInputs([]); setInputError("");
  };
  const generate = async () => {
    await generation.run(inputs, normaliseHex(colour));
  };

  return <div>
    <PageHead kicker="MODULE 03 · DONEPIXEL PIXEL TO PIXEL" title="Your designs. Back on your colour."
      desc="Drop any batch size—1, 5, 20, 50, 100 or more. Available Gemini Pro accounts recreate designs in parallel; exactly one verified PNG per input is saved to output/ and auto-archived in Designs History."
      right={<Chip tone={bridge.connected && !bridge.mock ? "lime" : "neutral"}>{bridge.mock ? "MOCK MODE · BLOCKED" : bridge.connected ? "DISK + LIVE HISTORY" : "ENGINE OFFLINE"}</Chip>} />
    {(inputError || generation.error) && <div role="alert" className="mb-4 border border-red-400/30 rounded-xl bg-red-500/10 p-4 text-sm text-red-200">{inputError || generation.error}</div>}
    <div className="grid xl:grid-cols-5 gap-4">
      <div className="xl:col-span-3 space-y-4">
        <Card className="p-5">
          <div className="flex justify-between gap-3 items-start">
            <Section icon={<ImageUp size={16} />} title="Upload your designs" desc="Multi-image dropzone · no fixed batch count or 100-design cap" />
            <Btn variant="ghost" disabled={!inputs.length || generation.running} onClick={clear}>Clear all</Btn>
          </div>
          <div className="mb-3"><Chip tone="violet"><span data-testid="p2p-upload-count" aria-live="polite">{inputs.length} designs uploaded</span></Chip></div>
          <div role="button" aria-label="Upload design screenshots" tabIndex={0} className="dropzone rounded-2xl p-5 min-h-[240px] cursor-pointer"
            onClick={() => { if (!generation.running) fileInput.current?.click(); }}
            onKeyDown={(e) => { if (["Enter", " "].includes(e.key)) { e.preventDefault(); if (!generation.running) fileInput.current?.click(); } }}
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}>
            <input ref={fileInput} id="p2p-files" type="file" accept="image/*" multiple={true} disabled={generation.running} className="hidden" onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
            {inputs.length ? <div className="grid grid-cols-3 sm:grid-cols-4 gap-3 max-h-[440px] overflow-y-auto">{inputs.map((item) => <div key={item.id} className="relative rounded-xl border border-white/10 checkerboard aspect-square overflow-hidden">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={item.url} alt={item.file.name} loading="lazy" className="w-full h-full object-contain" />
              <button type="button" aria-label={`Remove ${item.file.name}`} disabled={generation.running} onClick={(e) => { e.stopPropagation(); remove(item); }} className="absolute top-1 right-1 bg-black/75 p-1.5 rounded-md"><X size={12} /></button>
              <div className="absolute bottom-0 inset-x-0 bg-black/70 text-[9px] p-1 truncate">{item.file.name}</div>
            </div>)}</div> : <div className="text-center py-12"><ImageUp size={28} className="mx-auto text-[#b9a8ff] mb-3" /><div className="text-sm font-semibold">Drop 1 to 100+ design screenshots</div><div className="text-xs text-white/40 mt-2">PNG · JPEG · WebP and supported image formats · 40 MB per upload</div></div>}
          </div>
        </Card>
        <Card className="p-5">
          <fieldset disabled={generation.running}>
            <Section icon={<Palette size={16} />} title="Target background" desc="Strict 1:1 recreation · one input image → exactly one PNG · no variants" />
            <div role="radiogroup" aria-label="Target background colour" className="grid grid-cols-2 gap-3">
              {COLOUR_SWATCHES.map((option) => <button key={option.hex} type="button" role="radio" aria-label={option.label} aria-checked={colour === option.hex}
                onClick={() => setColour(option.hex)} className={cx("rounded-2xl border-2 p-5 text-left font-semibold transition-colors", colour === option.hex ? "border-[#c9f24b]/70 bg-[#c9f24b]/10" : "border-white/15 text-white/60")}>
                <span className="flex items-center justify-center w-12 h-12 mb-3 rounded-full border border-white/30" style={{ backgroundColor: option.hex }}>{colour === option.hex && <Check size={23} className={option.hex === "#FFFFFF" ? "text-black" : "text-white"} />}</span>
                <span className="text-base">{option.label}</span><span className="block text-xs font-mono2 text-white/50 mt-1">{option.hex}</span>
              </button>)}
            </div>
          </fieldset>
          <div className="mt-5 flex flex-col gap-3">
            <Btn variant="lime" className="justify-center" disabled={!inputs.length || generation.running || !bridge.connected || bridge.mock || bridge.capabilities?.donepixelBatch === false} onClick={() => void generate()}>
              {generation.running ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}{generation.uploading ? generation.uploadProgress : generation.running ? `${batch?.completed ?? 0} / ${batch?.total ?? inputs.length} archived · generating…` : generationLabel(inputs.length)}
            </Btn>
          </div>
        </Card>
        <Card className="p-4 text-xs text-white/45 leading-relaxed">
          <strong className="text-white/75">Real files, not a browser download:</strong> streamed libvips export → 4500 × 5400 RGBA PNG → 300 DPI → verified disk write → SQLite History → live update. Lossless compression only; no palette or colour reduction. Files above 4 MB remain valid below the 25 MB ceiling. Strictly one output per input, never candidate sets. The exact prompt requests 8K clarity; actual provider resolution varies, and the final print canvas is 4500 × 5400, with aspect ratio preserved and colour-matched padding.
        </Card>
      </div>
      <div className="xl:col-span-2 space-y-4">
        <Card className="p-5">
          <Section icon={<History size={16} />} title="Automatic archive" desc="Uploads are spooled to disk; queued generation continues when you switch modules or disconnect from events." />
          <Btn variant="primary" className="w-full justify-center" onClick={() => setView("history")}><History size={14} />Open Designs History <ArrowRight size={14} /></Btn>
          {generation.uploading && <p className="text-xs text-amber-200 mt-3">Keep this browser open until uploads finish. Once queued, the engine owns the batch.</p>}
          {batch && <div className="mt-5">
            <div className="flex flex-wrap gap-2 mb-4"><Chip tone="violet">{batch.total} designs</Chip><Chip tone="lime">{batch.completed} archived</Chip>{batch.failed > 0 && <Chip tone="red">{batch.failed} failed</Chip>}</div>
            <div data-testid="p2p-batch-status" className="text-xs text-white/50 mb-3">Batch {batch.id.slice(0, 8)} · {batch.status} · {batch.background}</div>
            {batch.status === "uploading" && !generation.uploading && <Btn variant="ghost" onClick={() => void generation.discard()}>Discard unfinished upload</Btn>}
            <div className="space-y-2 max-h-[680px] overflow-y-auto">{batch.items.map((job) => <div key={job.id} className="rounded-xl border border-white/10 p-3 flex items-center gap-3">
              {job.design ? <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={job.design.previewUrl} alt={job.name} loading="lazy" className="w-12 h-14 object-contain rounded bg-black/30" />
              </> : <div className="w-12 h-14 rounded bg-white/5 flex items-center justify-center shrink-0">{["rendering", "saving"].includes(job.state) ? <Loader2 size={16} className="animate-spin text-[#b9a8ff]" /> : <ImageUp size={16} className="text-white/25" />}</div>}
              <div className="min-w-0 flex-1"><div className="text-xs truncate font-semibold">{job.name}</div><div className={cx("text-[10px] mt-1", job.state === "succeeded" ? "text-[#c9f24b]" : job.state === "failed" ? "text-red-300" : "text-white/40")}>{job.state === "succeeded" ? "Saved to disk + Designs History" : job.state} {job.account && `· ${job.account}`}</div>
                {job.design && <div className="text-[9px] text-white/35 mt-1">4500 × 5400 · 300 DPI · {formatBytes(job.design.bytes)} · lossless RGBA
                  {job.design.nativeW && <div>Provider {job.design.nativeW} × {job.design.nativeH} · {job.design.upscaled ? "Lanczos-upscaled to print" : "aspect-fit print export"}</div>}
                </div>}
                {job.reason && <div className="text-[10px] text-red-200 mt-1 break-words">{job.reason}</div>}
              </div>
            </div>)}</div>
          </div>}
        </Card>
      </div>
    </div>
  </div>;
}
