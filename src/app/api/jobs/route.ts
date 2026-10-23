import { NextRequest, NextResponse } from "next/server";
import { db } from "@/db";
import { jobs } from "@/db/schema";
import { desc } from "drizzle-orm";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const rows = await db.select().from(jobs).orderBy(desc(jobs.id)).limit(24);
    return NextResponse.json({ items: rows });
  } catch {
    return NextResponse.json({ items: [] });
  }
}

export async function POST(req: NextRequest) {
  try {
    const b = (await req.json()) as Record<string, unknown>;
    const [row] = await db
      .insert(jobs)
      .values({
        styleName: String(b.styleName ?? "").slice(0, 120),
        nicheName: String(b.nicheName ?? "").slice(0, 120),
        aspect: String(b.aspect ?? "4:5").slice(0, 8),
        background: String(b.background ?? "transparent").slice(0, 20),
        textOverride: String(b.textOverride ?? "").slice(0, 120),
        accountLabel: String(b.accountLabel ?? "").slice(0, 40),
        status: String(b.status ?? "done").slice(0, 12),
        durationMs: Math.min(600000, Math.max(0, Number(b.durationMs ?? 0) || 0)),
      })
      .returning();
    return NextResponse.json({ ok: true, item: row });
  } catch (e) {
    return NextResponse.json({ error: "job insert failed", detail: String(e) }, { status: 500 });
  }
}
