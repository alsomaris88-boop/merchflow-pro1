import { NextRequest } from "next/server";
import { proxyWorker } from "@/lib/worker-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 600;

export async function POST(req: NextRequest) {
  return proxyWorker(req, "/p2p");
}
