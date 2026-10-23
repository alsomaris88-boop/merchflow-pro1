/** Disk-backed Designs History. An object URL is NOT proof of a saved file.
 * Worker operations archive before emitting success, independently of the UI.
 */
export const DESIGNS_EVENT = "merchflow:designs-changed";
export const HANDOFF_EVENT = "merchflow:send-to-prepare";
const HANDOFF_KEY = "merchflow:pending-prepare";

export interface DesignRecord {
  id: string;
  name: string;
  niche: string;
  style: string;
  source: "generator" | "pixel-to-pixel" | "prepare" | "recolor" | "import";
  aspect: string;
  background: string;
  prompt: string;
  account: string;
  w: number;
  h: number;
  dpi: number;
  bytes: number;
  ms: number;
  path: string;
  saved: true;
  createdAt: number;
  fileUrl: string;
  previewUrl: string;
}
export type DesignMeta = DesignRecord;
export type DesignInput = Pick<DesignRecord, "name" | "source"> & Partial<Pick<DesignRecord, "niche" | "style" | "aspect" | "background" | "prompt" | "account" | "w" | "h" | "ms">>;

export function isSavedDesign(value: unknown): value is DesignRecord {
  if (!value || typeof value !== "object") return false;
  const d = value as Partial<DesignRecord>;
  return d.saved === true && typeof d.id === "string" && !!d.path && typeof d.fileUrl === "string"
    && Number(d.w) > 0 && Number(d.h) > 0 && Number(d.bytes) > 8 && Number(d.dpi) === 300;
}

export function announceDesignsChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(DESIGNS_EVENT));
}

export async function responseJson<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => null);
  if (!res.ok || data?.ok === false) throw new Error(String(data?.reason ?? data?.detail ?? data?.error ?? `Request failed (${res.status})`));
  return data as T;
}

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Image file could not be read"));
    reader.readAsDataURL(blob);
  });
}

/** Save browser-derived PNGs (distress, product variants) before marking done. */
export async function saveDesignFromUrl(url: string, meta: DesignInput): Promise<DesignRecord> {
  const response = await fetch(url);
  if (!response.ok) throw new Error("Could not read the generated image");
  return saveDesignBlob(await response.blob(), meta);
}

export async function saveDesignBlob(blob: Blob, meta: DesignInput): Promise<DesignRecord> {
  const { name, ...rest } = meta;
  const res = await fetch("/api/bridge/designs", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image: await blobToDataUrl(blob), name, meta: rest }),
  });
  const data = await responseJson<{ design: DesignRecord }>(res);
  if (!isSavedDesign(data.design)) throw new Error("Engine did not confirm a genuine PNG on disk");
  announceDesignsChanged();
  return data.design;
}

export async function addDesign(input: DesignInput & { blob: Blob }): Promise<string> {
  const { blob, ...meta } = input;
  return (await saveDesignBlob(blob, meta)).id;
}

export async function addDesignFromDataUrl(url: string, meta: DesignInput): Promise<string> {
  return (await saveDesignFromUrl(url, meta)).id;
}

export async function listDesigns(): Promise<DesignRecord[]> {
  const data = await responseJson<{ items: DesignRecord[] }>(await fetch("/api/bridge/designs", { cache: "no-store" }));
  if (!Array.isArray(data.items)) throw new Error("Invalid Designs History response");
  return data.items.filter(isSavedDesign);
}

export async function getDesigns(ids: string[]): Promise<DesignRecord[]> {
  const records = new Map((await listDesigns()).map((d) => [d.id, d]));
  return ids.flatMap((id) => records.has(id) ? [records.get(id)!] : []);
}

export async function countDesigns(): Promise<number> {
  return (await listDesigns()).length;
}

export async function deleteDesigns(ids: string[]): Promise<void> {
  await responseJson(await fetch("/api/bridge/designs", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) }));
  announceDesignsChanged();
}

export async function clearDesigns(): Promise<void> {
  await responseJson(await fetch("/api/bridge/designs", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ all: true }) }));
  announceDesignsChanged();
}

/** Let the browser download manager consume the streaming ZIP response.
 * No JSZip, archive-sized Blob, base64 copy, or object URL is needed.
 */
export async function downloadDesignsZip(ids: string[]): Promise<void> {
  const data = await responseJson<{ url: string }>(await fetch("/api/bridge/export", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }),
  }));
  if (!data.url?.startsWith("/api/bridge/export?ticket=")) throw new Error("Invalid ZIP download URL");
  const anchor = document.createElement("a");
  anchor.href = data.url;
  anchor.download = `merchflow-designs_${ids.length}.zip`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

/** Persist across navigation: the Prepare component is not mounted yet when
 * History dispatches the event. A transient DOM event alone loses the batch.
 */
export function sendToPrepare(ids: string[]): void {
  if (typeof window === "undefined" || !ids.length) return;
  sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(ids));
  window.dispatchEvent(new CustomEvent<string[]>(HANDOFF_EVENT, { detail: ids }));
}

export function takePrepareHandoff(): string[] {
  const raw = sessionStorage.getItem(HANDOFF_KEY);
  sessionStorage.removeItem(HANDOFF_KEY);
  try {
    const ids = JSON.parse(raw ?? "[]") as unknown;
    return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === "string") : [];
  } catch { return []; }
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function timeAgo(ts: number): string {
  const seconds = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}
