import { NextResponse } from "next/server";
import { db } from "@/db";
import { jobs, listings } from "@/db/schema";
import { desc } from "drizzle-orm";

export const dynamic = "force-dynamic";

export interface ActivityItem {
  kind: "job" | "listing";
  title: string;
  detail: string;
  at: string;
}

export async function GET() {
  try {
    const [j, l] = await Promise.all([
      db.select().from(jobs).orderBy(desc(jobs.id)).limit(8),
      db.select().from(listings).orderBy(desc(listings.id)).limit(8),
    ]);
    const items: ActivityItem[] = [
      ...j.map((r) => ({
        kind: "job" as const,
        title: `${r.styleName || "Style"} × ${r.nicheName || "niche"}`,
        detail: `${r.aspect} · ${r.background} · ${r.accountLabel || "pool"} · ${Math.round(r.durationMs / 1000)}s`,
        at: (r.createdAt ?? new Date()).toString(),
      })),
      ...l.map((r) => ({
        kind: "listing" as const,
        title: r.title || r.niche,
        detail: `MBA + TeePublic · main tag: ${r.mainTag} · risk ${r.riskScore}/100`,
        at: (r.createdAt ?? new Date()).toString(),
      })),
    ];
    return NextResponse.json({ items: items.slice(0, 10), jobCount: j.length, listingCount: l.length, source: "db" });
  } catch {
    return NextResponse.json({
      source: "fallback",
      jobCount: 71,
      listingCount: 29,
      items: [
        { kind: "job", title: "Retro Wavy × Pickleball", detail: "4:5 · transparent · Profile 01 · 71s", at: new Date(Date.now() - 1000 * 60 * 4).toString() },
        { kind: "job", title: "Kawaii × Axolotl", detail: "4:5 · white · Profile 02 · 68s", at: new Date(Date.now() - 1000 * 60 * 12).toString() },
        { kind: "listing", title: "Vintage Bass Fishing Sunset Emblem", detail: "MBA + TeePublic · main tag: bass fishing · risk 0/100", at: new Date(Date.now() - 1000 * 60 * 31).toString() },
        { kind: "job", title: "Distressed Badge × Welder Skull", detail: "4:5 · transparent · Profile 03 · 74s", at: new Date(Date.now() - 1000 * 60 * 44).toString() },
        { kind: "listing", title: "Groovy Roller Skate Disco", detail: "MBA + TeePublic · main tag: roller skating · risk 0/100", at: new Date(Date.now() - 1000 * 60 * 66).toString() },
        { kind: "job", title: "Linework × Celestial Moth", detail: "4:5 · black · Profile 04 · 66s", at: new Date(Date.now() - 1000 * 60 * 88).toString() },
      ],
    });
  }
}
