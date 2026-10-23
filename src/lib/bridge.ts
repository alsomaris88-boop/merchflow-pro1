/* Same-origin client helpers: Next proxies to the local desktop worker. */
export interface WorkerProfile { id: number; label: string; port?: number; status?: string; loggedIn?: boolean }
export interface BridgeStatus {
  connected: boolean;
  version?: string;
  engine?: string;
  mock?: boolean;
  outDir?: string;
  profiles?: WorkerProfile[];
  capabilities?: Record<string, boolean | string>;
}

export async function probeBridge(): Promise<BridgeStatus> {
  try {
    const res = await fetch("/api/bridge", { cache: "no-store" });
    if (!res.ok) return { connected: false };
    const data = await res.json() as BridgeStatus;
    return { ...data, connected: !!data.connected };
  } catch { return { connected: false }; }
}

export async function dataUrlFromUrl(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Cannot load image (${res.status})`);
  const blob = await res.blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Image read failed"));
    reader.readAsDataURL(blob);
  });
}

export type SSEHandler = (event: Record<string, unknown>) => void | Promise<void>;

/** Handles split UTF-8, CRLF/LF frames, comments and a final unterminated frame.
 * Handler errors propagate: a failed disk confirmation must not be swallowed.
 */
export async function readSSE(response: Response, onEvent: SSEHandler): Promise<void> {
  if (!response.ok || !response.body) throw new Error(`No event stream (${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const emit = async (block: string) => {
    const payload = block.split(/\r?\n/).filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart()).join("\n");
    if (payload) await onEvent(JSON.parse(payload) as Record<string, unknown>);
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) await emit(block);
      if (done) { if (buffer.trim()) await emit(buffer); break; }
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally { reader.releaseLock(); }
}
