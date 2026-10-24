"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Images, Layers, ShieldCheck, Sparkles, Store, WandSparkles, type LucideIcon } from "lucide-react";
import { Card, Chip, PageHead } from "@/components/ui";
import { isConnectedAccount, type ConnectedAccount } from "@/lib/accounts-client";
import type { ViewKey } from "@/components/shell";

interface ActivityItem { kind: string; title: string; detail: string }

export default function Dashboard({ setView }: { setView: (view: ViewKey) => void }) {
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [stats, setStats] = useState({ designs: 0, listings: 0, accounts: 0, ready: 0, jobs: 0 });
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const responses = await Promise.all(["/api/activity", "/api/accounts", "/api/bridge/designs"].map((url) => fetch(url, { cache: "no-store" })));
        if (responses.some((response) => !response.ok)) throw new Error("Local engine unavailable");
        const [events, accounts, history] = await Promise.all(responses.map((response) => response.json()));
        if (disposed) return;
        const connected: ConnectedAccount[] = Array.isArray(accounts.items) ? accounts.items.filter(isConnectedAccount) : [];
        setStats({ designs: Array.isArray(history.items) ? history.items.length : 0, listings: events.listingCount || 0,
          accounts: connected.length, ready: connected.filter((account) => account.status === "ready" && account.authed).length,
          jobs: events.jobCount || 0 });
        setActivity(Array.isArray(events.items) ? events.items : []);
        setUnavailable(false);
      } catch { if (!disposed) setUnavailable(true); }
    };
    void load();
    const refresh = () => { void load(); };
    window.addEventListener("merchflow:designs-changed", refresh);
    return () => { disposed = true; window.removeEventListener("merchflow:designs-changed", refresh); };
  }, []);

  const cards: { icon: LucideIcon; label: string; value: number; detail: string }[] = [
    { icon: Images, label: "Archived designs", value: stats.designs, detail: "Actual disk-backed History records" },
    { icon: Store, label: "Saved listings", value: stats.listings, detail: "Actual local SQLite listings" },
    { icon: Layers, label: "Connected accounts", value: stats.accounts, detail: `${stats.ready} ready · verified browser sign-ins only` },
    { icon: Sparkles, label: "Recorded jobs", value: stats.jobs, detail: "Local job records, no sample activity" },
  ];
  const quick: { key: ViewKey; title: string; detail: string; icon: LucideIcon }[] = [
    { key: "generator", title: "Generate designs", detail: "Style × niche reference fusion", icon: WandSparkles },
    { key: "recolor", title: "Pixel to Pixel", detail: "Exact recreation · one PNG per input", icon: Sparkles },
    { key: "history", title: "Designs Library", detail: "Inspect, select and stream a ZIP", icon: Images },
    { key: "listing", title: "Listing Hub", detail: "List selected designs from History", icon: Store },
    { key: "accounts", title: "Connect Gemini", detail: "Real Google browser login · private SQLite sessions", icon: Layers },
    { key: "trademark", title: "Scan a phrase", detail: "Trademark screening for your artwork", icon: ShieldCheck },
  ];
  return <div>
    <PageHead kicker="COMMAND CENTER" title="Your print production workspace"
      desc="Real connected accounts, verified files and local records. Nothing is pre-populated with sample users or invented account activity."
      right={<Chip tone="violet">4500×5400 @ 300 DPI</Chip>} />
    {unavailable && <p className="mb-4 text-sm text-amber-200">Local data is unavailable. Start run_windows.bat; no sample data is substituted.</p>}
    <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">{cards.map((card) => <Card key={card.label} className="p-5"><card.icon size={20} className="text-[#b9a8ff] mb-4" /><div className="text-3xl font-mono2 text-[#c9f24b]">{card.value}</div><div className="text-sm mt-2">{card.label}</div><div className="text-xs text-white/35 mt-2">{card.detail}</div></Card>)}</div>
    <div className="grid xl:grid-cols-3 gap-4"><div className="xl:col-span-2 grid sm:grid-cols-2 gap-4">{quick.map((item) => <button key={item.key} type="button" onClick={() => setView(item.key)} className="glass rounded-2xl p-5 text-left hover:border-viol-500/40"><item.icon size={22} className="text-[#b9a8ff] mb-4" /><div className="font-semibold flex gap-2 items-center">{item.title}<ArrowRight size={14} /></div><div className="text-xs text-white/40 mt-2">{item.detail}</div></button>)}</div>
      <Card className="p-5"><h2 className="text-xs tracking-widest text-white/50 mb-4">ACTUAL LOCAL ACTIVITY</h2>{activity.length ? activity.map((item, index) => <div key={index} className="py-3 border-b border-white/5"><div className="text-sm">{item.title}</div><div className="text-xs text-white/35 mt-1">{item.detail}</div></div>) : <p className="text-sm text-white/35">No activity recorded yet. Connect an account to begin.</p>}</Card>
    </div>
  </div>;
}
