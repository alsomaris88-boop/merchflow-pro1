"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  Boxes,
  Cpu,
  FlaskConical,
  Images,
  KeyRound,
  Layers,
  LayoutDashboard,
  Palette,
  PlugZap,
  Radar,
  ShieldCheck,
  Shirt,
  Store,
  WandSparkles,
} from "lucide-react";
import { cx } from "./ui";
import { probeBridge } from "@/lib/bridge";

export type ViewKey =
  | "dashboard"
  | "generator"
  | "history"
  | "recolor"
  | "prepare"
  | "trademark"
  | "listing"
  | "trends"
  | "mockup"
  | "accounts"
  | "license";

export const NAV: { key: ViewKey; label: string; icon: React.ReactNode; group: string }[] = [
  { key: "dashboard", label: "Command Center", icon: <LayoutDashboard size={17} />, group: "CORE" },
  { key: "generator", label: "AI Design Generator", icon: <WandSparkles size={17} />, group: "CREATE" },
  { key: "history", label: "Designs History", icon: <Images size={17} />, group: "CREATE" },
  { key: "recolor", label: "Pixel to Pixel", icon: <Palette size={17} />, group: "CREATE" },
  { key: "prepare", label: "Prepare Designs Lab", icon: <FlaskConical size={17} />, group: "CREATE" },
  { key: "trademark", label: "Trademark Shield", icon: <ShieldCheck size={17} />, group: "PROTECT" },
  { key: "listing", label: "Listing & Export Hub", icon: <Store size={17} />, group: "PROTECT" },
  { key: "license", label: "License & Sync", icon: <KeyRound size={17} />, group: "PROTECT" },
  { key: "trends", label: "Trend Hunter", icon: <Radar size={17} />, group: "RESEARCH" },
  { key: "mockup", label: "Mockup Studio", icon: <Shirt size={17} />, group: "RESEARCH" },
  { key: "accounts", label: "Account Pool", icon: <Layers size={17} />, group: "SYSTEM" },
];

const groups = ["CORE", "CREATE", "PROTECT", "RESEARCH", "SYSTEM"];

function useClock() {
  const [t, setT] = useState("");
  useEffect(() => {
    const f = () =>
      setT(
        new Date().toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
      );
    f();
    const i = setInterval(f, 1000);
    return () => clearInterval(i);
  }, []);
  return t;
}

export function Shell({
  view,
  setView,
  children,
}: {
  view: ViewKey;
  setView: (v: ViewKey) => void;
  children: React.ReactNode;
}) {
  const clock = useClock();
  // Connection state is probed; memory and online status are never invented.
  const [engineOn, setEngineOn] = useState(false);

  useEffect(() => {
    const probe = () => probeBridge().then((s) => setEngineOn(!!s.connected)).catch(() => setEngineOn(false));
    void probe();
    const p = setInterval(probe, 15000);
    return () => clearInterval(p);
  }, []);

  return (
    <div className="min-h-screen flex relative">
      {/* ambient backdrop */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute inset-0 bg-blueprint" />
        <div className="absolute -top-40 left-1/4 h-[480px] w-[680px] rounded-full bg-viol-500/14 blur-[140px]" />
        <div className="absolute bottom-0 right-0 h-[380px] w-[520px] rounded-full bg-[#c9f24b]/[0.05] blur-[130px]" />
      </div>

      {/* Sidebar */}
      <aside className="w-[248px] shrink-0 border-r border-white/[0.06] bg-ink-950/70 backdrop-blur-xl relative z-10 flex flex-col h-screen sticky top-0">
        <div className="px-5 pt-6 pb-5">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-2xl bg-gradient-to-br from-viol-500 to-[#4a2fd6] flex items-center justify-center shadow-[0_0_30px_rgba(124,90,255,0.5)]">
              <Boxes size={20} className="text-white" />
            </div>
            <div>
              <div className="font-display font-bold text-[17px] tracking-tight leading-none">
                MerchFlow<span className="text-[#c9f24b]"> Pro</span>
              </div>
              <div className="font-mono2 text-[9px] tracking-[0.28em] text-white/35 mt-1">POD AUTOMATION SUITE</div>
            </div>
          </div>
        </div>
        <div className="glow-line mx-5" />

        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-4">
          {groups.map((g) => (
            <div key={g}>
              <div className="px-2.5 mb-1.5 font-mono2 text-[9px] tracking-[0.3em] text-white/25">{g}</div>
              <div className="space-y-0.5">
                {NAV.filter((n) => n.group === g).map((n) => {
                  const active = view === n.key;
                  return (
                    <button
                      key={n.key}
                      onClick={() => setView(n.key)}
                      className={cx(
                        "w-full flex items-center gap-3 rounded-xl px-3 py-[9px] text-[13px] font-medium transition-all duration-200 relative group",
                        active ? "text-white" : "text-white/45 hover:text-white/85 hover:bg-white/[0.04]"
                      )}
                    >
                      {active && (
                        <span className="absolute inset-0 rounded-xl bg-gradient-to-r from-viol-500/25 to-viol-500/5 border border-viol-500/30" />
                      )}
                      {active && (
                        <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-full bg-[#c9f24b] shadow-[0_0_10px_rgba(201,242,75,0.9)]" />
                      )}
                      <span className={cx("relative z-10", active ? "text-[#b9a8ff]" : "")}>{n.icon}</span>
                      <span className="relative z-10 tracking-tight">{n.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="p-3">
          <div className="glass-deep rounded-2xl p-3.5">
            <div className="flex items-center justify-between mb-2">
              <span className="font-mono2 text-[9px] tracking-[0.22em] text-white/35 flex items-center gap-1.5">
                <Cpu size={11} className="text-[#b9a8ff]" /> PRINT PIPELINE
              </span>
              <span className="font-mono2 text-[10px] text-[#c9f24b]">PNG</span>
            </div>
            <div className="mt-2 text-[10px] text-white/30 leading-tight">Sequential processing · verified files · streaming ZIP</div>
          </div>
        </div>
      </aside>

      {/* Main column */}
      <div className="flex-1 min-w-0 relative z-10 flex flex-col">
        {/* Topbar */}
        <header className="h-[60px] border-b border-white/[0.06] bg-ink-950/60 backdrop-blur-xl flex items-center justify-between px-6 sticky top-0 z-20">
          <div className="flex items-center gap-3">
            <div className="glass rounded-xl px-3.5 py-2 flex items-center gap-2.5">
              <span className={cx("h-[7px] w-[7px] rounded-full", engineOn ? "bg-[#c9f24b]" : "bg-white/25")} />
              <span className="text-[12px] font-semibold text-white/80 tracking-tight">{engineOn ? "Engine connected" : "Engine offline"}</span>
              <span className="font-mono2 text-[10px] text-white/30">disk-backed</span>
            </div>
            <div className="hidden md:flex glass rounded-xl px-3.5 py-2 items-center gap-2">
              <Activity size={12} className="text-[#b9a8ff]" />
              <span className="font-mono2 text-[11px] text-white/60">sequential raster queue</span>
            </div>
            <div
              className={cx(
                "hidden lg:flex glass rounded-xl px-3.5 py-2 items-center gap-2 transition-all",
                engineOn && "border-[#c9f24b]/40"
              )}
              title={engineOn ? "Local Python engine connected at 127.0.0.1:8000" : "Run run_windows.bat to connect the local engine"}
            >
              <PlugZap size={12} className={engineOn ? "text-[#c9f24b]" : "text-white/30"} />
              <span className={cx("font-mono2 text-[11px]", engineOn ? "text-[#c9f24b]" : "text-white/35")}>
                {engineOn ? "ENGINE: LOCAL" : "ENGINE: OFFLINE"}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden sm:block font-mono2 text-[11px] text-white/35 tabular-nums">{clock} LOCAL</div>
            <div className="h-8 w-8 rounded-xl bg-gradient-to-br from-[#c9f24b]/25 to-viol-500/25 border border-white/10 flex items-center justify-center font-display font-bold text-[12px]">
              MF
            </div>
          </div>
        </header>

        <main className="flex-1 px-6 lg:px-8 py-7 max-w-[1400px] w-full mx-auto">{children}</main>

        <footer className="px-8 pb-6 pt-2 flex items-center justify-between">
          <div className="font-mono2 text-[10px] text-white/25 tracking-[0.2em]">
            MERCHFLOW PRO · MBA + TEEPUBLIC DUAL EXPORT
          </div>
          <div className="font-mono2 text-[10px] text-white/25">4500×5400 · 300 DPI · sRGB</div>
        </footer>
      </div>
    </div>
  );
}
