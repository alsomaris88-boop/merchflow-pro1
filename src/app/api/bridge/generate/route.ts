import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const maxDuration = 600;

const WORKER = process.env.MERCHFLOW_WORKER_URL ?? "http://127.0.0.1:8000";

/**
 * Streaming generate proxy: forwards the dual-dropzone payload to the local
 * Python worker and pipes its Server-Sent Events straight back to the UI.
 * Falls back to 503 so the client can switch to simulated preview.
 */
export async function POST(req: NextRequest) {
  const payload = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${WORKER}/generate/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: payload,
    });
  } catch {
    return NextResponse.json(
      { ok: false, reason: "local engine offline — simulated preview available" },
      { status: 503 }
    );
  }

  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { ok: false, reason: `engine responded ${upstream.status}` },
      { status: 502 }
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
