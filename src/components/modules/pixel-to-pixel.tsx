"use client";

import { useEffect, useRef, useState } from "react";
import { ImageUp, Loader2, ScanEye, Sparkles, X } from "lucide-react";
import { Btn, Card, Chip, Field, PageHead, Section, Select, TextArea, cx } from "@/components/ui";
import { GenerationOutputs, ProductionQueue } from "@/components/generation-output";
import { probeBridge, type BridgeStatus } from "@/lib/bridge";
import { useGeneration, type ReferenceItem } from "@/lib/use-generation";

const BACKGROUNDS = [
  { key: "transparent", label: "Transparent", colour: undefined },
  { key: "black", label: "Solid Black", colour: "#000000" },
  { key: "white", label: "Solid White", colour: "#ffffff" },
  { key: "original", label: "Keep Original", colour: "#514373" },
] as const;

export default function PixelToPixel() {
  const [references, setReferences] = useState<ReferenceItem[]>([]);
  const [background, setBackground] = useState<string>("transparent");
  const [model, setModel] = useState("u2net");
  const [instructions, setInstructions] = useState("");
  const [bridge, setBridge] = useState<BridgeStatus>({ connected: false });
  const [inputError, setInputError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const urls = useRef(new Set<string>());
  const generation = useGeneration();

  useEffect(() => {
    const probe = () => probeBridge().then(setBridge);
    void probe(); const timer = setInterval(() => void probe(), 15000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => () => { for (const url of urls.current) URL.revokeObjectURL(url); }, []);

  const addFiles = (files: FileList) => {
    if (generation.running) return;
    const list = Array.from(files);
    if (list.some((file) => file.size > 40 * 1024 * 1024)) { setInputError("Each concept must be 40 MB or smaller; no files were added."); return; }
    const items = list.map((file) => {
      const url = URL.createObjectURL(file); urls.current.add(url);
      return { id: crypto.randomUUID(), name: file.name.replace(/\.[^.]+$/, ""), url };
    });
    setReferences((previous) => [...previous, ...items]); setInputError("");
  };
  const remove = (reference: ReferenceItem) => {
    URL.revokeObjectURL(reference.url); urls.current.delete(reference.url);
    setReferences((previous) => previous.filter((item) => item.id !== reference.id));
  };

  return <div>
    <PageHead kicker="MODULE 03 · PIXEL TO PIXEL" title="Recreate the concept. Export in 8K."
      desc="A real image-provider recreation of your uploaded design concept, preserving its subject, composition, visual style and legible text. No palette-transfer substitute or fake finished preview."
      right={<Chip tone={bridge.connected && !bridge.mock ? "lime" : "neutral"}><ScanEye size={12} />{bridge.mock ? "MOCK MODE · BLOCKED" : bridge.connected ? "PROVIDER + LOCAL EXPORT" : "ENGINE OFFLINE"}</Chip>} />
    {(inputError || generation.error) && <div role="alert" className="mb-4 border border-red-400/30 rounded-xl bg-red-500/10 p-4 text-sm text-red-200">{inputError || generation.error}</div>}
    <div className="grid xl:grid-cols-5 gap-4">
      <div className="xl:col-span-3 space-y-4">
        <Card className="p-5">
          <Section icon={<ImageUp size={16} />} title={`Concept references · ${references.length}`} desc="Drop one or many images; each is recreated as a separate design." />
          <div role="button" aria-label="Upload concept references" tabIndex={0} className="dropzone rounded-2xl p-5 min-h-[250px] cursor-pointer"
            onClick={() => { if (!generation.running) input.current?.click(); }}
            onKeyDown={(e) => { if (e.key === "Enter" && !generation.running) input.current?.click(); }}
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}>
            <input ref={input} id="p2p-files" type="file" accept="image/*" multiple disabled={generation.running} className="hidden" onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
            {references.length ? <div className="grid grid-cols-3 sm:grid-cols-4 gap-3 max-h-[420px] overflow-y-auto">{references.map((reference) => <div key={reference.id} className="relative rounded-xl border border-white/10 checkerboard aspect-square overflow-hidden">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={reference.url} alt={reference.name} loading="lazy" className="w-full h-full object-contain" />
              <button aria-label={`Remove ${reference.name}`} disabled={generation.running} onClick={(e) => { e.stopPropagation(); remove(reference); }} className="absolute top-1 right-1 bg-black/70 p-1 rounded"><X size={12} /></button>
              <div className="absolute bottom-0 inset-x-0 bg-black/70 text-[9px] p-1 truncate">{reference.name}</div>
            </div>)}</div> : <div className="text-center py-14"><ImageUp size={24} className="mx-auto text-[#b9a8ff] mb-3" /><div className="text-sm font-semibold">Drop the design concept</div><div className="text-xs text-white/40 mt-2">PNG · JPEG · WebP · up to 40 MB per image</div></div>}
          </div>
        </Card>
        <Card className="p-5"><fieldset disabled={generation.running} className="space-y-5">
          <div><div className="text-xs font-semibold mb-2">Background</div><div role="group" aria-label="Recreation background" className="grid grid-cols-2 sm:grid-cols-4 gap-2">{BACKGROUNDS.map((option) => <button key={option.key} aria-label={option.label} aria-pressed={background === option.key} onClick={() => setBackground(option.key)}
            className={cx("rounded-xl border p-3 text-left text-[11px] font-semibold transition-colors", background === option.key ? "bg-viol-500/20 border-viol-500/60 text-white" : "border-white/10 text-white/50")}>
            <span className={cx("block w-6 h-6 mb-2 rounded-md border border-white/20", !option.colour && "checkerboard")} style={{ backgroundColor: option.colour }} />{option.label}
          </button>)}</div></div>
          <p className="text-[11px] text-white/40 leading-relaxed">{background === "original"
            ? "Keep Original asks the provider to preserve the reference backdrop. Export bypasses background removal and retains the recreated backdrop; generative results can differ from the source."
            : "Neural foreground matting removes the rendered backdrop. Transparent keeps alpha; Solid Black and Solid White composite onto exact RGB 0 or 255 after the 1px alpha choke."}</p>
          {background !== "original" && <Field label="Neural matting model"><Select aria-label="Recreation matting model" value={model} onChange={(e) => setModel(e.target.value)}><option value="u2net">U2Net · universal</option><option value="birefnet-general">BiRefNet · fine detail</option></Select></Field>}
          <Field label="Additional direction" hint="optional"><TextArea aria-label="Additional recreation direction" rows={3} maxLength={4000} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="For example: refine the lightning detail and preserve the existing lettering." /></Field>
        </fieldset>
          <div className="mt-5 flex justify-between items-center gap-3"><Chip tone="violet">8192 px longest edge · 300 DPI PNG</Chip>
            <Btn variant="lime" disabled={!references.length || generation.running || !bridge.connected || bridge.mock} onClick={() => void generation.run(references, { endpoint: "/api/bridge/pixel-to-pixel", background, model, instructions })}>
              {generation.running ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}{generation.running ? "Recreating…" : `Recreate in 8K (${references.length})`}
            </Btn>
          </div>
        </Card>
        <Card className="p-4 text-xs text-white/40 leading-relaxed">
          <strong className="text-white/70">What “8K” means here:</strong> the saved PNG is 8192 pixels on its longest edge, preserving the rendered aspect ratio. The provider may return a smaller native image; the export then uses Lanczos upscaling, not native 8K generation. Native dimensions are shown for every result. An authenticated provider account is required, and all verified files appear in Designs History.
        </Card>
      </div>
      <div className="xl:col-span-2"><ProductionQueue jobs={generation.jobs} logs={generation.logs} /></div>
    </div>
    <GenerationOutputs results={generation.results} />
  </div>;
}
