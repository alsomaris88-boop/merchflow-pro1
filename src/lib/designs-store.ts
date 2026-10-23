/**
 * MerchFlow Pro — Designs History store (IndexedDB).
 *
 * Every design produced by the AI Design Generator or Pixel to Pixel is
 * auto-archived here with full metadata, then handed off to the Prepare
 * Designs Lab in one click. Blobs are stored (not data URLs) so a 200-design
 * batch stays cheap; object URLs are minted on read and revoked on clear.
 */

export const DESIGNS_DB = "merchflow";
export const DESIGNS_STORE = "designs";
export const DESIGNS_EVENT = "merchflow:designs-changed";
export const HANDOFF_EVENT = "merchflow:send-to-prepare";

export interface DesignRecord {
  id: string;
  name: string;
  niche: string;
  style: string;
  source: "generator" | "recolor" | "import";
  aspect: string;
  background: string;
  prompt: string;
  account: string;
  w: number;
  h: number;
  bytes: number;
  ms: number;
  path?: string;
  createdAt: number;
  blob: Blob;
}

export type DesignMeta = Omit<DesignRecord, "blob">;

let dbp: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") return Promise.reject(new Error("no indexedDB"));
  if (dbp) return dbp;
  dbp = new Promise((resolve, reject) => {
    const req = indexedDB.open(DESIGNS_DB, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(DESIGNS_STORE)) {
        const os = db.createObjectStore(DESIGNS_STORE, { keyPath: "id" });
        os.createIndex("createdAt", "createdAt");
        os.createIndex("niche", "niche");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbp;
}

function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return open().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(DESIGNS_STORE, mode);
        const req = run(t.objectStore(DESIGNS_STORE));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      })
  );
}

function announce() {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(DESIGNS_EVENT));
}

/* --------------------------------- writes -------------------------------- */

export async function addDesign(
  input: Omit<DesignRecord, "id" | "createdAt" | "bytes" | "blob"> & { blob: Blob; id?: string }
): Promise<string> {
  const id = input.id ?? `d_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const rec: DesignRecord = {
    ...input,
    id,
    bytes: input.blob.size,
    createdAt: Date.now(),
  };
  await tx("readwrite", (s) => s.put(rec));
  announce();
  return id;
}

/** Convenience: archive from a data URL (canvas/base64 pipelines). */
export async function addDesignFromDataUrl(
  dataUrl: string,
  meta: Omit<DesignRecord, "id" | "createdAt" | "bytes" | "blob">
): Promise<string | null> {
  try {
    const blob = await (await fetch(dataUrl)).blob();
    return await addDesign({ ...meta, blob });
  } catch {
    return null;
  }
}

export async function deleteDesigns(ids: string[]): Promise<void> {
  const db = await open();
  await new Promise<void>((resolve, reject) => {
    const t = db.transaction(DESIGNS_STORE, "readwrite");
    const os = t.objectStore(DESIGNS_STORE);
    for (const id of ids) os.delete(id);
    t.oncomplete = () => resolve();
    t.onerror = () => reject(t.error);
  });
  announce();
}

export async function clearDesigns(): Promise<void> {
  await tx("readwrite", (s) => s.clear());
  announce();
}

/* --------------------------------- reads --------------------------------- */

export async function listDesigns(): Promise<DesignRecord[]> {
  try {
    const all = await tx<DesignRecord[]>("readonly", (s) => s.getAll() as IDBRequest<DesignRecord[]>);
    return (all ?? []).sort((a, b) => b.createdAt - a.createdAt);
  } catch {
    return [];
  }
}

export async function getDesigns(ids: string[]): Promise<DesignRecord[]> {
  const all = await listDesigns();
  const want = new Set(ids);
  return all.filter((d) => want.has(d.id));
}

export async function countDesigns(): Promise<number> {
  try {
    return await tx<number>("readonly", (s) => s.count());
  } catch {
    return 0;
  }
}

/* ------------------------------ cross-module ------------------------------ */

/** Hand selected designs to the Prepare Lab (consumed by prepare-lab.tsx). */
export function sendToPrepare(ids: string[]): void {
  if (typeof window === "undefined" || !ids.length) return;
  window.dispatchEvent(new CustomEvent<string[]>(HANDOFF_EVENT, { detail: ids }));
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function timeAgo(ts: number): string {
  const s = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
