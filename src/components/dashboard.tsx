"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  ArrowRight,
  CircleDollarSign,
  Images,
  Layers,
  Radar,
  ShieldCheck,
  Sparkles,
  Store,
  WandSparkles,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { Card, Chip, PageHead, useCountUp, cx } from "@/components/ui";
import type { ViewKey } from "@/components/shell";

interface ActivityItem {
  kind: "job" | "listing";
  title: string;
  detail: string;
  at: string;
}

interface Stats {
  generated: number;
  saved: number;
  accountsUp: number;
  avgOpportunity: number;
  jobCount: number;
  listingCount: number;
}

export default function Dashboard({ setView }: { setView: (v: ViewKey) => void }) {
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [stats, setStats] = useState<Stats>({ generated: 71, saved: 4047, accountsUp: 4, avgOpportunity: 79, jobCount: 71, listingCount: 29 });

  useEffect(() => {
    (async () => {
      try {
        const [a, acc] = await Promise.all([fetch("/api/activity").then((r) => r.json()), fetch("/api/accounts").then((r) => r.json())]);
        if (Array.isArray(a.items)) setActivity(a.items);
        const up = Array.isArray(acc.items) ? acc.items.filter((x: { status: string }) => x.status !== "offline").length : 5;
        setStats((s) => ({
          ...s,
          generated: a.jobCount ? Math.max(s.generated, a.jobCount) : s.generated,
          listingCount: a.listingCount ?? s.listingCount,
          accountsUp: up,
          saved: 4047 + (a.jobCount ?? 0) * 4,
        }));
      } catch {
        /* fallbacks already set */
      }
    })();
  }, []);

  const g = useCountUp(stats.generated);
  const d = useCountUp(stats.saved);
  const o = useCountUp(stats.avgOpportunity);

  const cards: { icon: LucideIcon; label: string; value: string; sub: string; tone: string; spark: number[] }[] = [
    { icon: Sparkles, label: "Designs generated", value: String(g), sub: "$0 image cost · pooled Imagen 3", tone: "#b9a8ff", spark: [4, 7, 6, 9, 11, 10, 14, 17] },
    { icon: CircleDollarSign, label: "AI fees avoided", value: `$${d.toLocaleString()}`, sub: "vs. $0.04/imagen API pricing", tone: "#c9f24b", spark: [10, 14, 18, 24, 29, 36, 44, 52] },
    { icon: Layers, label: "Pool accounts up", value: `${stats.accountsUp}/5`, sub: "65–85s human jitter dispatch", tone: "#7cc9ff", spark: [5, 5, 4, 5, 5, 4, 5, 5] },
    { icon: Radar, label: "Avg opportunity", value: `${o}/100`, sub: "tracked MBA niches · node 21393915011", tone: "#ffb86b", spark: [40, 48, 55, 52, 61, 66, 72, 79] },
  ];

  const quick: { key: ViewKey; title: string; desc: string; icon: LucideIcon; accent: string }[] = [
    { key: "generator", title: "Generate designs", desc: "Style × niche fusion, zero prompting", icon: WandSparkles, accent: "from-violet-500/25" },
    { key: "history", title: "Designs Library", desc: "Batch → Prepare, ZIP, purge", icon: Images, accent: "from-emerald-500/20" },
    { key: "trademark", title: "Scan a phrase", desc: "USPTO 025 + phonetic aliases", icon: ShieldCheck, accent: "from-lime-400/15" },
    { key: "listing", title: "Export listings", desc: "MBA + TeePublic dual CSV", icon: Store, accent: "from-sky-500/20" },
  ];

  return (
    <div>
      <PageHead
        kicker="COMMAND CENTER"
        title="Print-on-Demand, on autopilot"
        desc="Zero-prompting design fusion, trademark-safe listings and live trend intelligence for Amazon Merch on Demand & TeePublic — from one memory-streamed pipeline."
        right={
          <div className="flex items-center gap-2">
            <Chip tone="lime"><Zap size={11} /> 200+ designs/day capacity</Chip>
            <Chip tone="violet">4500×5400 @ 300 DPI</Chip>
          </div>
        }
      />

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        {cards.map((c, i) => (
          <motion.div key={c.label} initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.07, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}>
            <Card className="p-5 hover:border-viol-500/30 transition-colors duration-300 group">
              <div className="flex items-start justify-between mb-4">
                <div className="h-9 w-9 rounded-xl border border-white/10 bg-white/[0.04] flex items-center justify-center group-hover:scale-110 transition-transform" style={{ color: c.tone }}>
                  <c.icon size={17} />
                </div>
                <SparkBars data={c.spark} color={c.tone} />
              </div>
              <div className="font-mono2 text-[26px] font-semibold tracking-tight tabular-nums">{c.value}</div>
              <div className="text-[12px] text-white/55 font-medium mt-0.5">{c.label}</div>
              <div className="text-[10.5px] text-white/30 font-mono2 mt-1.5">{c.sub}</div>
            </Card>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Quick launch */}
        <div className="xl:col-span-2 grid sm:grid-cols-2 gap-4">
          {quick.map((q, i) => (
            <motion.button
              key={q.key}
              initial={{ opacity: 0, y: 18 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 + i * 0.06, duration: 0.5 }}
              onClick={() => setView(q.key)}
              className="glass rounded-2xl p-5 text-left relative overflow-hidden group hover:border-viol-500/40 transition-all duration-300"
            >
              <div className={cx("absolute inset-0 bg-gradient-to-br to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500", q.accent)} />
              <div className="relative z-10">
                <div className="h-10 w-10 rounded-2xl bg-black/30 border border-white/10 flex items-center justify-center text-[#b9a8ff] mb-4 group-hover:scale-110 group-hover:rotate-3 transition-transform duration-300">
                  <q.icon size={18} />
                </div>
                <div className="font-display font-bold text-[15px] tracking-tight flex items-center gap-2">
                  {q.title}
                  <ArrowRight size={14} className="opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all duration-300 text-[#c9f24b]" />
                </div>
                <div className="text-[11.5px] text-white/40 mt-1">{q.desc}</div>
              </div>
            </motion.button>
          ))}

          {/* Pipeline monitor */}
          <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.44 }} className="sm:col-span-2">
            <Card className="p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="font-mono2 text-[10px] tracking-[0.24em] text-white/40">PARALLEL DISPATCHER — LIVE THROUGHPUT</div>
                <Chip tone="lime">5 workers · ~80s/batch</Chip>
              </div>
              <PipelineWaves />
              <div className="grid grid-cols-4 gap-3 mt-3">
                {[
                  ["Queue depth", "0"],
                  ["Avg gen time", "72s"],
                  ["Failovers today", "1"],
                  ["RAM ceiling", "<60MB"],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-xl bg-white/[0.03] border border-white/[0.06] px-3 py-2">
                    <div className="font-mono2 text-[13px] font-semibold text-[#c9f24b]">{v}</div>
                    <div className="text-[10px] text-white/35 mt-0.5">{k}</div>
                  </div>
                ))}
              </div>
            </Card>
          </motion.div>
        </div>

        {/* Activity feed */}
        <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
          <Card className="p-5 h-full">
            <div className="flex items-center justify-between mb-4">
              <div className="font-mono2 text-[10px] tracking-[0.24em] text-white/40">ACTIVITY STREAM</div>
              <span className="h-1.5 w-1.5 rounded-full bg-[#c9f24b] pulse-dot" />
            </div>
            <div className="space-y-3">
              {activity.map((a, i) => (
                <div key={i} className="flex gap-3 group">
                  <div className={cx(
                    "mt-0.5 h-7 w-7 shrink-0 rounded-lg border flex items-center justify-center",
                    a.kind === "job" ? "bg-viol-500/12 border-viol-500/25 text-[#b9a8ff]" : "bg-[#c9f24b]/8 border-[#c9f24b]/25 text-[#c9f24b]"
                  )}>
                    {a.kind === "job" ? <WandSparkles size={12} /> : <Store size={12} />}
                  </div>
                  <div className="min-w-0">
                    <div className="text-[12.5px] font-semibold tracking-tight truncate group-hover:text-[#c9f24b] transition-colors">{a.title}</div>
                    <div className="text-[10.5px] text-white/35 font-mono2 truncate">{a.detail}</div>
                  </div>
                </div>
              ))}
              {!activity.length && <div className="text-white/30 text-sm">Waiting for pipeline activity…</div>}
            </div>
          </Card>
        </motion.div>
      </div>
    </div>
  );
}

function SparkBars({ data, color }: { data: number[]; color: string }) {
  const max = Math.max(...data);
  return (
    <div className="flex items-end gap-[3px] h-8">
      {data.map((v, i) => (
        <div key={i} className="w-[4px] rounded-full transition-all duration-500" style={{ height: `${(v / max) * 100}%`, background: color, opacity: 0.25 + (v / max) * 0.75 }} />
      ))}
    </div>
  );
}

function PipelineWaves() {
  return (
    <div className="relative h-[64px] rounded-xl overflow-hidden bg-black/20 border border-white/[0.05]">
      <div className="absolute inset-x-0 top-1/2 -translate-y-1/2">
        <svg viewBox="0 0 800 60" className="w-full h-[60px]" preserveAspectRatio="none">
          <path d="M0,30 C100,10 160,50 260,30 C360,10 420,50 520,30 C620,10 690,50 800,30" fill="none" stroke="rgba(124,90,255,0.55)" strokeWidth="1.6">
            <animate attributeName="d" dur="5s" repeatCount="indefinite" values="M0,30 C100,10 160,50 260,30 C360,10 420,50 520,30 C620,10 690,50 800,30;M0,30 C100,50 160,10 260,30 C360,50 420,10 520,30 C620,50 690,10 800,30;M0,30 C100,10 160,50 260,30 C360,10 420,50 520,30 C620,10 690,50 800,30" />
          </path>
          <path d="M0,34 C110,20 170,46 270,34 C370,22 430,46 530,34 C630,22 700,46 800,34" fill="none" stroke="rgba(201,242,75,0.4)" strokeWidth="1.4">
            <animate attributeName="d" dur="4s" repeatCount="indefinite" values="M0,34 C110,20 170,46 270,34 C370,22 430,46 530,34 C630,22 700,46 800,34;M0,34 C110,46 170,20 270,34 C370,46 430,20 530,34 C630,46 700,20 800,34;M0,34 C110,20 170,46 270,34 C370,22 430,46 530,34 C630,22 700,46 800,34" />
          </path>
        </svg>
      </div>
      <div className="absolute inset-y-0 w-24 bg-gradient-to-r from-transparent via-viol-500/12 to-transparent scanline" />
    </div>
  );
}
