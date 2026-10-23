"use client";

import { useEffect, useRef, useState } from "react";
import { ImageUp, Layers, Loader2, Play, PlugZap, WifiOff, X } from "lucide-react";
import { Btn, Card, Chip, Field, Input, PageHead, Section, Select } from "@/components/ui";
import { GenerationOutputs, ProductionQueue } from "@/components/generation-output";
import { probeBridge, type BridgeStatus } from "@/lib/bridge";
import { useGeneration, type ReferenceItem } from "@/lib/use-generation";

export default function Generator() {
  const [style, setStyle] = useState<ReferenceItem | null>(null);
  const [niches, setNiches] = useState<ReferenceItem[]>([]);
  const [aspect, setAspect] = useState("4:5");
  // The mandated fusion directive renders on pure pitch-black (#000000);
  // other choices emit an explicit operator override clause in the prompt.
  const [background, setBackground] = useState("black");
  const [model, setModel] = useState("u2net");
  const [text, setText] = useState("");
  const [bridge, setBridge] = useState<BridgeStatus>({ connected: false });
  const [inputError, setInputError] = useState("");
  const styleInput = useRef<HTMLInputElement>(null);
  const nicheInput = useRef<HTMLInputElement>(null);
  const urls = useRef(new Set<string>());
  const generation = useGeneration();

  useEffect(() => {
    const probe = () => probeBridge().then(setBridge);
    void probe(); const timer = setInterval(() => void probe(), 15000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => () => { for (const url of urls.current) URL.revokeObjectURL(url); }, []);

  const reference = (file: File): ReferenceItem => {
    const url = URL.createObjectURL(file); urls.current.add(url);
    return { id: crypto.randomUUID(), name: file.name.replace(/\.[^.]+$/, ""), url };
  };
  const loadStyle = (file?: File) => {
    if (!file || generation.running) return;
    if (file.size > 40 * 1024 * 1024) { setInputError("The style image exceeds 40 MB."); return; }
    if (style) { URL.revokeObjectURL(style.url); urls.current.delete(style.url); }
    setStyle(reference(file)); setInputError("");
  };
  const loadNiches = (files: FileList) => {
    if (generation.running) return;
    const list = Array.from(files);
    if (list.some((file) => file.size > 40 * 1024 * 1024)) { setInputError("Each image must be 40 MB or smaller; no files were added."); return; }
    setNiches((previous) => [...previous, ...list.map(reference)]); setInputError("");
  };
  const removeNiche = (item: ReferenceItem) => {
    URL.revokeObjectURL(item.url); urls.current.delete(item.url);
    setNiches((previous) => previous.filter((n) => n.id !== item.id));
  };

  return <div>
    <PageHead kicker="MODULE 01 · AI DESIGN GENERATOR" title="Style × Niche fusion"
      desc="Drop niche subjects (IMAGE 1) and the art-style reference (IMAGE 2). Every job injects the mandatory Style × Niche Complete Fusion multimodal directive verbatim; the local worker mats, verifies, and saves genuine 300 DPI PNGs."
      right={<Chip tone={bridge.connected && !bridge.mock ? "lime" : "neutral"}>{bridge.connected ? <PlugZap size={11} /> : <WifiOff size={11} />}{bridge.mock ? "MOCK MODE · GENERATION BLOCKED" : bridge.connected ? "LOCAL ENGINE" : "ENGINE OFFLINE"}</Chip>} />
    {(inputError || generation.error) && <div role="alert" className="mb-4 rounded-xl border border-red-400/30 bg-red-500/10 p-4 text-sm text-red-200">{inputError || generation.error}</div>}
    <div className="grid xl:grid-cols-5 gap-4">
      <div className="xl:col-span-3 space-y-4">
        <div className="grid md:grid-cols-2 gap-4">
          <Card className="p-4"><Section icon={<ImageUp size={15} />} title="A · Art style · IMAGE 2" desc="Visual DNA: palette, linework, medium, shading." />
            <div role="button" aria-label="Upload style reference" tabIndex={0} className="dropzone rounded-xl h-[270px] flex items-center justify-center cursor-pointer overflow-hidden"
              onClick={() => { if (!generation.running) styleInput.current?.click(); }}
              onKeyDown={(e) => { if (e.key === "Enter" && !generation.running) styleInput.current?.click(); }}
              onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); loadStyle(e.dataTransfer.files[0]); }}>
              <input ref={styleInput} id="dz-style" type="file" accept="image/*" disabled={generation.running} className="hidden" onChange={(e) => { loadStyle(e.target.files?.[0]); e.target.value = ""; }} />
              {style ? <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={style.url} alt={style.name} className="w-full h-full object-contain p-2" />
              </> : <div className="text-center text-xs text-white/40 px-4">Drop one art-style reference</div>}
            </div>
            {style && <p className="text-xs mt-3 truncate">{style.name}</p>}
          </Card>
          <Card className="p-4"><Section icon={<Layers size={15} />} title={`B · Niche subjects · IMAGE 1 · ${niches.length}`} desc="All dropped images are queued sequentially." />
            <div role="button" aria-label="Upload niche subjects" tabIndex={0} className="dropzone rounded-xl h-[270px] overflow-y-auto cursor-pointer p-2"
              onClick={() => { if (!generation.running) nicheInput.current?.click(); }}
              onKeyDown={(e) => { if (e.key === "Enter" && !generation.running) nicheInput.current?.click(); }}
              onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); loadNiches(e.dataTransfer.files); }}>
              <input ref={nicheInput} id="dz-niche" type="file" accept="image/*" multiple disabled={generation.running} className="hidden" onChange={(e) => { if (e.target.files) loadNiches(e.target.files); e.target.value = ""; }} />
              {niches.length ? <div className="grid grid-cols-3 gap-2">{niches.map((niche) => <div key={niche.id} className="relative border border-white/10 rounded-lg aspect-square overflow-hidden">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={niche.url} alt={niche.name} loading="lazy" className="w-full h-full object-contain" />
                <button aria-label={`Remove ${niche.name}`} disabled={generation.running} onClick={(e) => { e.stopPropagation(); removeNiche(niche); }} className="absolute right-1 top-1 rounded bg-black/70 p-1"><X size={10} /></button>
              </div>)}</div> : <div className="h-full flex items-center justify-center text-center text-xs text-white/40">Drop niche subjects · PNG / JPEG / WebP</div>}
            </div>
          </Card>
        </div>
        <Card className="p-5"><fieldset disabled={generation.running} className="grid sm:grid-cols-2 gap-4">
          <Field label="Print canvas"><Select aria-label="Print canvas" value={aspect} onChange={(e) => setAspect(e.target.value)}><option value="4:5">Apparel · 4500×5400</option><option value="1:1">Square · 4500×4500</option><option value="3:4">Portrait · 4050×5400</option></Select></Field>
          <Field label="Background"><Select aria-label="Background" value={background} onChange={(e) => setBackground(e.target.value)}><option value="transparent">Transparent</option><option value="black">Solid Black</option><option value="white">Solid White</option></Select></Field>
          <Field label="Neural cutout"><Select aria-label="Neural cutout" value={model} onChange={(e) => setModel(e.target.value)}><option value="u2net">U2Net</option><option value="birefnet-general">BiRefNet General</option></Select></Field>
          <Field label="Exact text override" hint="optional"><Input aria-label="Exact text override" value={text} onChange={(e) => setText(e.target.value)} placeholder="Blank = graphic artwork only" /></Field>
        </fieldset>
          <div className="flex items-center justify-between gap-3 mt-5"><span className="text-[11px] text-white/40">Real provider account required · no simulated fallback</span>
            <Btn variant="lime" disabled={!style || !niches.length || generation.running || !bridge.connected || bridge.mock} onClick={() => void generation.run(niches, { endpoint: "/api/bridge/generate", styleUrl: style!.url, styleName: style!.name, aspect, background, model, textOverride: text })}>
              {generation.running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}{generation.running ? "Generating…" : `Generate batch (${niches.length})`}
            </Btn>
          </div>
        </Card>
        <Card className="p-4 text-xs text-white/40 leading-relaxed">Every fusion job sends both references to the provider paired with the mandatory <em>Style × Niche Complete Fusion</em> directive verbatim — IMAGE 1 niche subject fully re-drawn inside IMAGE 2&rsquo;s visual DNA, 8K apparel quality, pitch-black (#000000) background unless an operator override below relaxes it. The provider determines render quality, text accuracy, account limits and costs. Successful files are saved under <code>{bridge.outDir ?? "output/"}</code> and archived before the UI reports completion. An unavailable provider, empty response or failed disk write is a failed job.</Card>
      </div>
      <div className="xl:col-span-2"><ProductionQueue jobs={generation.jobs} logs={generation.logs} /></div>
    </div>
    <GenerationOutputs results={generation.results} />
  </div>;
}
