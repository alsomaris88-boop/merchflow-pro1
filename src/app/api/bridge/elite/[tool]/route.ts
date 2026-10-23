import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";
const TOOLS = new Set(["polycanvas", "ocr", "phash", "vectorize", "distress", "split"]);

/** Proxy a single elite tool call to the local engine. */
export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ tool: string }> }
) {
  const { tool } = await ctx.params;
  if (!TOOLS.has(tool)) {
    return NextResponse.json({ ok: false, reason: "unknown tool" }, { status: 400 });
  }
  const payload = await req.text();
  try {
    const r = await fetch(`${WORKER}/elite/${tool}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      signal: AbortSignal.timeout(240000),
    });
    const d = await r.json();
    return NextResponse.json(d, { status: r.status });
  } catch {
    return NextResponse.json(
      { ok: false, available: false, reason: "local engine offline — using in-browser engine" },
      { status: 503 }
    );
  }
}
