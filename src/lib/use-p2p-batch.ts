"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  discardPixelUpload, getPixelBatch, isBatchActive, P2P_BATCH_KEY, P2P_EVENT,
  submitPixelBatch, type PixelBatch, type PixelInput,
} from "./p2p-client";

export function usePixelBatch() {
  const [batch, setBatch] = useState<PixelBatch | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState("");
  const [error, setError] = useState("");
  const current = useRef<string | null>(null);
  const inFlight = useRef(false);
  const dirty = useRef(false);

  const refresh = useCallback(async () => {
    if (!current.current) return;
    if (inFlight.current) { dirty.current = true; return; }
    inFlight.current = true;
    try {
      do {
        dirty.current = false;
        const id: string | null = current.current;
        if (!id) break;
        const latest = await getPixelBatch(id);
        if (id === current.current) setBatch(latest);
      } while (dirty.current);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not read batch state"); }
    finally { inFlight.current = false; }
  }, []);

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
    setError(""); setUploading(true); setUploadProgress(`Uploading 0 / ${inputs.length}`);
    try {
      const started = await submitPixelBatch(inputs, colour, (value) => { current.current = value.id; setBatch(value); },
        (done, total) => setUploadProgress(`Uploading ${done} / ${total}`));
      return started;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the batch. Check its saved state before retrying.");
      void refresh();
    } finally { setUploading(false); setUploadProgress(""); }
  };
  const discard = async () => {
    if (!batch) return;
    try { setBatch(await discardPixelUpload(batch.id)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not discard unfinished upload"); }
  };
  return { batch, uploading, uploadProgress, error, run, discard, running: uploading || isBatchActive(batch) };
}
