"use client";

import { Download, FolderCheck, Loader2 } from "lucide-react";
import { Btn, Card, Chip, Section } from "@/components/ui";
import { formatBytes, type DesignRecord } from "@/lib/designs-store";
import { type GenerationJob, type GenerationResult } from "@/lib/use-generation";

export function downloadSavedPng(design: DesignRecord): void {
  const a = document.createElement("a");
  a.href = design.fileUrl;
  a.download = `${design.name.replace(/[^\w-]+/g, "-")}_${design.w}x${design.h}_300dpi.png`;
  document.body.appendChild(a); a.click(); a.remove();
}

export function ProductionQueue({ jobs, logs }: { jobs: GenerationJob[]; logs: string[] }) {
  return <div className="space-y-4">
    <Card className="p-5">
      <Section icon={<Loader2 size={15} />} title="Production queue" desc="Only provider images with a verified disk save become finished designs." />
      {!jobs.length && <p className="py-8 text-center text-xs text-white/30">Stage your references, then generate.</p>}
      <div className="space-y-2 max-h-[360px] overflow-y-auto">{jobs.map((job) => <div key={job.id} className="border border-white/10 rounded-xl p-3">
        <div className="flex justify-between items-center gap-3"><span className="text-xs font-semibold truncate">{job.name}</span>
          <Chip tone={job.status === "done" ? "lime" : job.status === "failed" ? "amber" : "neutral"}>{job.status === "running" && <Loader2 size={10} className="animate-spin" />}{job.status}</Chip></div>
        {job.message && <div className={`mt-2 text-[10px] break-words ${job.status === "failed" ? "text-red-300" : "text-white/40"}`}>{job.message}</div>}
      </div>)}</div>
    </Card>
    <Card className="p-5"><div className="font-mono2 text-[10px] tracking-widest text-white/40 mb-3">ENGINE CONSOLE</div>
      <div className="rounded-xl bg-black/30 p-3 h-[220px] overflow-y-auto font-mono2 text-[10px] leading-relaxed text-[#c9f24b]/80">
        {logs.length ? logs.map((message, index) => <div key={index}>{message}</div>) : <span className="text-white/25">Awaiting a real provider job.</span>}
      </div>
    </Card>
  </div>;
}

export function GenerationOutputs({ results }: { results: GenerationResult[] }) {
  if (!results.length) return null;
  return <Card className="p-5 mt-4" glow>
    <Section icon={<FolderCheck size={15} />} title={`Saved designs · ${results.length}`} desc="Download returns the exact PNG on disk. All outputs are already in Designs History." />
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">{results.map((result) => {
      const d = result.design;
      return <div key={d.id}>
        <div className="checkerboard rounded-xl border border-white/10 aspect-[5/6] overflow-hidden">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={d.previewUrl} alt={d.name} loading="lazy" className="w-full h-full object-contain" />
        </div>
        <div className="text-xs font-semibold mt-2 truncate">{d.name}</div>
        <div className="text-[10px] text-white/40 mt-1">{d.w}×{d.h} · 300 DPI · {formatBytes(d.bytes)}</div>
        <div className="text-[10px] text-white/40">{(result.ms / 1000).toFixed(1)}s · {d.account}</div>
        {result.nativeW && result.nativeH && <div className="text-[10px] text-white/40 mt-1">Provider: {result.nativeW}×{result.nativeH}{result.upscaled ? " · Lanczos upscaled to 8K" : " · resized to 8K"}</div>}
        <div className="text-[9px] text-[#c9f24b]/70 break-all mt-2" title={d.path}><FolderCheck size={10} className="inline mr-1" />{d.path}</div>
        <Btn variant="ghost" className="mt-3 text-xs px-3 py-2" onClick={() => downloadSavedPng(d)}><Download size={12} /> PNG</Btn>
      </div>;
    })}</div>
  </Card>;
}
