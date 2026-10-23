import { NextRequest } from "next/server";
import { proxyWorker } from "@/lib/worker-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyWorker(req, `/api/bridge/designs/${encodeURIComponent(id)}${req.nextUrl.search}`);
}
