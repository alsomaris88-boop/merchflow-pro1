import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";

/** Forward a design + lab settings to the real libvips pipeline. */
export async function POST(req: NextRequest) {
  const payload = await req.text();
  try {
    const r = await fetch(`${WORKER}/process`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      signal: AbortSignal.timeout(240000),
    });
    const d = await r.json();
    return NextResponse.json(d, { status: r.ok ? 200 : r.status });
  } catch {
    return NextResponse.json(
      { ok: false, reason: "local engine offline" },
      { status: 503 }
    );
  }
}
