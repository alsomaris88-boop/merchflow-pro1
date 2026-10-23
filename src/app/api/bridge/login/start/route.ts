import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";

/**
 * Start the one-time interactive login window (isolated profile) and begin the
 * 1.5s background capture sensor on the local engine.
 */
export async function POST(req: NextRequest) {
  const payload = await req.text();
  try {
    const r = await fetch(`${WORKER}/accounts/login/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      signal: AbortSignal.timeout(20000),
    });
    const d = await r.json();
    return NextResponse.json(d, { status: r.ok ? 200 : r.status });
  } catch {
    return NextResponse.json(
      {
        ok: false,
        state: "failed",
        reason:
          "The local engine is not running. Start it with desktop/run_windows.bat, then try again.",
      },
      { status: 503 }
    );
  }
}
