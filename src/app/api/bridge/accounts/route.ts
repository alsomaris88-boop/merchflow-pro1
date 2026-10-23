import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";

/** Headless account status from the local engine. */
export async function GET() {
  try {
    const r = await fetch(`${WORKER}/accounts/status`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
    });
    if (!r.ok) throw new Error(String(r.status));
    return NextResponse.json(await r.json());
  } catch {
    return NextResponse.json({ ok: false, profiles: [], headless: true }, { status: 503 });
  }
}

/** Forward pasted session cookies to the engine for SNlM0e verification. */
export async function POST(req: NextRequest) {
  const payload = await req.text();
  try {
    const r = await fetch(`${WORKER}/accounts/cookies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      signal: AbortSignal.timeout(30000),
    });
    const d = await r.json();
    return NextResponse.json(d, { status: r.status });
  } catch {
    return NextResponse.json({ ok: false, authed: false, reason: "local engine offline" }, { status: 503 });
  }
}
