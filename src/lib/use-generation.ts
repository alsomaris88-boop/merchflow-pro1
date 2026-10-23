"use client";

import { useCallback, useRef, useState } from "react";
import { dataUrlFromUrl, probeBridge, readSSE } from "@/lib/bridge";
import { announceDesignsChanged, isSavedDesign, responseJson, type DesignRecord } from "@/lib/designs-store";

export interface ReferenceItem { id: string; name: string; url: string }
export interface GenerationJob extends ReferenceItem { status: "queued" | "running" | "done" | "failed"; message?: string; account?: string }
export interface GenerationResult { design: DesignRecord; nativeW?: number; nativeH?: number; upscaled: boolean; ms: number }
export interface GenerationOptions {
  endpoint: "/api/bridge/generate" | "/api/bridge/pixel-to-pixel";
  background: string;
  model: string;
  styleUrl?: string;
  styleName?: string;
  aspect?: string;
  textOverride?: string;
  instructions?: string;
}

/** One reference request at a time, verified success events only, no demo path. */
export function useGeneration() {
  const [running, setRunning] = useState(false);
  const [jobs, setJobs] = useState<GenerationJob[]>([]);
  const [results, setResults] = useState<GenerationResult[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const busy = useRef(false);
  const log = useCallback((message: string) => setLogs((previous) => [...previous.slice(-79), `${new Date().toLocaleTimeString()} · ${message}`]), []);

  const run = useCallback(async (references: ReferenceItem[], options: GenerationOptions) => {
    if (busy.current || !references.length) return;
    busy.current = true;
    setRunning(true); setError(""); setResults([]); setLogs([]);
    setJobs(references.map((item) => ({ ...item, status: "queued" })));
    try {
      const bridge = await probeBridge();
      if (!bridge.connected) throw new Error("Local engine offline. Start run_windows.bat; simulated generation is not available.");
      if (bridge.mock) throw new Error("Disable MERCHFLOW_MOCK_ENGINE and connect a real image-provider account.");
      const styleImage = options.styleUrl ? await dataUrlFromUrl(options.styleUrl) : "";
      for (const reference of references) {
        setJobs((previous) => previous.map((job) => job.id === reference.id ? { ...job, status: "running", message: "Uploading reference" } : job));
        let imageReceived = false;
        let terminal = false;
        let failure = "";
        try {
          const response = await fetch(options.endpoint, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              styleImage, styleName: options.styleName, aspect: options.aspect ?? "4:5",
              background: options.background, model: options.model, textOverride: options.textOverride,
              instructions: options.instructions,
              nicheImages: [{ id: reference.id, name: reference.name, image: await dataUrlFromUrl(reference.url) }],
            }),
          });
          if (!response.ok) await responseJson(response); // propagate engine/auth errors, never substitute a preview
          await readSSE(response, (event) => {
            const type = String(event.type ?? "");
            if (type === "dispatch") {
              setJobs((previous) => previous.map((job) => job.id === reference.id ? { ...job, account: String(event.account), message: "Image provider is rendering" } : job));
              log(`${reference.name}: dispatched to ${String(event.account)}`);
            } else if (type === "progress") {
              setJobs((previous) => previous.map((job) => job.id === reference.id ? { ...job, message: String(event.msg ?? "Processing") } : job));
              if (event.msg) log(String(event.msg));
            } else if (type === "image") {
              if (!isSavedDesign(event.design)) throw new Error("Provider result was not confirmed as a real saved 300 DPI PNG");
              imageReceived = true;
              const design = event.design;
              setResults((previous) => [...previous, { design, nativeW: Number(event.nativeW) || undefined, nativeH: Number(event.nativeH) || undefined, upscaled: event.upscaled === true, ms: Number(event.ms) || design.ms }]);
              setJobs((previous) => previous.map((job) => job.id === reference.id ? { ...job, status: "done", message: design.path } : job));
              announceDesignsChanged();
              log(`${reference.name}: saved ${design.w}×${design.h} PNG → ${design.path}`);
            } else if (type === "failed") {
              failure = String(event.reason ?? "Image generation failed");
            } else if (type === "fatal") {
              throw new Error(String(event.reason ?? "Engine failure"));
            } else if (type === "done") {
              terminal = true;
            } else if (type === "log" && event.msg) log(String(event.msg));
          });
          if (!terminal) throw new Error("Generation stream ended early. Check Designs History before retrying.");
          if (!imageReceived) throw new Error(failure || "Engine returned no verified image");
        } catch (e) {
          const message = e instanceof Error ? e.message : "Generation failed";
          setJobs((previous) => previous.map((job) => job.id === reference.id ? { ...job, status: "failed", message } : job));
          setError(message);
          log(`${reference.name}: FAILED · ${message}`);
        }
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : "Batch failed";
      setError(message); log(message);
      setJobs((previous) => previous.map((job) => job.status === "queued" || job.status === "running" ? { ...job, status: "failed", message } : job));
    } finally { busy.current = false; setRunning(false); }
  }, [log]);

  return { running, jobs, results, logs, error, run };
}
