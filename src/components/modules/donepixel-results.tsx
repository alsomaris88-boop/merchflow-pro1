"use client";

import { useRef } from "react";
import { AlertTriangle, Check, Download, FlaskConical, Loader2, RotateCcw, Sparkles } from "lucide-react";
import { Btn, Chip, cx } from "@/components/ui";
import { formatBytes, isSavedDesign, sendToPrepare } from "@/lib/designs-store";
import type { PixelJob } from "@/lib/p2p-client";
import type { ViewKey } from "@/components/shell";

export function DonepixelResults({ jobs, busy, canRetry, retryErrors, onRetry, onClear, setView }: {
  jobs: PixelJob[]; busy: boolean; canRetry: boolean; retryErrors: Record<string, string>;
  onRetry: (id: string, file?: File) => void; onClear?: () => void; setView: (view: ViewKey) => void;
}) {
  const replacement = useRef<HTMLInputElement>(null);
  const retryTarget = useRef<string | null>(null);
  if (!jobs.length) return null;
  return <section aria-labelledby="p2p-results-title" data-testid="p2p-results" className="mt-6">
    <div className="flex items-center justify-between gap-3 mb-4">
      <div><h2 id="p2p-results-title" className="font-display font-bold text-xl flex items-center gap-2"><Sparkles size={19} className="text-[#c9f24b]" />Live results</h2>
        <p className="mt-1 text-xs text-white/45">Real artwork appears only after the PNG and Designs History commit. Failed designs keep their exact engine error.</p></div>
      <div className="flex items-center gap-2">
        <Chip tone="violet">{jobs.length} {jobs.length === 1 ? "design" : "designs"}</Chip>
        {onClear && !busy && (
          <Btn variant="ghost" className="text-xs px-2.5 py-1 text-white/60 hover:text-white" onClick={onClear}>
            Clear results
          </Btn>
        )}
      </div>
    </div>
    <input ref={replacement} id="p2p-retry-file" aria-label="Original image for failed design retry" type="file" accept="image/*" className="hidden"
      onChange={(event) => {
        const file = event.target.files?.[0], id = retryTarget.current;
        event.target.value = ""; retryTarget.current = null;
        if (file && id) onRetry(id, file);
      }} />
    <div className="grid sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-4">
      {jobs.map((job) => {
        const failed = job.state === "failed";
        const design = job.state === "succeeded" && isSavedDesign(job.design) ? job.design : null;
        const pending = !failed && job.state !== "succeeded";
        return <article key={job.id} data-testid="p2p-result-card" data-job-id={job.id} data-state={job.state}
          aria-label={`Result for ${job.name}`} aria-busy={pending}
          className={cx("rounded-2xl overflow-hidden border bg-white/[0.025]", failed ? "border-red-400/65 shadow-[0_0_22px_-12px_rgba(248,113,113,0.5)]" : "border-white/10")}>
          <div className="relative aspect-[5/6] bg-black/30 overflow-hidden flex items-center justify-center">
            {design ? <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={design.previewUrl} alt={`Generated artwork: ${job.name}`} data-testid="p2p-generated-artwork" loading="lazy" className="h-full w-full object-contain" />
              <span className="absolute top-3 left-3 rounded-full border border-[#c9f24b]/30 bg-black/85 px-2.5 py-1 text-[10px] text-[#c9f24b] flex items-center gap-1"><Check size={11} />Saved to History</span>
            </> : pending ? <div className="w-full h-full p-6 flex flex-col items-center justify-center gap-5" data-testid="p2p-generating">
              <div aria-hidden="true" className="absolute inset-0 animate-pulse bg-gradient-to-br from-[#7c5aff]/15 via-white/[0.035] to-[#c9f24b]/10" />
              <div aria-hidden="true" className="relative w-3/4 space-y-4 animate-pulse"><div className="h-28 rounded-2xl bg-white/[0.07]" /><div className="h-3 rounded-full bg-white/[0.08]" /><div className="h-3 w-2/3 mx-auto rounded-full bg-white/[0.05]" /></div>
              <div role="status" className="relative flex items-center gap-2 text-sm font-semibold text-[#d1c6ff]"><Loader2 size={17} className="animate-spin" />Generating native-resolution artwork...</div>
              <p className="relative text-[11px] text-white/45">{["requesting", "awaiting-upload", "uploading", "retry-uploading", "uploaded"].includes(job.state) ? "Preparing your reference upload" : job.state === "saving" ? "Verifying PNG + archiving to History" : "Waiting for the Gemini engine"}</p>
            </div> : failed ? <div className="p-5 w-full space-y-4 max-h-full overflow-y-auto">
              <AlertTriangle size={32} className="text-red-300" />
              <div role="alert" className="rounded-xl border border-red-400/55 bg-red-500/15 p-4 text-red-100">
                <span className="block text-[11px] uppercase tracking-wide font-bold text-red-300 mb-2">Error · generation failed</span>
                <p data-testid="p2p-failure-reason" className="text-sm whitespace-pre-wrap break-words max-h-44 overflow-y-auto">{job.reason || "Engine did not provide a failure reason. Nothing was saved."}</p>
              </div>
              {retryErrors[job.id] && <p className="text-xs text-red-200 whitespace-pre-wrap break-words max-h-24 overflow-y-auto">Retry could not start: {retryErrors[job.id]}</p>}
              {!job.local && !job.retryable && <p className="text-[11px] text-amber-200/90">Original upload unavailable. Retry will ask you to choose the original image again.</p>}
              <Btn variant="danger" className="w-full justify-center" aria-label={`Retry ${job.name}`} disabled={busy || !canRetry}
                title={busy ? "Wait for active generation to finish before retrying" : "Retry only this failed design"}
                onClick={() => {
                  if (job.local || job.retryable) onRetry(job.id);
                  else { retryTarget.current = job.id; replacement.current?.click(); }
                }}><RotateCcw size={14} />Retry</Btn>
              <p className="text-[11px] text-white/40">Nothing was archived for this failed input.</p>
            </div> : <div className="p-6 text-center"><AlertTriangle size={36} className="mx-auto text-red-300 mb-3" /><p className="text-sm font-semibold text-red-200">Saved artwork unavailable</p><p className="mt-2 text-xs text-white/45">No generated image is being substituted.</p></div>}
          </div>
          <div className="p-4 space-y-3">
            <h3 className="font-semibold text-sm truncate" title={job.name}>{job.name}</h3>
            {design ? <>
              <p className="text-[11px] text-white/45">Native {design.nativeW ?? design.w} × {design.nativeH ?? design.h} · {formatBytes(design.bytes)} · lossless RGBA</p>
              <p className="text-[10px] text-white/40">Prepare Lab expands the saved native image to the selected print canvas.</p>
              <div className="flex flex-col gap-2">
                <a href={design.fileUrl} download={`${design.name}.png`} aria-label={`Download ${job.name}`} className="inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-[13px] font-semibold border border-white/15 bg-white/[0.05] hover:bg-white/10"><Download size={14} />Download</a>
                <Btn variant="primary" className="justify-center" aria-label={`Send ${job.name} to Prepare Designs Lab`} onClick={() => { sendToPrepare([design.id]); setView("prepare"); }}><FlaskConical size={14} />Send to Prepare Lab</Btn>
              </div>
            </> : !pending && !failed ? <p role="alert" className="text-xs text-red-200">This archived design is no longer available in History. Successful jobs are never regenerated automatically.</p> : null}
          </div>
        </article>;
      })}
    </div>
  </section>;
}
