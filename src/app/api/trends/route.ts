import { NextRequest, NextResponse } from "next/server";
import { db } from "@/db";
import { niches } from "@/db/schema";
import { desc } from "drizzle-orm";
import {
  getSuggestions,
  FALLBACK_MOVERS,
  ETSY_BESTSELLERS,
  PIN_BOARDS,
  type MoverItem,
} from "@/lib/trends-engine";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const view = req.nextUrl.searchParams.get("view") ?? "suggest";

  try {
    if (view === "suggest") {
      const q = (req.nextUrl.searchParams.get("q") ?? "").slice(0, 80);
      if (!q.trim()) return NextResponse.json({ error: "q is required" }, { status: 400 });
      const { live, items } = await getSuggestions(q);
      return NextResponse.json({ source: live ? "live:completion.amazon.com" : "simulated:a-z-expansion", items });
    }

    if (view === "movers") {
      try {
        const rows = await db.select().from(niches).orderBy(desc(niches.opportunity)).limit(20);
        if (rows.length) {
          const items: MoverItem[] = rows.map((r) => ({
            keyword: r.keyword,
            bsrNow: r.bsrNow,
            bsrPrev: r.bsrPrev,
            delta: r.bsrPrev > 0 ? Math.round(((r.bsrPrev - r.bsrNow) / r.bsrPrev) * 100) : 0,
            opportunity: r.opportunity,
            isMerch: r.isMerch,
          }));
          return NextResponse.json({ source: "db:node-21393915011", items });
        }
      } catch {
        /* fall through to seed data */
      }
      return NextResponse.json({ source: "seed:node-21393915011", items: FALLBACK_MOVERS });
    }

    if (view === "etsy") {
      return NextResponse.json({ source: "recon:etsy-search", items: ETSY_BESTSELLERS });
    }

    if (view === "pinterest") {
      return NextResponse.json({ source: "recon:pinterest-visual", items: PIN_BOARDS });
    }

    return NextResponse.json({ error: "unknown view" }, { status: 400 });
  } catch (e) {
    return NextResponse.json({ error: "trend radar failure", detail: String(e) }, { status: 500 });
  }
}
