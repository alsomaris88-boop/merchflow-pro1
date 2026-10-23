import { NextRequest, NextResponse } from "next/server";
import { analyzeText } from "@/lib/trademark-engine";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { text?: string };
    const text = (body.text ?? "").slice(0, 4000);
    if (!text.trim()) {
      return NextResponse.json({ error: "text is required" }, { status: 400 });
    }
    const result = analyzeText(text);
    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json({ error: "scan failed", detail: String(e) }, { status: 500 });
  }
}
