import { NextRequest, NextResponse } from "next/server";
import { proxyWorker } from "@/lib/worker-proxy";

export const dynamic = "force-dynamic";

export function GET(req: NextRequest) {
  return proxyWorker(req, "/accounts/status", 5000);
}

export function POST() {
  return NextResponse.json({ ok: false, reason: "Pasted cookies cannot create accounts. Complete the real Google/Gemini browser login." }, { status: 405 });
}
