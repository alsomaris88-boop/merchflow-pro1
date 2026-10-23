import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";

const ACTION_RE = /^(status|cancel)$/;

/**
 * Poll or cancel a running login flow.
 * GET  /api/bridge/login/flow/<id>         → sensor status
 * POST /api/bridge/login/flow/<id>?cancel  → dismiss
 */
async function proxy(flowId: string, method: "GET" | "POST") {
  const url =
    method === "GET"
      ? `${WORKER}/accounts/login/status/${flowId}`
      : `${WORKER}/accounts/login/cancel/${flowId}`;
  try {
    const r = await fetch(url, {
      method,
      cache: "no-store",
      signal: AbortSignal.timeout(6000),
    });
    const d = await r.json();
    return NextResponse.json(d, { status: r.status });
  } catch {
    return NextResponse.json(
      { ok: false, state: "failed", reason: "local engine offline" },
      { status: 503 }
    );
  }
}

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ flowId: string }> }
) {
  const { flowId } = await ctx.params;
  if (!ACTION_RE.test(flowId) && flowId.length > 40) {
    return NextResponse.json({ ok: false, reason: "bad flow id" }, { status: 400 });
  }
  return proxy(flowId, "GET");
}

export async function POST(
  _req: NextRequest,
  ctx: { params: Promise<{ flowId: string }> }
) {
  const { flowId } = await ctx.params;
  return proxy(flowId, "POST");
}
