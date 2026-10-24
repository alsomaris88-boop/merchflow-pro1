import { NextRequest, NextResponse } from "next/server";
import { proxyWorker } from "@/lib/worker-proxy";

export const dynamic = "force-dynamic";

/** SQLite-backed verified browser sessions are the only account source. */
export function GET(req: NextRequest) {
  return proxyWorker(req, "/api/accounts", 5000);
}

export function DELETE(req: NextRequest) {
  return proxyWorker(req, `/api/accounts${req.nextUrl.search}`, 5000);
}

export function POST() {
  return NextResponse.json({ ok: false, reason: "Use Add Gemini Account and complete the real Google/Gemini browser login." }, { status: 405 });
}
export const PATCH = POST;
