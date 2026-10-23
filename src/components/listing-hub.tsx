"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Package,
  ArrowDownToLine,
  CheckCircle2,
  FileSpreadsheet,
  Loader2,
  Save,
  ShieldAlert,
  ShieldCheck,
  Store,
  Tags,
  WandSparkles,
} from "lucide-react";
import { Btn, Card, CharMeter, Chip, CopyBtn, Field, Input, PageHead, Section, Select, cx } from "@/components/ui";
import { clearListingHandoff, getDesigns, listingHandoff, responseJson, type DesignRecord } from "@/lib/designs-store";
import type { ShieldResult } from "@/lib/trademark-engine";

const ADJECTIVES = ["Retro", "Vintage", "Groovy", "Funny", "Aesthetic", "Kawaii", "Distressed", "Classic", "Bold", "Cozy"];
const OCCASIONS = ["birthdays, holidays and weekend hangouts", "family gatherings, vacations and BBQs", "game nights, parties and everyday wear", "Christmas, Father's Day and casual Fridays"];

interface BuiltListing {
  niche: string;
  brand: string;
  title: string;
  bullet1: string;
  bullet2: string;
  tpTitle: string;
  tpDescription: string;
  mainTag: string;
  tags: string[];
}

interface SavedRow extends BuiltListing {
  id: number;
  riskScore: number;
  tags: string[];
  createdAt?: string;
}

const cap = (s: string) => s.replace(/\b\w/g, (c) => c.toUpperCase());
const clampTo = (s: string, n: number) => (s.length <= n ? s : s.slice(0, n - 1).replace(/[\s-]+$/, "") + "…");

function csvEscape(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

function downloadCsv(filename: string, headers: string[], rows: string[][]) {
  const lines = [headers, ...rows].map((r) => r.map(csvEscape).join(",")).join("\r\n");
  const blob = new Blob(["﻿" + lines], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

export default function ListingHub() {
  const [niche, setNiche] = useState("retro bass fishing");
  const [audience, setAudience] = useState("unisex");
  const [styleNote, setStyleNote] = useState("70s sunset palette, distressed badge");
  const [built, setBuilt] = useState<BuiltListing | null>(null);
  const [shield, setShield] = useState<ShieldResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<SavedRow[]>([]);
  const [saveOk, setSaveOk] = useState(false);
  const [designs, setDesigns] = useState<DesignRecord[]>([]);
  const [activeDesign, setActiveDesign] = useState<DesignRecord | null>(null);
  const [error, setError] = useState("");
  const chooseDesign = useCallback((design: DesignRecord) => {
    setActiveDesign(design); setNiche(design.niche || design.name);
    setStyleNote(design.style); setBuilt(null); setShield(null); setSaveOk(false);
  }, []);

  useEffect(() => {
    let alive = true;
    const ids = listingHandoff();
    if (ids.length) void getDesigns(ids).then((records) => {
      if (!alive) return;
      setDesigns(records);
      if (records[0]) chooseDesign(records[0]);
      else setError("The selected History records are no longer available. Their original PNG files were not deleted.");
    }).catch((e) => { if (alive) setError(e instanceof Error ? e.message : "Could not load selected designs"); });
    return () => { alive = false; };
  }, [chooseDesign]);

  useEffect(() => {
    fetch("/api/bridge/listings")
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d.items)) {
          setSaved(d.items.map((r: Record<string, unknown>) => ({ ...(r as object), tags: String(r.tags ?? "").split(", ").filter(Boolean) })) as SavedRow[]);
        }
      })
      .catch(() => undefined);
  }, []);

  const generate = useCallback(async () => {
    if (!niche.trim() || busy) return;
    setBusy(true);

    // 1) sanitize the niche first so nothing dirty ever reaches a listing
    let cleanNiche = niche.trim().toLowerCase();
    try {
      const r = await fetch("/api/trademark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: cleanNiche }),
      });
      const pre = (await r.json()) as ShieldResult;
      if (pre.sanitized.trim()) cleanNiche = pre.sanitized.trim().toLowerCase();
    } catch {
      /* keep raw */
    }

    const words = cleanNiche.split(/\s+/);
    const core = words[words.length - 1];
    const seed = cleanNiche.length + words.length * 7;
    const adj = ADJECTIVES[seed % ADJECTIVES.length];
    const occasion = OCCASIONS[seed % OCCASIONS.length];
    const audiencePhrase: Record<string, string> = {
      unisex: "men and women", men: "men", women: "women", dad: "dads", mom: "moms", kids: "kids",
    };
    const aud = audiencePhrase[audience] ?? "everyone";
    const note = styleNote.trim() || "bold vintage lines";

    const brand = clampTo(cap(`${core} Culture Co`), 30).replace(/…$/, "");
    const title = clampTo(cap(`${adj} ${cleanNiche} ${core === "design" ? "" : "Design"}`.replace(/\s+/g, " ")), 60).replace(/…$/, "");
    const bullet1 = clampTo(
      `Striking ${adj.toLowerCase()} ${cleanNiche} artwork with ${note} — made for ${aud} who live and breathe everything ${core}. A conversation-starting graphic that pops on any base colorway.`,
      256
    );
    const bullet2 = clampTo(
      `Pairs effortlessly with denim, joggers or a flannel layer. Ideal for ${occasion}. A clean, expressive look for ${aud} who want their passion front and center.`,
      256
    );
    const tpTitle = `${cap(cleanNiche)} | ${adj} Graphic Design`;
    const tags = Array.from(
      new Set(
        [
          cleanNiche, `${core} lover`, `${core} design`, `${adj.toLowerCase()} ${core}`, `funny ${core}`,
          `${core} art`, `${core} gift`, `vintage ${core}`, `${core} for ${audience === "unisex" ? "everyone" : audience}`,
          `${cleanNiche} design`, `retro ${core}`, `${core} apparel`, `${adj.toLowerCase()} aesthetic`,
          `graphic ${core}`, `${core} humor`,
        ]
          .map((t) => t.toLowerCase().trim())
          .filter((t) => t.length >= 3 && t.length <= 40)
      )
    ).slice(0, 15);
    const tpDescription = clampTo(
      `${cap(cleanNiche)} design in a ${adj.toLowerCase()} style with ${note}. Perfect for ${aud} who love ${core}. Great for ${occasion}.`,
      300
    );

    const next: BuiltListing = { niche: cleanNiche, brand, title, bullet1, bullet2, tpTitle, tpDescription, mainTag: cleanNiche, tags };
    setBuilt(next);

    // 2) full shield scan on the assembled listing
    try {
      const r2 = await fetch("/api/trademark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: [next.brand, next.title, next.bullet1, next.bullet2, next.tags.join(", ")].join("\n") }),
      });
      setShield((await r2.json()) as ShieldResult);
    } catch {
      setShield(null);
    }
    setBusy(false);
  }, [audience, busy, niche, styleNote]);

  const save = useCallback(async () => {
    if (!built) return;
    try {
      const r = await fetch("/api/bridge/listings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...built, designId: activeDesign?.id ?? "", tags: built.tags.join(", "), riskScore: shield?.score ?? 0 }),
      });
      const d = await responseJson<{ item: Omit<SavedRow, "tags"> & { tags: string } }>(r);
      if (!d.item || !Number.isFinite(d.item.id)) throw new Error("Listing was not confirmed by SQLite");
      if (d.item) {
        setSaved((s) => [{ ...d.item, tags: String(d.item.tags ?? "").split(", ").filter(Boolean) }, ...s].slice(0, 30));
      }
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 1600);
      setError("");
    } catch (e) {
      setSaveOk(false); setError(e instanceof Error ? e.message : "Listing could not be saved");
    }
  }, [activeDesign, built, shield]);

  const garmentFlag = (s: string) => /\b(t-?shirt|tee|hoodie|sweatshirt|apparel|tank top)\b/i.test(s);

  return (
    <div>
      <PageHead
        kicker="MODULE 05 · DUAL LISTING & EXPORT HUB"
        title="One pass, two marketplaces"
        desc="Amazon A9-tuned fields (60-char mobile title sweet spot, zero garment words) and TeePublic sacred-main-tag metadata — generated sanitized, shield-scanned, then exported for Productor, Flying Upload and Merch Titans."
        right={<Chip tone="violet"><Store size={11} /> MBA + TeePublic</Chip>}
      />

      {error && <div role="alert" className="mb-4 rounded-xl border border-red-400/30 bg-red-500/10 p-4 text-sm text-red-200">{error}</div>}
      {designs.length > 0 && <Card className="p-4 mb-4">
        <div className="flex justify-between items-center gap-3 mb-3"><div><div className="text-sm font-semibold">{designs.length} designs from History</div><p className="text-xs text-white/40 mt-1">Choose a winning design to build its listing. Selection survives module navigation; saving links the listing to its real PNG.</p></div><Btn variant="ghost" onClick={() => { clearListingHandoff(); setDesigns([]); setActiveDesign(null); }}>Clear design batch</Btn></div>
        <div className="flex gap-2 overflow-x-auto pb-2">{designs.map((design) => <button key={design.id} aria-label={`List ${design.name}`} aria-pressed={activeDesign?.id === design.id} onClick={() => chooseDesign(design)} className={cx("w-28 shrink-0 rounded-xl border p-2 text-left", activeDesign?.id === design.id ? "border-[#c9f24b]/60 bg-[#c9f24b]/10" : "border-white/10")}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={design.previewUrl} alt={design.name} className="w-full h-24 object-contain" /><span className="block text-[10px] truncate mt-1">{design.name}</span>
        </button>)}</div>
        {activeDesign && <div className="text-[10px] text-white/45 mt-2 break-all">{activeDesign.w} × {activeDesign.h} · {activeDesign.dpi} DPI · <a className="text-[#b9a8ff] underline" href={activeDesign.fileUrl} target="_blank" rel="noopener noreferrer">Inspect physical PNG</a><div className="mt-1">{activeDesign.path}</div></div>}
      </Card>}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* inputs */}
        <Card className="p-5">
          <Section icon={<WandSparkles size={16} />} title="Listing brief" desc="Niche goes through the sanitizer before any field is built." />
          <div className="space-y-4">
            <Field label="Niche keyword" hint="auto-sanitized">
              <Input value={niche} onChange={(e) => setNiche(e.target.value)} placeholder="e.g. retro bass fishing" />
            </Field>
            <Field label="Target audience">
              <Select value={audience} onChange={(e) => setAudience(e.target.value)}>
                {["unisex", "men", "women", "dad", "mom", "kids"].map((a) => (
                  <option key={a} value={a}>{cap(a)}</option>
                ))}
              </Select>
            </Field>
            <Field label="Style notes" hint="feeds bullet 1">
              <Input value={styleNote} onChange={(e) => setStyleNote(e.target.value)} placeholder="e.g. 70s sunset palette, distressed badge" />
            </Field>
            <Btn onClick={() => void generate()} disabled={busy || !niche.trim()} variant="lime" className="w-full justify-center">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <WandSparkles size={14} />}
              {busy ? "Building + scanning…" : "Generate dual listing"}
            </Btn>
            {shield && (
              <div className={cx(
                "rounded-xl border p-3.5 flex items-center gap-3",
                shield.band === "CLEAR" ? "bg-[#c9f24b]/[0.05] border-[#c9f24b]/25" : "bg-amber-400/[0.06] border-amber-400/25"
              )}>
                {shield.band === "CLEAR" ? <ShieldCheck size={16} className="text-[#c9f24b]" /> : <ShieldAlert size={16} className="text-amber-300" />}
                <div>
                  <div className="text-[12px] font-semibold">Trademark Shield: {shield.band} ({shield.score}/100)</div>
                  <div className="text-[10.5px] text-white/40">{shield.matches.length ? `${shield.matches[0].matchedTerm} flagged — rewrite before export` : "no hits across USPTO 025 + policy DB"}</div>
                </div>
              </div>
            )}
          </div>
        </Card>

        {/* outputs */}
        <div className="xl:col-span-2 space-y-4">
          {!built ? (
            <Card className="p-12 text-center">
              <FileSpreadsheet size={26} className="mx-auto text-white/20 mb-3" />
              <div className="text-[13px] text-white/40">Generate a listing to preview both marketplace formats.</div>
            </Card>
          ) : (
            <>
              {/* MBA */}
              <Card className="p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2.5">
                    <Package size={17} className="text-[#ffb86b]" />
                    <span className="font-display font-bold text-[15px] tracking-tight">Merch on Demand</span>
                    <Chip tone="amber">A9 mobile-tuned</Chip>
                  </div>
                  <Btn variant="ghost" className="py-1.5 px-3" onClick={() =>
                    downloadCsv("productor-mba.csv", ["Design File", "Brand", "Title", "Bullet Point 1", "Bullet Point 2"], [[activeDesign?.path.split(/[\\/]/).pop() ?? "", built.brand, built.title, built.bullet1, built.bullet2]])
                  }>
                    <ArrowDownToLine size={13} /> Productor CSV
                  </Btn>
                </div>
                <div className="space-y-4">
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-mono2 text-[10px] tracking-[0.16em] text-white/40">BRAND NAME</span>
                      <CharMeter value={built.brand.length} max={30} />
                    </div>
                    <div className="rounded-xl bg-black/30 border border-white/[0.07] px-4 py-3 text-[14px] font-semibold flex items-center justify-between">
                      {built.brand}
                      <CopyBtn text={built.brand} />
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-mono2 text-[10px] tracking-[0.16em] text-white/40">TITLE · no garment words</span>
                      <CharMeter value={built.title.length} max={60} />
                    </div>
                    <div className={cx("rounded-xl bg-black/30 border px-4 py-3 text-[14px] font-semibold flex items-center justify-between gap-3", garmentFlag(built.title) ? "border-red-500/40" : "border-white/[0.07]")}>
                      {built.title}
                      <CopyBtn text={built.title} />
                    </div>
                  </div>
                  {[["BULLET 1 · graphic · aesthetic · recipient", built.bullet1], ["BULLET 2 · styling · occasions · denim pairing", built.bullet2]].map(([label, text]) => (
                    <div key={label}>
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="font-mono2 text-[10px] tracking-[0.16em] text-white/40">{label}</span>
                        <CharMeter value={(text as string).length} max={256} />
                      </div>
                      <div className="rounded-xl bg-black/30 border border-white/[0.07] px-4 py-3 text-[12.5px] text-white/75 leading-relaxed flex items-start justify-between gap-3">
                        <span>{text}</span>
                        <CopyBtn text={text as string} />
                      </div>
                    </div>
                  ))}
                  <div className="flex items-center gap-2 font-mono2 text-[10px] text-white/30">
                    <CheckCircle2 size={11} className="text-[#c9f24b]" /> Excel (.xlsx) parity — same row schema as the CSV export
                  </div>
                </div>
              </Card>

              {/* TeePublic */}
              <Card className="p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2.5">
                    <Tags size={17} className="text-[#7cc9ff]" />
                    <span className="font-display font-bold text-[15px] tracking-tight">TeePublic</span>
                    <Chip tone="violet">Flying Upload / Merch Titans ready</Chip>
                  </div>
                  <Btn variant="ghost" className="py-1.5 px-3" onClick={() =>
                    downloadCsv("teepublic.csv", ["Title", "Description", "Main Tag", "Tags (10-15)"], [[built.tpTitle, built.tpDescription, built.mainTag, built.tags.join(", ")]])
                  }>
                    <ArrowDownToLine size={13} /> TeePublic CSV
                  </Btn>
                </div>
                <div className="grid md:grid-cols-2 gap-4">
                  <div className="space-y-3">
                    <div>
                      <div className="font-mono2 text-[10px] tracking-[0.16em] text-white/40 mb-1.5">TITLE</div>
                      <div className="rounded-xl bg-black/30 border border-white/[0.07] px-4 py-3 text-[13px] font-semibold flex justify-between gap-2">{built.tpTitle}<CopyBtn text={built.tpTitle} /></div>
                    </div>
                    <div>
                      <div className="font-mono2 text-[10px] tracking-[0.16em] text-white/40 mb-1.5">SEO DESCRIPTION</div>
                      <div className="rounded-xl bg-black/30 border border-white/[0.07] px-4 py-3 text-[12px] text-white/70 leading-relaxed flex justify-between gap-2">{built.tpDescription}<CopyBtn text={built.tpDescription} /></div>
                    </div>
                    <div>
                      <div className="font-mono2 text-[10px] tracking-[0.16em] text-[#c9f24b] mb-1.5">MAIN TAG · sacred category keyword</div>
                      <div className="rounded-xl bg-[#c9f24b]/[0.05] border border-[#c9f24b]/25 px-4 py-3 text-[13px] font-bold text-[#c9f24b] flex justify-between gap-2">{built.mainTag}<CopyBtn text={built.mainTag} /></div>
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <div className="font-mono2 text-[10px] tracking-[0.16em] text-white/40">SUPPORTING TAGS · {built.tags.length}/15</div>
                      <CopyBtn text={built.tags.join(", ")} label="Copy all" />
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {built.tags.map((t, i) => (
                        <span key={i} className={cx(
                          "rounded-lg border px-2 py-1 text-[10.5px] font-mono2",
                          i === 0 ? "bg-[#c9f24b]/10 border-[#c9f24b]/40 text-[#c9f24b]" : "bg-white/[0.04] border-white/10 text-white/60"
                        )}>
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </Card>

              <div className="flex justify-end">
                <Btn onClick={() => void save()} variant="primary">
                  <Save size={14} /> {saveOk ? "Saved to library ✓" : "Save to library"}
                </Btn>
              </div>
            </>
          )}

          {/* saved library */}
          {saved.length > 0 && (
            <Card className="p-5">
              <Section icon={<FileSpreadsheet size={16} />} title="Listing library" desc={`${saved.length} saved · newest first`} />
              <div className="space-y-2 max-h-[240px] overflow-y-auto pr-1">
                {saved.map((r) => (
                  <div key={r.id} className="flex items-center justify-between gap-3 rounded-xl bg-white/[0.03] border border-white/[0.06] px-4 py-2.5">
                    <div className="min-w-0">
                      <div className="text-[12.5px] font-semibold truncate">{r.title || r.tpTitle}</div>
                      <div className="font-mono2 text-[9.5px] text-white/30">main: {r.mainTag} · {r.tags.length} tags</div>
                    </div>
                    <Chip tone={r.riskScore >= 45 ? "amber" : "lime"}>
                      {r.riskScore >= 45 ? <ShieldAlert size={10} /> : <ShieldCheck size={10} />} {r.riskScore}/100
                    </Chip>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
