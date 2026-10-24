import { pgTable, serial, text, integer, timestamp, boolean } from "drizzle-orm/pg-core";

/** Generation jobs routed through the account pool */
export const jobs = pgTable("jobs", {
  id: serial("id").primaryKey(),
  styleName: text("style_name").notNull().default(""),
  nicheName: text("niche_name").notNull().default(""),
  aspect: text("aspect").notNull().default("4:5"),
  background: text("background").notNull().default("transparent"),
  textOverride: text("text_override").notNull().default(""),
  accountLabel: text("account_label").notNull().default(""),
  status: text("status").notNull().default("done"), // queued | running | done | failed
  durationMs: integer("duration_ms").notNull().default(0),
  createdAt: timestamp("created_at").defaultNow(),
});

/** Dual-marketplace listings (Amazon MBA + TeePublic) */
export const listings = pgTable("listings", {
  id: serial("id").primaryKey(),
  niche: text("niche").notNull().default(""),
  brand: text("brand").notNull().default(""),
  title: text("title").notNull().default(""),
  bullet1: text("bullet1").notNull().default(""),
  bullet2: text("bullet2").notNull().default(""),
  tpTitle: text("tp_title").notNull().default(""),
  tpDescription: text("tp_description").notNull().default(""),
  mainTag: text("main_tag").notNull().default(""),
  tags: text("tags").notNull().default(""), // comma separated, 10-15
  riskScore: integer("risk_score").notNull().default(0),
  createdAt: timestamp("created_at").defaultNow(),
});

/** Trend radar — BSR tracked niches (browse node 21393915011) */
export const niches = pgTable("niches", {
  id: serial("id").primaryKey(),
  keyword: text("keyword").notNull(),
  source: text("source").notNull().default("mba"), // mba | etsy | pinterest
  bsrNow: integer("bsr_now").notNull().default(0),
  bsrPrev: integer("bsr_prev").notNull().default(0),
  isMerch: boolean("is_merch").notNull().default(true),
  opportunity: integer("opportunity").notNull().default(50),
  updatedAt: timestamp("updated_at").defaultNow(),
});

/** SaaS licensing with HWID lock */
export const licenses = pgTable("licenses", {
  id: serial("id").primaryKey(),
  key: text("key").notNull().unique(),
  plan: text("plan").notNull().default("pro"), // starter | pro | team
  status: text("status").notNull().default("active"), // active | revoked
  hwid: text("hwid"),
  seats: integer("seats").notNull().default(10),
  createdAt: timestamp("created_at").defaultNow(),
});
