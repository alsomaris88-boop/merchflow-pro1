"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BadgeDollarSign,
  Crown,
  Download,
  Flame,
  Image as ImageIcon,
  Loader2,
  Radar,
  Search,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { Btn, Card, Chip, CopyBtn, Input, PageHead, Ring, cx } from "@/components/ui";

interface SuggestionItem { keyword: string; demand: number; competition: number; opportunity: number; fromCompletion: boolean }
interface MoverItem { keyword: string; bsrNow: number; bsrPrev: number; delta: number; opportunity: number; isMerch: boolean }
interface EtsyItem { title: string; price: number; estSales: number; rating: number; bestseller: boolean; tags: string[] }
interface PinBoard { name: string; style: string; trendScore: number; weeklyPins: number; palette: string[] }

type Tab = "keyword" | "movers" | "etsy" | "pinterest";

const oppTone = (v: number) => (v >= 75 ? "text-[#c9f24b]" : v >= 50 ? "text-amber-300" : "text-white/45");
const oppChip = (v: number): "lime" | "amber" | "neutral" => (v >= 75 ? "lime" : v >= 50 ? "amber" : "neutral");

export default function TrendRadar() {
  const [tab, setTab] = useState<Tab>("movers");
  const [q, setQ] = useState("pickleball");
  const [src, setSrc] = useState("");
  const [suggest, setSuggest] = useState<SuggestionItem[]>([]);
  const [movers, setMovers] = useState<MoverItem[]>([]);
  const [etsy, setEtsy] = useState<EtsyItem[]>([]);
  const [pins, setPins] = useState<PinBoard[]>([]);
  const [busy, setBusy] = useState(false);

  const loadSuggest = useCallback(async (query: string) => {
    if (!query.trim() || busy) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/trends?view=suggest&q=${encodeURIComponent(query)}`);
      const d = await r.json();
      setSuggest(d.items ?? []);
      setSrc(d.source ?? "");
    } catch { /* noop */ }
    setBusy(false);
  }, [busy]);

  useEffect(() => {
    fetch("/api/trends?view=movers").then((r) => r.json()).then((d) => setMovers(d.items ?? [])).catch(() => undefined);
    fetch("/api/trends?view=etsy").then((r) => r.json()).then((d) => setEtsy(d.items ?? [])).catch(() => undefined);
    fetch("/api/trends?view=pinterest").then((r) => r.json()).then((d) => setPins(d.items ?? [])).catch(() => undefined);
  }, []);

  const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "movers", label: "BSR Movers", icon: <Flame size={13} /> },
    { key: "keyword", label: "Keyword Opportunity", icon: <Search size={13} /> },
    { key: "etsy", label: "Etsy Bestsellers", icon: <BadgeDollarSign size={13} /> },
    { key: "pinterest", label: "Pinterest Style Radar", icon: <ImageIcon size={13} /> },
  ];

  return (
    <div>
      <PageHead
        kicker="MODULE 06 · SECRET MERCH TREND HUNTER"
        title="Demand before design"
        desc="Browse-node 21393915011 surveillance with byte-identical Merch fabric markers, 24h BSR velocity tracking, live Amazon completion demand, Etsy bestseller tag extraction and Pinterest aesthetic radar — a full Merch Dominator / EverBee replacement."
        right={<Chip tone="lime"><Radar size={11} /> 4 recon feeds</Chip>}
      />

      {/* tabs */}
      <div className="flex gap-2 mb-5 flex-wrap">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={cx(
              "flex items-center gap-2 rounded-xl px-4 py-2.5 text-[12.5px] font-semibold border transition-all",
              tab === t.key
                ? "bg-viol-500/20 border-viol-500/45 text-white shadow-[0_0_24px_-6px_rgba(124,90,255,0.6)]"
                : "bg-white/[0.03] border-white/10 text-white/45 hover:text-white/80"
            )}>
            {t.icon}{t.label}
          </button>
        ))}
      </div>

      {/* ------------------------------- BSR movers ------------------------------- */}
      {tab === "movers" && (
        <Card className="p-5">
          <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
            <div className="font-mono2 text-[10px] tracking-[0.22em] text-white/40">
              AMAZON MERCH NICHE RADAR · NODE 21393915011 · pure-MBA filtered (fabric marker: “Solid colors: 100% Cotton…”)
            </div>
            <Chip tone="neutral">24h velocity window</Chip>
          </div>
          <div className="space-y-2">
            {movers.map((m, i) => (
              <div key={m.keyword} className="grid grid-cols-[28px_1fr_auto] md:grid-cols-[28px_1fr_120px_120px_90px_110px] items-center gap-3 rounded-xl bg-white/[0.03] border border-white/[0.06] px-4 py-3 hover:border-viol-500/30 transition-colors">
                <span className={cx("font-mono2 text-[13px] font-semibold", i < 3 ? "text-[#c9f24b]" : "text-white/30")}>#{i + 1}</span>
                <div className="min-w-0">
                  <div className="text-[13px] font-semibold truncate">{m.keyword}</div>
                  <div className="md:hidden font-mono2 text-[9.5px] text-white/35 mt-0.5">
                    BSR {m.bsrNow.toLocaleString()} · −{m.delta}% · opp {m.opportunity}
                  </div>
                </div>
                <div className="hidden md:block font-mono2 text-[11px] text-white/50">BSR {m.bsrNow.toLocaleString()}</div>
                <div className="hidden md:flex items-center gap-1.5 font-mono2 text-[11px] text-[#c9f24b]">
                  <TrendingDown size={12} /> −{m.delta}%
                </div>
                <div className="hidden md:block">
                  {m.isMerch ? <Chip tone="violet">MBA ✓</Chip> : <Chip tone="neutral">3P brand</Chip>}
                </div>
                <div className="hidden md:flex items-center gap-2">
                  <div className="h-1 w-14 rounded-full bg-white/[0.08] overflow-hidden">
                    <div className="h-full rounded-full bg-gradient-to-r from-viol-500 to-[#c9f24b]" style={{ width: `${m.opportunity}%` }} />
                  </div>
                  <span className={cx("font-mono2 text-[11px] font-semibold", oppTone(m.opportunity))}>{m.opportunity}</span>
                </div>
              </div>
            ))}
            {!movers.length && <div className="py-10 text-center text-white/30 text-[13px]"><Loader2 className="animate-spin mx-auto" size={18} /></div>}
          </div>
        </Card>
      )}

      {/* ----------------------------- keyword opportunity ----------------------------- */}
      {tab === "keyword" && (
        <Card className="p-5">
          <div className="flex gap-3 mb-5">
            <div className="relative flex-1">
              <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-white/30" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} className="pl-9"
                placeholder="seed keyword — e.g. pickleball, welder, axolotl"
                onKeyDown={(e) => { if (e.key === "Enter") void loadSuggest(q); }} />
            </div>
            <Btn variant="lime" onClick={() => void loadSuggest(q)} disabled={busy}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />} Expand
            </Btn>
          </div>
          {src && (
            <div className="mb-4 flex items-center gap-2">
              <Chip tone={src.startsWith("live") ? "lime" : "violet"}>{src}</Chip>
              <span className="font-mono2 text-[10px] text-white/30">completion.amazon.com · A-Z expansion · scored: demand × (1−competition)</span>
            </div>
          )}
          <div className="grid md:grid-cols-2 gap-2.5">
            {suggest.map((s) => (
              <div key={s.keyword} className="rounded-xl bg-white/[0.03] border border-white/[0.06] px-4 py-3">
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="text-[13px] font-semibold truncate">{s.keyword}</div>
                  <Chip tone={oppChip(s.opportunity)}>OPP {s.opportunity}</Chip>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  {[["Demand", s.demand, "#c9f24b"], ["Competition", s.competition, "#fb7185"]].map(([k, v, c]) => (
                    <div key={k as string}>
                      <div className="flex justify-between font-mono2 text-[9px] text-white/35 mb-1"><span>{k}</span><span>{v}</span></div>
                      <div className="h-1 rounded-full bg-white/[0.07] overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${v}%`, background: c as string }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {!suggest.length && !busy && (
              <div className="md:col-span-2 py-12 text-center text-white/30 text-[13px]">
                Run an expansion to fetch live customer search suggestions with opportunity scoring.
              </div>
            )}
          </div>
        </Card>
      )}

      {/* -------------------------------- etsy -------------------------------- */}
      {tab === "etsy" && (
        <div className="grid md:grid-cols-2 gap-4">
          {etsy.map((e) => (
            <Card key={e.title} className="p-5">
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="text-[13.5px] font-bold tracking-tight leading-snug">{e.title}</div>
                {e.bestseller && <Chip tone="amber" className="shrink-0"><Crown size={10} /> BESTSELLER</Chip>}
              </div>
              <div className="flex items-center gap-4 mb-4 font-mono2 text-[11px] text-white/50">
                <span className="text-[#c9f24b] font-semibold">${e.price.toFixed(2)}</span>
                <span className="flex items-center gap-1"><TrendingUp size={11} /> {e.estSales.toLocaleString()} sales</span>
                <span>★ {e.rating}</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {e.tags.slice(0, 13).map((t, i) => (
                  <span key={i} className="rounded-md bg-white/[0.04] border border-white/10 px-1.5 py-[3px] text-[9.5px] font-mono2 text-white/50">{t}</span>
                ))}
              </div>
              <div className="mt-3.5 flex justify-between items-center">
                <span className="font-mono2 text-[9.5px] text-white/25">exact 13-tag extraction</span>
                <CopyBtn text={e.tags.join(", ")} label="Steal tags" />
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* ------------------------------ pinterest ------------------------------ */}
      {tab === "pinterest" && (
        <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {pins.map((p) => (
            <Card key={p.name} className="p-5 group hover:border-viol-500/35 transition-colors">
              <div className="h-16 rounded-xl mb-4 relative overflow-hidden" style={{ background: `linear-gradient(120deg, ${p.palette.join(", ")})` }}>
                <div className="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent" />
                <div className="absolute bottom-1.5 left-2.5 flex gap-1">
                  {p.palette.map((c) => <span key={c} className="h-3.5 w-3.5 rounded-md border border-white/30" style={{ background: c }} />)}
                </div>
              </div>
              <div className="text-[13px] font-bold tracking-tight">{p.name}</div>
              <div className="text-[10.5px] text-white/40 mt-1 leading-relaxed min-h-[30px]">{p.style}</div>
              <div className="flex items-center justify-between mt-3 mb-3">
                <Ring value={p.trendScore / 100} size={52} stroke={5} label={String(p.trendScore)} sub="TREND" color={p.trendScore >= 88 ? "#c9f24b" : "#b9a8ff"} />
                <div className="text-right">
                  <div className="font-mono2 text-[13px] font-semibold text-white/80">{(p.weeklyPins / 1000).toFixed(1)}k</div>
                  <div className="text-[9.5px] font-mono2 text-white/30">pins / week</div>
                </div>
              </div>
              <CopyBtn text={`${p.name} — ${p.style} — palette: ${p.palette.join(", ")}`} label="Send to Dropzone A" />
            </Card>
          ))}
        </div>
      )}

      <div className="mt-5 flex items-center gap-2 font-mono2 text-[10px] text-white/25">
        <Download size={11} /> All recon requests originate client-side from your residential IP — zero bot-detection footprint.
      </div>
    </div>
  );
}
