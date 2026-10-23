import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";

/** Health probe for the local Python desktop engine (FastAPI worker). */
export async function GET() {
  try {
    const r = await fetch(`${WORKER}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(1800),
    });
    if (!r.ok) throw new Error(`worker ${r.status}`);
    const d = (await r.json()) as Record<string, unknown>;
    return NextResponse.json({ connected: true, ...d });
  } catch {
    return NextResponse.json({
      connected: false,
      engine: "simulated-browser-preview",
      hint: "Start the local engine: desktop/run_windows.bat (uvicorn on 127.0.0.1:8000)",
    });
  }
}
