import { NextRequest, NextResponse } from "next/server";

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";

/** Stream files, ZIPs and SSE without collecting them in the Next.js process. */
export async function proxyWorker(req: NextRequest, path: string, timeoutMs?: number): Promise<Response> {
  try {
    const upstream = await fetch(`${WORKER}${path}`, {
      method: req.method,
      headers: {
        "Content-Type": req.headers.get("content-type") ?? "application/json",
        Accept: req.headers.get("accept") ?? "*/*",
        "Accept-Encoding": "identity",
        ...(req.headers.has("last-event-id") ? { "Last-Event-ID": req.headers.get("last-event-id")! } : {}),
      },
      body: ["GET", "HEAD"].includes(req.method) ? undefined : req.body,
      duplex: "half",
      cache: "no-store",
      signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : req.signal,
    } as RequestInit & { duplex: "half" });
    const headers = new Headers({ "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no" });
    for (const name of ["content-type", "content-disposition"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (error) {
    return NextResponse.json({ ok: false, reason: error instanceof Error && error.name === "TimeoutError"
      ? "Local engine request timed out. Check Designs History before retrying."
      : "Local engine unavailable. Start run_windows.bat; no simulated result was created." }, { status: 503 });
  }
}
