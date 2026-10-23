import { NextRequest } from "next/server";
import { proxyWorker } from "@/lib/worker-proxy";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export async function GET(req: NextRequest) { return proxyWorker(req, "/api/bridge/listings"); }
export async function POST(req: NextRequest) { return proxyWorker(req, "/api/bridge/listings"); }
