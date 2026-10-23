/* Client-side helpers for the local desktop engine bridge (Next API → 127.0.0.1:8000) */

export interface WorkerProfile {
  id: number;
  label: string;
  port?: number;
  status?: string;
  loggedIn?: boolean;
}

export interface BridgeStatus {
  connected: boolean;
  version?: string;
  engine?: string;
  profiles?: WorkerProfile[];
}

export async function probeBridge(): Promise<BridgeStatus> {
  try {
    const r = await fetch("/api/bridge", { cache: "no-store" });
    const d = (await r.json()) as BridgeStatus;
    return { ...d, connected: !!d.connected };
  } catch {
    return { connected: false };
  }
}

/** Convert an object-URL / remote URL into a data URL (base64) */
export async function dataUrlFromUrl(url: string): Promise<string> {
  const blob = await (await fetch(url)).blob();
  return await new Promise<string>((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(new Error("read failed"));
    fr.readAsDataURL(blob);
  });
}

export type SSEHandler = (ev: Record<string, unknown>) => void;

/** Stream-parse a Server-Sent Events response body, firing JSON events */
export async function readSSE(res: Response, onEvent: SSEHandler): Promise<void> {
  if (!res.body) throw new Error("no stream body");
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const blocks = buf.split("\n\n");
    buf = blocks.pop() ?? "";
    for (const block of blocks) {
      const payload = block
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim())
        .join("");
      if (!payload) continue;
      try {
        onEvent(JSON.parse(payload) as Record<string, unknown>);
      } catch {
        /* ignore partial frames */
      }
    }
  }
}
