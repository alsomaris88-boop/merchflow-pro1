import { NextRequest, NextResponse } from "next/server";
import { db } from "@/db";
import { accounts } from "@/db/schema";
import { asc, eq } from "drizzle-orm";

export const dynamic = "force-dynamic";

// The Account Manager starts with ZERO accounts — never fabricate identities.
const FALLBACK: never[] = [];

export async function GET() {
  try {
    const rows = await db.select().from(accounts).orderBy(asc(accounts.id));
    if (rows.length) return NextResponse.json({ source: "db", items: rows });
    return NextResponse.json({ source: "db", items: FALLBACK });
  } catch {
    return NextResponse.json({ source: "fallback", items: FALLBACK });
  }
}

export async function POST(req: NextRequest) {
  try {
    const b = (await req.json()) as { label?: string; email?: string; provider?: string };
    if (!b.email || !b.label) return NextResponse.json({ error: "label + email required" }, { status: 400 });
    const [row] = await db
      .insert(accounts)
      .values({
        label: b.label.slice(0, 40),
        email: b.email.slice(0, 120),
        provider: (b.provider ?? "chrome").slice(0, 20),
        status: "ready",
      })
      .returning();
    return NextResponse.json({ ok: true, item: row });
  } catch (e) {
    return NextResponse.json({ error: "insert failed", detail: String(e) }, { status: 500 });
  }
}

export async function PATCH(req: NextRequest) {
  try {
    const b = (await req.json()) as { id?: number; status?: string; bumpUsed?: boolean };
    if (!b.id) return NextResponse.json({ error: "id required" }, { status: 400 });
    const patch: Record<string, unknown> = { lastHeartbeat: new Date() };
    if (b.status) patch.status = b.status.slice(0, 20);
    await db.update(accounts).set(patch).where(eq(accounts.id, b.id));
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: "patch failed", detail: String(e) }, { status: 500 });
  }
}
