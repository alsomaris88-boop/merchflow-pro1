import { NextRequest } from "next/server";
import { proxyWorker } from "@/lib/worker-proxy";

export const dynamic = "force-dynamic";

/** Actual local activity, never fabricated account/profile events. */
export function GET(req: NextRequest) {
  return proxyWorker(req, "/api/activity", 5000);
}
