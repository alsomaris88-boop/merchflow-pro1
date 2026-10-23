import { NextRequest, NextResponse } from "next/server";
import { db } from "@/db";
import { licenses } from "@/db/schema";
import { eq } from "drizzle-orm";

export const dynamic = "force-dynamic";

const DEMO_KEYS: Record<string, { plan: string; seats: number }> = {
  "MF-PRO-DEMO-2026-0001": { plan: "pro", seats: 10 },
  "MF-TEAM-DEMO-2026-0002": { plan: "team", seats: 25 },
  "MF-STARTER-DEMO-2026-0003": { plan: "starter", seats: 2 },
};

export async function POST(req: NextRequest) {
  try {
    const b = (await req.json()) as { key?: string; hwid?: string };
    const key = (b.key ?? "").trim().toUpperCase().slice(0, 40);
    const hwid = (b.hwid ?? "").slice(0, 64);
    if (!key) return NextResponse.json({ valid: false, reason: "Enter a license key." }, { status: 400 });

    try {
      const [row] = await db.select().from(licenses).where(eq(licenses.key, key)).limit(1);
      if (row) {
        if (row.status !== "active") return NextResponse.json({ valid: false, reason: "License revoked. Contact support." });
        if (row.hwid && hwid && row.hwid !== hwid) {
          return NextResponse.json({ valid: false, reason: "HWID lock: key already bound to another machine." });
        }
        if (!row.hwid && hwid) {
          await db.update(licenses).set({ hwid }).where(eq(licenses.id, row.id));
        }
        return NextResponse.json({ valid: true, plan: row.plan, seats: row.seats, bound: true, source: "db" });
      }
    } catch {
      /* DB offline → check demo keys */
    }

    const demo = DEMO_KEYS[key];
    if (demo) return NextResponse.json({ valid: true, plan: demo.plan, seats: demo.seats, bound: true, source: "offline-verify" });
    return NextResponse.json({ valid: false, reason: "Invalid key. Check your LemonSqueezy receipt." });
  } catch (e) {
    return NextResponse.json({ valid: false, reason: String(e) }, { status: 500 });
  }
}
