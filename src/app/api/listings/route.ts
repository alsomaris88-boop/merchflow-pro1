import { NextRequest, NextResponse } from "next/server";
import { db } from "@/db";
import { listings } from "@/db/schema";
import { desc } from "drizzle-orm";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const rows = await db.select().from(listings).orderBy(desc(listings.id)).limit(40);
    return NextResponse.json({ items: rows });
  } catch {
    return NextResponse.json({ items: [] });
  }
}

export async function POST(req: NextRequest) {
  try {
    const b = (await req.json()) as Record<string, unknown>;
    const s = (v: unknown, n: number) => String(v ?? "").slice(0, n);
    const [row] = await db
      .insert(listings)
      .values({
        niche: s(b.niche, 120),
        brand: s(b.brand, 60),
        title: s(b.title, 120),
        bullet1: s(b.bullet1, 512),
        bullet2: s(b.bullet2, 512),
        tpTitle: s(b.tpTitle, 120),
        tpDescription: s(b.tpDescription, 640),
        mainTag: s(b.mainTag, 80),
        tags: s(b.tags, 640),
        riskScore: Math.min(100, Math.max(0, Number(b.riskScore ?? 0) || 0)),
      })
      .returning();
    return NextResponse.json({ ok: true, item: row });
  } catch (e) {
    return NextResponse.json({ error: "listing insert failed", detail: String(e) }, { status: 500 });
  }
}
