import { NextRequest } from "next/server";
import { proxyWorker } from "@/lib/worker-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 600;

async function route(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyWorker(req, `/api/bridge/p2p/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`);
}
export { route as GET, route as POST };
