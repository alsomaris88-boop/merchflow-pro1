"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  discardPixelUpload, getPixelBatch, isBatchActive, P2P_BATCH_KEY, P2P_EVENT,
  retryPixelJob, submitPixelBatch, type PixelBatch, type PixelInput, type PixelJob,
} from "./p2p-client";

const message = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;

export function usePixelBatch() {
  const [batch, setBatch] = useState<PixelBatch | null>(null);
  const [drafts, setDrafts] = useState<PixelJob[]>([]);
  const [uploading, setUploading] = useState(false);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [retryErrors, setRetryErrors] = useState<Record<string, string>>({});
  const [uploadProgress, setUploadProgress] = useState("");
  const [error, setError] = useState("");
  const current = useRef<string | null>(null);
  const latestBatch = useRef<PixelBatch | null>(null);
  const originals = useRef(new Map<string, PixelInput>());
  const originalColour = useRef("#000000");
  const operation = useRef(false);
  const epoch = useRef(0);
  const inFlight = useRef(false);
  const dirty = useRef(false);

  const accept = useCallback((value: PixelBatch) => {
    epoch.current++;
    current.current = value.id;
    latestBatch.current = value;
    setBatch(value);
  }, []);

  const refresh = useCallback(async () => {
    if (!current.current) return;
    if (inFlight.current) { dirty.current = true; return; }
    inFlight.current = true;
    try {
      do {
        dirty.current = false;
        const id: string | null = current.current, version = epoch.current;
        if (!id) break;
        const value = await getPixelBatch(id);
        // A GET started before Generate/Retry must not overwrite that action.
        if (id === current.current && version === epoch.current) accept(value);
      } while (dirty.current);
    } catch (e) { setError(message(e, "Could not read batch state")); }
    finally { inFlight.current = false; }
  }, [accept]);

  useEffect(() => {
    current.current = localStorage.getItem(P2P_BATCH_KEY);
    const initial = setTimeout(() => void refresh(), 0);
    const onUpdate = (event: Event) => {
      const id = (event as CustomEvent<string | undefined>).detail;
      if (!id || id === current.current) void refresh();
    };
    window.addEventListener(P2P_EVENT, onUpdate);
    const timer = setInterval(() => void refresh(), 3000);
    return () => { clearTimeout(initial); clearInterval(timer); window.removeEventListener(P2P_EVENT, onUpdate); };
  }, [refresh]);

  const run = async (inputs: PixelInput[], colour: string) => {
    if (operation.current || isBatchActive(latestBatch.current) || !inputs.length) return;
    operation.current = true;
    epoch.current++; current.current = null; latestBatch.current = null;
    originals.current = new Map(inputs.map((input) => [input.id, input]));
    originalColour.current = colour;
    setBatch(null); setError(""); setRetryErrors({}); setUploading(true);
    // Pending UI only: no design, image, archive claim or invented server batch.
    // This runs before the very first POST, not after its response.
    setDrafts(inputs.map((input) => ({ id: input.id, name: input.file.name, state: "requesting",
      account: "", reason: "", design: null, local: true, retryable: true })));
    setUploadProgress(`Uploading 0 / ${inputs.length}`);
    try {
      return await submitPixelBatch(inputs, colour, (value) => { accept(value); setDrafts([]); },
        (done, total) => setUploadProgress(`Uploading ${done} / ${total}`));
    } catch (e) {
      const reason = message(e, "Could not start the batch. Check its saved state before retrying.");
      setError(reason);
      setDrafts((items) => items.map((job) => ({ ...job, state: "failed", reason })));
      void refresh();
    } finally { operation.current = false; setUploading(false); setUploadProgress(""); }
  };

  const retry = async (jobId: string, file?: File) => {
    if (operation.current || isBatchActive(latestBatch.current)) return;
    const local = drafts.find((job) => job.id === jobId && job.state === "failed");
    if (local) {
      const original = originals.current.get(jobId);
      if (original) return run([original], originalColour.current);
      return;
    }
    const savedBatch = latestBatch.current;
    const job = savedBatch?.items.find((item) => item.id === jobId);
    if (!savedBatch || job?.state !== "failed") return;
    operation.current = true; epoch.current++;
    setRetrying(jobId); setError("");
    setRetryErrors((old) => ({ ...old, [jobId]: "" }));
    try {
      const value = await retryPixelJob(savedBatch.id, jobId, file);
      accept(value);
    } catch (e) {
      const reason = message(e, "Could not retry the failed design");
      setRetryErrors((old) => ({ ...old, [jobId]: reason }));
      setError(reason);
    } finally {
      operation.current = false; setRetrying(null); void refresh();
    }
  };

  const discard = async () => {
    if (!batch || operation.current) return;
    operation.current = true;
    try { accept(await discardPixelUpload(batch.id)); setError(""); }
    catch (e) { setError(message(e, "Could not discard unfinished upload")); }
    finally { operation.current = false; }
  };
  const clear = () => {
    if (isBatchActive(latestBatch.current)) return;
    current.current = null;
    latestBatch.current = null;
    setBatch(null);
    setDrafts([]);
    setError("");
    setRetryErrors({});
    try {
      localStorage.removeItem(P2P_BATCH_KEY);
    } catch {}
  };
  const jobs = (drafts.length ? drafts : batch?.items ?? []).map((job) =>
    job.id === retrying ? { ...job, state: "queued", reason: "" } : job);
  return { batch, jobs, uploading, uploadProgress, error, retryErrors, run, retry, discard, clear,
    running: uploading || !!retrying || isBatchActive(batch) };
}
