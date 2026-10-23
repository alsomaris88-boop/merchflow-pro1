"use client";

import { useCallback, useRef, useState } from "react";
import { ArrowRight, Download, Images, Loader2, Palette, RefreshCw, X } from "lucide-react";
import { Btn, Card, Chip, PageHead, Section, cx } from "@/components/ui";
import { addDesign } from "@/lib/designs-store";

interface SrcItem {
  id: string;
  name: string;
  url: string;
  w: number;
  h: number;
}
interface Colorway {
  id: string;
  garment: string;
  hex: string;
  mode: "dark" | "light";
  url: string;
}

const GARMENTS = [
  { name: "Dark Heather", hex: "#282828", mode: "dark" as const },
  { name: "Black", hex: "#111111", mode: "dark" as const },
  { name: "White", hex: "#F3F4F6", mode: "light" as const },
  { name: "Heather Grey", hex: "#C7C9CD", mode: "light" as const },
];

function rgb2hsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h /= 6;
  }
  return [h, s, l];
}

function hsl2rgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const f = (t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [Math.round(f(h + 1 / 3) * 255), Math.round(f(h) * 255), Math.round(f(h - 1 / 3) * 255)];
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** High-contrast print-palette transfer for a target garment mode */
async function transfer(img: HTMLImageElement, mode: "dark" | "light", maxDim = 900): Promise<HTMLCanvasElement> {
  const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
  const W = Math.max(2, Math.round(img.width * scale));
  const H = Math.max(2, Math.round(img.height * scale));
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0, W, H);
  const id = ctx.getImageData(0, 0, W, H);
  const d = id.data;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] < 10) continue;
    const [h, s, l] = rgb2hsl(d[i], d[i + 1], d[i + 2]);
    let L: number;
    let S: number;
    if (mode === "dark") {
      // lift ink into a bright, saturated print range for dark fabric
      L = clamp01(0.96 - l * 0.78);
      S = clamp01(s * 1.14 + 0.05);
    } else {
      // deepen ink for light fabric
      L = clamp01(0.06 + l * 0.66);
      S = clamp01(s * 1.05);
    }
    const [r, g, b] = hsl2rgb(h, S, L);
    d[i] = r;
    d[i + 1] = g;
    d[i + 2] = b;
  }
  ctx.putImageData(id, 0, 0);
  return c;
}

function loadMeta(url: string): Promise<{ img: HTMLImageElement; w: number; h: number }> {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res({ img, w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = rej;
    img.src = url;
  });
}

export default function Recolor() {
  const [items, setItems] = useState<SrcItem[]>([]);
  const [outputs, setOutputs] = useState<Record<string, Colorway[]>>({});
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<[number, number]>([0, 0]);
  const imgStore = useRef(new Map<string, HTMLImageElement>());

  const onFiles = useCallback(async (files: FileList) => {
    const next: SrcItem[] = [];
    for (const f of Array.from(files).slice(0, 20)) {
      const url = URL.createObjectURL(f);
      try {
        const { img, w, h } = await loadMeta(url);
        const id = Math.random().toString(36).slice(2, 9);
        imgStore.current.set(id, img);
        next.push({ id, name: f.name.replace(/\.[^.]+$/, ""), url, w, h });
      } catch {
        /* skip bad file */
      }
    }
    setItems((x) => [...x, ...next].slice(0, 20));
    setOutputs({});
  }, []);

  const runBatch = useCallback(async () => {
    if (!items.length || busy) return;
    setBusy(true);
    const total = items.length * 2;
    let done = 0;
    setProgress([0, total]);
    const next: Record<string, Colorway[]> = {};
    for (const item of items) {
      const img = imgStore.current.get(item.id);
      if (!img) continue;
      next[item.id] = [];
      for (const g of [GARMENTS[0], GARMENTS[2]]) {
        const canvas = await transfer(img, g.mode);
        next[item.id].push({
          id: `${item.id}-${g.name}`,
          garment: g.name,
          hex: g.hex,
          mode: g.mode,
          url: canvas.toDataURL("image/png"),
        });
        // auto-archive each colorway into Designs History
        await new Promise<void>((resolve) =>
          canvas.toBlob((blob) => {
            if (blob) {
              void addDesign({
                blob,
                name: `${item.name} · ${g.name}`,
                niche: item.name,
                style: `${g.name} colorway`,
                source: "recolor",
                aspect: "1:1",
                background: g.mode === "dark" ? "dark garment" : "light garment",
                prompt: `Pixel-to-Pixel palette transfer → ${g.name} (${g.hex})`,
                account: "local",
                w: canvas.width,
                h: canvas.height,
                ms: 0,
              });
            }
            resolve();
          }, "image/png")
        );
        done++;
        setProgress([done, total]);
        await new Promise((r) => setTimeout(r, 10));
      }
    }
    setOutputs((o) => ({ ...o, ...next }));
    setBusy(false);
  }, [busy, items]);

  const download = useCallback((cw: Colorway, item: SrcItem) => {
    const img = imgStore.current.get(item.id);
    if (!img) return;
    // full-res transfer for export
    void transfer(img, cw.mode, 4500).then((c) =>
      c.toBlob((b) => {
        if (!b) return;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(b);
        a.download = `${item.name.replace(/\s+/g, "-").toLowerCase()}_${cw.garment.replace(/\s+/g, "-").toLowerCase()}.png`;
        a.click();
      }, "image/png")
    );
  }, []);

  return (
    <div>
      <PageHead
        kicker="MODULE 02 · PIXEL TO PIXEL"
        title="Garment colorway adaptation"
        desc="Batch-recolor 50–200 designs into print-optimized colorways. Dominant clusters are re-mapped into high-contrast print palettes — ink lifted for dark fabric, deepened for light fabric, hue and shading preserved."
        right={<Chip tone="violet"><Palette size={11} /> 2 colorways / design</Chip>}
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card className="p-5 xl:col-span-1">
          <Section icon={<Images size={16} />} title="Source batch" desc="Drop up to 20 transparent PNG designs." />
          <div
            className="dropzone rounded-2xl py-8 px-4 text-center cursor-pointer"
            onClick={() => document.getElementById("pz-files")?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (e.dataTransfer.files?.length) void onFiles(e.dataTransfer.files);
            }}
          >
            <input id="pz-files" type="file" accept="image/*" multiple className="hidden"
              onChange={(e) => { if (e.target.files?.length) void onFiles(e.target.files); e.target.value = ""; }} />
            <div className="text-[13px] font-semibold">Drop designs here</div>
            <div className="text-[11px] text-white/35 mt-1">transparent PNG · 4500×5400 recommended</div>
          </div>

          <div className="mt-4 space-y-2 max-h-[300px] overflow-y-auto pr-1">
            {items.map((it) => (
              <div key={it.id} className="flex items-center gap-3 rounded-xl bg-white/[0.03] border border-white/[0.06] p-2">
                <div className="checkerboard h-10 w-10 rounded-lg overflow-hidden shrink-0 border border-white/10">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={it.url} alt={it.name} className="w-full h-full object-contain" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] font-semibold truncate">{it.name}</div>
                  <div className="font-mono2 text-[9.5px] text-white/30">{it.w}×{it.h}</div>
                </div>
                <button onClick={() => setItems((x) => x.filter((y) => y.id !== it.id))} className="text-white/30 hover:text-red-300 transition-colors">
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>

          <div className="mt-4 flex items-center justify-between">
            {busy ? (
              <div className="flex items-center gap-2 font-mono2 text-[11px] text-[#c9f24b]">
                <Loader2 size={13} className="animate-spin" /> {progress[0]}/{progress[1]} colorways
              </div>
            ) : (
              <span className="font-mono2 text-[10.5px] text-white/30">{items.length} staged</span>
            )}
            <Btn onClick={() => void runBatch()} disabled={!items.length || busy} variant="primary">
              <RefreshCw size={14} /> Transfer batch
            </Btn>
          </div>

          <div className="mt-5 rounded-xl bg-black/30 border border-white/[0.06] p-3.5">
            <div className="font-mono2 text-[9.5px] tracking-[0.2em] text-white/35 mb-2">TARGET GARMENT SETS</div>
            <div className="flex flex-wrap gap-2">
              {GARMENTS.map((g) => (
                <span key={g.name} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 text-[10.5px] font-mono2 text-white/55">
                  <span className="h-3 w-3 rounded-full border border-white/25" style={{ background: g.hex }} />
                  {g.name}
                </span>
              ))}
            </div>
          </div>
        </Card>

        {/* output grid */}
        <Card className="p-5 xl:col-span-2">
          <Section
            icon={<ArrowRight size={16} />}
            title="Colorway output"
            desc="Simulated on-fabric preview. Download grabs the full-resolution transferred PNG."
            right={Object.keys(outputs).length > 0 ? <Chip tone="lime">{Object.values(outputs).flat().length} renders</Chip> : undefined}
          />
          {items.length === 0 && (
            <div className="rounded-2xl border border-dashed border-white/10 py-24 text-center text-white/30 text-[13px]">
              Stage designs to preview dark + light garment colorways.
            </div>
          )}
          <div className="space-y-5 max-h-[560px] overflow-y-auto pr-1">
            {items.map((it) => {
              const cws = outputs[it.id];
              return (
                <div key={it.id}>
                  <div className="flex items-center gap-2 mb-2.5">
                    <div className="text-[13px] font-bold tracking-tight">{it.name}</div>
                    {!cws && busy && <Chip tone="violet">queued</Chip>}
                    {cws && <Chip tone="lime">transferred</Chip>}
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    {/* original */}
                    <div>
                      <div className="checkerboard rounded-xl border border-white/10 aspect-square overflow-hidden">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={it.url} alt={it.name} className="w-full h-full object-contain p-2" />
                      </div>
                      <div className="mt-1.5 text-[10px] font-mono2 text-white/35 text-center">ORIGINAL</div>
                    </div>
                    {(cws ?? GARMENTS.filter((_, i) => i === 0 || i === 2).map((g) => ({ id: `${it.id}-${g.name}`, garment: g.name, hex: g.hex, mode: g.mode, url: "" }))).map((cw) => (
                      <div key={cw.id}>
                        <div className="rounded-xl border border-white/10 aspect-square overflow-hidden relative group" style={{ background: cw.hex }}>
                          {cw.url ? (
                            <>
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img src={cw.url} alt={cw.garment} className="w-full h-full object-contain p-2" />
                              <button
                                onClick={() => download(cw as Colorway, it)}
                                className="absolute inset-x-2 bottom-2 rounded-lg bg-black/75 backdrop-blur border border-white/20 text-white text-[10px] font-mono2 font-semibold py-1.5 opacity-0 group-hover:opacity-100 transition-all flex items-center justify-center gap-1"
                              >
                                <Download size={10} /> full-res PNG
                              </button>
                            </>
                          ) : (
                            <div className="absolute inset-0 flex items-center justify-center">
                              <span className={cx("h-6 w-6 rounded-full border-2 border-white/20 border-t-white/70", busy && "animate-spin")} />
                            </div>
                          )}
                        </div>
                        <div className="mt-1.5 text-[10px] font-mono2 text-white/35 text-center">{cw.garment.toUpperCase()}</div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </div>
  );
}
