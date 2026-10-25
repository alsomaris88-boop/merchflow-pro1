import { isSavedDesign, responseJson, type DesignRecord } from "./designs-store";

export const P2P_EVENT = "merchflow:p2p-updated";
export const P2P_BATCH_KEY = "merchflow:current-p2p-batch";
export const COLOUR_SWATCHES = [
  { label: "Pitch Black", hex: "#000000" },
  { label: "Pure White", hex: "#FFFFFF" },
] as const;
export interface PixelInput { id: string; file: File; url: string }
export interface PixelJob {
  id: string; name: string; state: string; account: string; reason: string;
  design: DesignRecord | null; retryable?: boolean; local?: boolean;
}
export interface PixelBatch {
  id: string; status: string; background: string; total: number;
  completed: number; failed: number; items: PixelJob[];
}
export const isBatchActive = (batch: PixelBatch | null) => !!batch && ["uploading", "running", "retry-uploading"].includes(batch.status);
export const generationLabel = (n: number) => `${n} designs in, ${n} back on your colour. ▷ Generate`;
export function normaliseHex(value: string): string {
  if (!/^#[0-9a-f]{6}$/i.test(value)) throw new Error("Enter a six-digit hex colour, such as #000000.");
  return value.toUpperCase();
}
const root = "/api/bridge/p2p/batches";
export async function getPixelBatch(id: string): Promise<PixelBatch> {
  return (await responseJson<{ batch: PixelBatch }>(await fetch(`${root}/${encodeURIComponent(id)}`, { cache: "no-store" }))).batch;
}

/** Each original File uploads independently as binary, never one giant base64
 * payload. Only two uploads are resident in the proxy at a time; N is not capped.
 * Generation starts server-side and survives navigation/disconnected SSE.
 */
export async function submitPixelBatch(
  inputs: PixelInput[], colour: string,
  onBatch: (batch: PixelBatch) => void,
  onUpload: (finished: number, total: number) => void,
): Promise<PixelBatch> {
  if (!inputs.length) throw new Error("Upload at least one design.");
  const background = normaliseHex(colour);
  if (!COLOUR_SWATCHES.some((option) => option.hex === background)) throw new Error("Choose Pitch Black or Pure White.");
  // A retained selection may be generated again on another colour. Selection
  // IDs are not job IDs: each run needs fresh global SQLite job identities.
  const jobs = inputs.map(({ file }) => ({ id: crypto.randomUUID(), file }));
  const data = await responseJson<{ batch: PixelBatch }>(await fetch(root, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ background, outputsPerInput: 1, items: jobs.map(({ id, file }) => ({ id, name: file.name })) }),
  }));
  const batch = data.batch;
  localStorage.setItem(P2P_BATCH_KEY, batch.id);
  onBatch(batch);
  let cursor = 0, uploaded = 0;
  const upload = async () => {
    while (cursor < jobs.length) {
      const item = jobs[cursor++];
      try {
        await responseJson(await fetch(`${root}/${encodeURIComponent(batch.id)}/items/${encodeURIComponent(item.id)}`, {
          method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: item.file,
        }));
      } catch {
        // The server records invalid uploads, and start marks incomplete ones
        // failed. Other designs must still generate and archive normally.
      } finally { onUpload(++uploaded, inputs.length); }
    }
  };
  await Promise.all(Array.from({ length: Math.min(2, inputs.length) }, upload));
  const started = await responseJson<{ batch: PixelBatch }>(await fetch(`${root}/${encodeURIComponent(batch.id)}/start`, { method: "POST" }));
  onBatch(started.batch);
  return started.batch;
}

export async function discardPixelUpload(id: string): Promise<PixelBatch> {
  return (await responseJson<{ batch: PixelBatch }>(await fetch(`${root}/${encodeURIComponent(id)}/discard`, { method: "POST" }))).batch;
}

/** Explicit retry of ONE existing failed job; never creates a new saved identity.
 * A replacement binary is only needed for an old/missing spooled reference.
 */
export async function retryPixelJob(batchId: string, jobId: string, file?: File): Promise<PixelBatch> {
  const result = await responseJson<{ batch: PixelBatch }>(await fetch(
    `${root}/${encodeURIComponent(batchId)}/items/${encodeURIComponent(jobId)}/retry`, {
      method: "POST", ...(file ? { headers: { "Content-Type": "application/octet-stream" }, body: file } : {}),
    }));
  return result.batch;
}

/** A provider/transport/partial failure must never be labelled Batch complete. */
export function pixelBatchLabel(batch: PixelBatch): string {
  const failures = batch.items.filter((job) => job.state === "failed").length;
  if (failures || batch.failed || ["failed", "partial"].includes(batch.status)) {
    return isBatchActive(batch) ? "Generating · some designs failed" : "Generation failed · retry failed designs";
  }
  const saved = batch.items.filter((job) => job.state === "succeeded" && isSavedDesign(job.design)).length;
  if (batch.status === "completed" && saved === batch.total && saved > 0) return "Batch complete · all designs archived";
  if (batch.status === "completed") return "Saved artwork unavailable · check Designs History";
  return batch.status === "uploading" ? "Uploading references" : "Generating 8K artwork...";
}
