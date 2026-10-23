"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Globe,
  Heart,
  Hourglass,
  Layers,
  Loader2,
  Plus,
  PlugZap,
  ShieldCheck,
  Sparkles,
  Timer,
  Trash2,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import { Btn, Card, Chip, PageHead, Ring, cx } from "@/components/ui";
import { probeBridge, type BridgeStatus } from "@/lib/bridge";

interface Account {
  id: number;
  label: string;
  email: string;
  provider: string;
  status: string;
  dailyUsed: number;
  dailyCap: number;
  weeklyUsed?: number;
  weeklyCap?: number;
  totalGenerated?: number;
  windowUsage?: number;
  windowCap?: number;
  windowSeconds?: number;
  cooldownUntil?: number | null;
  cooldownRemaining?: number;
  isPro?: boolean;
}

interface WorkerCard {
  id: number;
  label: string;
  email?: string;
  provider?: string;
  status?: string;
  authed?: boolean;
  hasCookies?: boolean;
  headless?: boolean;
  isPro?: boolean;
  dailyUsed?: number;
  dailyCap?: number;
  weeklyUsed?: number;
  weeklyCap?: number;
  totalGenerated?: number;
  windowUsage?: number;
  windowCap?: number;
  windowSeconds?: number;
  cooldownUntil?: number | null;
  cooldownRemaining?: number;
}

interface FlowState {
  state: "pending" | "authenticated" | "closed" | "failed" | "unknown";
  email?: string | null;
  label?: string;
  provider?: string;
  error?: string | null;
  slot?: number;
}

const PROVIDERS = [
  { key: "brave", label: "Brave" },
  { key: "chrome", label: "Google Chrome" },
] as const;

const CLOSED_EARLY = "The sign-in window was closed before sign-in finished.";

const providerLabel = (p?: string) =>
  p === "brave" ? "Brave" : p === "edge" ? "Microsoft Edge" : "Google Chrome";

export default function Accounts() {
  const [items, setItems] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [bridge, setBridge] = useState<BridgeStatus>({ connected: false });
  const [workers, setWorkers] = useState<Map<number, WorkerCard>>(new Map());
  const [tick, setTick] = useState(0);

  // login modal
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<"brave" | "chrome">("brave");
  const [flowId, setFlowId] = useState<string | null>(null);
  const [flow, setFlow] = useState<FlowState | null>(null);
  const [starting, setStarting] = useState(false);
  const [fatal, setFatal] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const seenRef = useRef<Set<number>>(new Set());

  /* ------------------------------- data loading ------------------------------- */
  const loadLocal = useCallback(async () => {
    try {
      const r = await fetch("/api/accounts");
      const d = await r.json();
      if (Array.isArray(d.items) && d.items.length) setItems(d.items);
    } catch {
      /* keep */
    }
    setLoading(false);
  }, []);

  const loadWorkers = useCallback(async () => {
    try {
      const r = await fetch("/api/bridge/accounts", { cache: "no-store" });
      if (!r.ok) return;
      const d = (await r.json()) as { profiles?: WorkerCard[] };
      if (Array.isArray(d.profiles)) setWorkers(new Map(d.profiles.map((p) => [p.id, p])));
    } catch {
      /* engine offline */
    }
  }, []);

  useEffect(() => {
    void loadLocal();
    const probe = () => probeBridge().then(setBridge).catch(() => setBridge({ connected: false }));
    void probe().then(loadWorkers);
    const poll = setInterval(() => {
      void loadLocal();
      void probe().then(loadWorkers);
    }, 12000);
    const beat = setInterval(() => setTick((t) => t + 1), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(beat);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadLocal, loadWorkers]);

  /** Merge engine cards into the local rows so new sign-ins appear instantly. */
  useEffect(() => {
    if (!workers.size) return;
    setItems((prev) => {
      const next = [...prev];
      for (const [id, w] of workers) {
        if (!w.email && !w.hasCookies) continue;
        const i = next.findIndex((n) => n.id === id);
        const merged: Account = {
          id,
          label: w.label ?? `Profile ${String(id).padStart(2, "0")}`,
          email: w.email || "",
          provider: w.provider || "chrome",
          status: w.authed ? "ready" : (w.status ?? "auth-required"),
          dailyUsed: w.dailyUsed ?? 0,
          dailyCap: w.dailyCap ?? 200,
          weeklyUsed: w.weeklyUsed ?? 0,
          weeklyCap: w.weeklyCap ?? 1400,
          totalGenerated: w.totalGenerated ?? 0,
          windowUsage: w.windowUsage ?? 0,
          windowCap: w.windowCap ?? 60,
          windowSeconds: w.windowSeconds ?? 5 * 3600,
          cooldownUntil: w.cooldownUntil ?? null,
          cooldownRemaining: w.cooldownRemaining ?? 0,
          isPro: w.isPro,
        };
        if (i === -1) next.push(merged);
        else next[i] = { ...next[i], ...merged };
      }
      return next;
    });
  }, [workers]);

  /* ---------------------------- login flow control ---------------------------- */
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const beginLogin = useCallback(async () => {
    if (starting || flowId) return;
    setStarting(true);
    setFatal(null);
    setFlow(null);
    try {
      const r = await fetch("/api/bridge/login/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      });
      const d = (await r.json()) as {
        ok?: boolean;
        flowId?: string;
        state?: FlowState["state"];
        reason?: string;
        error?: string;
      };
      if (!d.ok || !d.flowId) {
        setFatal(d.reason || d.error || "Could not open the sign-in window.");
        setStarting(false);
        return;
      }
      setFlowId(d.flowId);
      setFlow({ state: "pending" });
      // background sensor poll — 1.5s cadence, matches the engine
      pollRef.current = setInterval(async () => {
        try {
          const s = await fetch(`/api/bridge/login/flow/${d.flowId}`, { cache: "no-store" });
          if (!s.ok) return;
          const st = (await s.json()) as FlowState & { profiles?: WorkerCard[] };
          setFlow(st);
          if (st.state === "authenticated") {
            stopPolling();
            void loadWorkers();
            if (st.slot) seenRef.current.add(st.slot);
            setTimeout(() => {
              setFlowId(null);
              setFlow(null);
            }, 2600);
          } else if (st.state === "closed" || st.state === "failed") {
            stopPolling();
          }
        } catch {
          /* keep polling */
        }
      }, 1500);
    } catch {
      setFatal("Could not reach the local engine. Is run_windows.bat running?");
    }
    setStarting(false);
  }, [flowId, loadWorkers, provider, starting, stopPolling]);

  const dismissLogin = useCallback(() => {
    if (flowId && flow?.state === "pending") {
      void fetch(`/api/bridge/login/flow/${flowId}`, { method: "POST" }).catch(() => undefined);
    }
    stopPolling();
    setFlowId(null);
    setFlow(null);
    setFatal(null);
    setOpen(false);
  }, [flow?.state, flowId, stopPolling]);

  const forget = useCallback(
    async (id: number) => {
      setWorkers((m) => {
        const n = new Map(m);
        n.delete(id);
        return n;
      });
      setItems((x) => x.filter((y) => y.id !== id));
    },
    []
  );

  /* --------------------------------- derived --------------------------------- */
  const authed = Array.from(workers.values()).filter((w) => w.authed).length;
  const readyCount = authed || items.filter((i) => i.status === "ready" || i.status === "active").length;

  const fmtLeft = (secs: number) => {
    if (secs <= 0) return "0m";
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  const stateOf = (a: Account) => {
    const left = a.status === "cooldown" && a.cooldownUntil
      ? Math.max(0, Math.round(a.cooldownUntil - Date.now() / 1000))
      : a.cooldownRemaining ?? 0;
    if (a.status === "cooldown") return { tone: "amber" as const, dot: "bg-amber-400", text: left > 0 ? `Cooldown · ${fmtLeft(left)}` : "Cooldown" };
    if (a.status === "active") return { tone: "violet" as const, dot: "bg-viol-500 pulse-dot", text: "Active" };
    if ((a.isPro || (a.email && a.status === "ready")) && a.status === "ready") return { tone: "lime" as const, dot: "bg-[#c9f24b] pulse-dot", text: "Ready · Pro" };
    if (a.status === "ready") return { tone: "lime" as const, dot: "bg-[#c9f24b] pulse-dot", text: "Ready" };
    return { tone: "neutral" as const, dot: "bg-white/25", text: "Not signed in" };
  };

  // Sequential exhaustion: head of the chain receives every job until its
  // 5h window fills.
  const dispatchTargetId = useMemo(() => {
    const sorted = [...items].sort((x, y) => x.id - y.id);
    const head = sorted.find((a) => {
      const wCap = a.windowCap ?? 60;
      const usage = a.windowUsage ?? 0;
      return (a.status === "ready" || a.status === "active") && usage < wCap && !a.cooldownUntil;
    });
    return head?.id ?? null;
  }, [items]);

  const showFatal = fatal || (flow?.state === "closed" ? CLOSED_EARLY : null) ||
    (flow?.state === "failed" ? flow.error || "Sign-in failed." : null);

  return (
    <div>
      <PageHead
        kicker="MODULE 08 · ACCOUNT POOL"
        title="Add a Gemini account"
        desc="Pick a browser, then sign in once in the window that opens. The account runs hidden and on its own — every generation after that is a silent HTTP request, never a browser window."
        right={
          <div className="flex items-center gap-2">
            {bridge.connected ? (
              <Chip tone="lime"><PlugZap size={11} /> LOCAL ENGINE ONLINE</Chip>
            ) : (
              <Chip tone="neutral"><WifiOff size={11} /> ENGINE OFFLINE</Chip>
            )}
            <Btn variant="lime" className="py-2" onClick={() => { setOpen(true); setFatal(null); }}>
              <Plus size={14} /> Add account
            </Btn>
          </div>
        }
      />

      {/* summary */}
      <Card className="p-5 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            ["Signed in (Pro)", `${readyCount}/${items.length || readyCount}`],
            ["5H windows live", `${items.reduce((s, a) => s + (a.windowUsage ?? 0), 0)}/${items.reduce((s, a) => s + (a.windowCap ?? 60), 0)} renders`],
            ["Daily throughput", `${200 + items.reduce((s, a) => s + (a.windowCap ?? 60), 0)}+ designs`],
            ["Loop mode", "sequential"],
          ].map(([k, v]) => (
            <div key={k} className="rounded-xl bg-white/[0.03] border border-white/[0.06] px-4 py-3">
              <div className="font-mono2 text-[17px] font-semibold text-[#c9f24b]">{v}</div>
              <div className="text-[10.5px] text-white/35 mt-0.5">{k}</div>
            </div>
          ))}
        </div>
      </Card>

      {/* cards */}
      {loading && !items.length ? (
        <div className="py-20 flex justify-center"><Loader2 className="animate-spin text-[#b9a8ff]" size={22} /></div>
      ) : !items.length ? (
        <Card className="py-16 text-center">
          <div className="h-12 w-12 mx-auto rounded-2xl bg-viol-500/12 border border-viol-500/25 flex items-center justify-center text-[#b9a8ff] mb-4">
            <Globe size={20} />
          </div>
          <div className="text-[14px] font-semibold">No accounts linked yet</div>
          <div className="text-[12px] text-white/35 mt-1.5 max-w-sm mx-auto leading-relaxed">
            Add your first Gemini account — one sign-in, then it runs hidden forever.
          </div>
          <Btn variant="lime" className="mt-5" onClick={() => setOpen(true)}><Plus size={14} /> Add and sign in</Btn>
        </Card>
      ) : (
        <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {items.map((a) => {
            const st = stateOf(a);
            const dailyPct = Math.min(1, (a.dailyUsed ?? 0) / (a.dailyCap || 200));
            const wu = a.windowUsage ?? 0;
            const wc = a.windowCap ?? 60;
            const winPct = Math.min(1, wu / wc);
            const isTarget = a.id === dispatchTargetId;
            const isReady = st.tone === "lime";
            return (
              <Card key={a.id} className={cx("p-5 transition-all duration-300", isReady && "border-[#c9f24b]/25", isTarget && "shadow-[0_0_30px_-8px_rgba(201,242,75,0.35)]")}>
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="relative shrink-0">
                      <div className="h-11 w-11 rounded-2xl bg-gradient-to-br from-viol-500/30 to-viol-500/5 border border-white/10 flex items-center justify-center font-display font-bold text-[15px] text-[#b9a8ff]">
                        {a.label.replace(/\D/g, "").padStart(2, "0") || "MF"}
                      </div>
                      <span className={cx("absolute -bottom-1 -right-1 h-3.5 w-3.5 rounded-full border-[3px] border-ink-950", st.dot)} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[13.5px] font-bold tracking-tight truncate">
                        {a.email || a.label}
                      </div>
                      <div className="font-mono2 text-[9.5px] text-white/35 truncate">
                        {providerLabel(a.provider)} · slot {a.id}/{items.length}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1.5">
                    <Chip tone={st.tone}>{st.text}</Chip>
                    {isTarget && <Chip tone="lime" className="text-[9px]">▶ DISPATCH TARGET</Chip>}
                  </div>
                </div>

                {/* capacity meters (Donepixel: Daily + 5H Window) */}
                <div className="rounded-xl bg-black/25 border border-white/[0.06] p-3.5 space-y-3 mb-4">
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-mono2 text-[9.5px] tracking-[0.16em] text-white/40">DAILY</span>
                      <span className="font-mono2 text-[10px] text-white/60 tabular-nums">
                        {a.dailyUsed ?? 0}/{a.dailyCap ?? 200} · {Math.round(dailyPct * 100)}%
                      </span>
                    </div>
                    <Bar value={dailyPct} />
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-mono2 text-[9.5px] tracking-[0.16em] text-white/40 flex items-center gap-1">
                        <Hourglass size={9} className="text-[#b9a8ff]" /> 5H WINDOW
                      </span>
                      <span className="font-mono2 text-[10px] text-white/60 tabular-nums">
                        {wu}/{wc} · {Math.round(winPct * 100)}%
                      </span>
                    </div>
                    <Bar value={winPct} hot={winPct >= 1} />
                  </div>
                </div>

                <div className="flex items-center gap-4 mb-4">
                  <Ring value={dailyPct} size={62} stroke={6} label={`${Math.round(dailyPct * 100)}%`} sub="TODAY" color={dailyPct >= 1 ? "#fbbf24" : "#c9f24b"} />
                  <div className="space-y-1.5 flex-1">
                    <Row icon={<Layers size={9} />} k="lifetime" v={String((a.totalGenerated ?? 0).toLocaleString())} />
                    <Row icon={<Timer size={9} />} k="jitter" v="65–85s" />
                    <Row icon={<Zap size={9} />} k="transport" v="httpx RPC" />
                  </div>
                </div>

                <div className="rounded-lg bg-black/30 border border-white/[0.05] px-2.5 py-1.5 mb-3 font-mono2 text-[9px] text-white/30 truncate">
                  {isReady ? "HIDDEN · silent session · batchexecute maZAc" : "sign in once to activate this slot"}
                </div>

                <div className="flex gap-2">
                  <Btn variant="ghost" className="flex-1 justify-center py-2 text-[11.5px]" onClick={() => setOpen(true)}>
                    <Plus size={12} /> Add another
                  </Btn>
                  <button
                    onClick={() => void forget(a.id)}
                    title="Remove from this list"
                    className="rounded-xl border border-white/10 bg-white/[0.03] px-3 text-white/35 hover:text-red-300 hover:border-red-400/30 transition-colors flex items-center justify-center"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* ------------------------- Donepixel login modal ------------------------- */}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={dismissLogin} />
          <div
            className="relative glass-deep rounded-3xl w-full max-w-[500px] p-6 shadow-[0_40px_120px_-20px_rgba(124,90,255,0.5)]"
            onClick={(e) => e.stopPropagation()}
          >
            {flowId ? (
              /* ---------------- in-progress sensor state ---------------- */
              <div className="text-center py-4">
                <div className="relative h-20 w-20 mx-auto mb-5">
                  <div className="absolute inset-0 rounded-full border border-viol-500/25" />
                  <div className="absolute inset-2 rounded-full border border-viol-500/15" />
                  <div className="absolute inset-0 rounded-full border-t-2 border-[#b9a8ff] animate-spin" style={{ animationDuration: "1.1s" }} />
                  <div className="absolute inset-0 flex items-center justify-center">
                    {flow?.state === "authenticated" ? (
                      <CheckCircle2 size={26} className="text-[#c9f24b]" />
                    ) : (
                      <Globe size={22} className="text-[#b9a8ff]" />
                    )}
                  </div>
                </div>

                {flow?.state === "authenticated" ? (
                  <>
                    <div className="font-display font-bold text-xl tracking-tight text-[#c9f24b]">
                      Signed in
                    </div>
                    <div className="font-mono2 text-[12px] text-white/70 mt-2">
                      {flow.email || "account"}@gmail.com
                    </div>
                    <div className="text-[12px] text-white/40 mt-3 max-w-[340px] mx-auto leading-relaxed">
                      The sign-in window has been closed. This account now runs hidden — no browser window will open again.
                    </div>
                    <Btn variant="lime" className="mt-5" onClick={dismissLogin}>Done</Btn>
                  </>
                ) : (
                  <>
                    <div className="font-display font-bold text-xl tracking-tight">
                      Waiting for you to sign in
                    </div>
                    <div className="text-[12.5px] text-white/40 mt-2 max-w-[340px] mx-auto leading-relaxed">
                      The {providerLabel(flow?.provider || provider)} window is open on Google&apos;s sign-in page.
                      Enter your email and password — 2FA is fine.
                    </div>
                    <div className="mt-5 flex items-center justify-center gap-2 font-mono2 text-[10.5px] text-white/35">
                      <span className="h-1.5 w-1.5 rounded-full bg-[#c9f24b] pulse-dot" />
                      checking every 1.5 seconds · {flow ? Math.round((flow as { elapsed?: number }).elapsed ?? 0) : 0}s
                    </div>
                    <Btn variant="ghost" className="mt-5" onClick={dismissLogin}>Cancel</Btn>
                  </>
                )}

                {showFatal && (
                  <div className="mt-5 rounded-xl border border-red-500/35 bg-red-500/[0.09] px-4 py-3 flex items-start gap-2.5 text-left">
                    <AlertTriangle size={15} className="text-red-300 mt-0.5 shrink-0" />
                    <div className="text-[12px] text-red-200/90 leading-relaxed">{showFatal}</div>
                  </div>
                )}
              </div>
            ) : (
              /* ---------------- initial picker state ---------------- */
              <>
                <div className="flex items-start justify-between mb-1">
                  <div className="font-display font-bold text-xl tracking-tight">Add a Gemini account</div>
                  <button onClick={dismissLogin} className="h-8 w-8 rounded-lg bg-white/[0.04] border border-white/10 flex items-center justify-center text-white/50 hover:text-white">
                    <X size={14} />
                  </button>
                </div>
                <div className="text-[13px] text-white/45 leading-relaxed mb-6">
                  Pick a browser, then sign in once in the window that opens. The account runs hidden and on its own.
                </div>

                <div className="font-mono2 text-[10px] tracking-[0.2em] text-white/40 mb-2.5">BROWSER</div>
                <div className="grid grid-cols-2 gap-3 mb-6">
                  {PROVIDERS.map((p) => (
                    <button
                      key={p.key}
                      onClick={() => setProvider(p.key)}
                      className={cx(
                        "rounded-2xl border px-4 py-4 text-left transition-all duration-200 group",
                        provider === p.key
                          ? "bg-viol-500/18 border-viol-500/50 shadow-[0_0_24px_-8px_rgba(124,90,255,0.7)]"
                          : "bg-white/[0.03] border-white/10 hover:border-white/25"
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <span className={cx("text-[13.5px] font-bold", provider === p.key ? "text-white" : "text-white/65")}>
                          {p.label}
                        </span>
                        <span className={cx(
                          "h-4 w-4 rounded-full border-2 flex items-center justify-center transition-all",
                          provider === p.key ? "border-[#c9f24b]" : "border-white/20"
                        )}>
                          {provider === p.key && <span className="h-2 w-2 rounded-full bg-[#c9f24b]" />}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>

                <Btn variant="lime" className="w-full justify-center py-3" onClick={() => void beginLogin()} disabled={starting}>
                  {starting ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
                  {starting ? "Opening window…" : "Add and sign in"}
                </Btn>

                {fatal && (
                  <div className="mt-4 rounded-xl border border-red-500/35 bg-red-500/[0.09] px-4 py-3 flex items-start gap-2.5">
                    <AlertTriangle size={15} className="text-red-300 mt-0.5 shrink-0" />
                    <div className="text-[12px] text-red-200/90 leading-relaxed">{fatal}</div>
                  </div>
                )}

                <div className="mt-6 pt-5 border-t border-white/[0.07] flex items-start gap-2.5">
                  <ShieldCheck size={14} className="text-[#c9f24b] mt-0.5 shrink-0" />
                  <div className="text-[11.5px] text-white/40 leading-relaxed">
                    Chrome or Brave. Your everyday browsing is never touched. Each account is its own hidden profile that you sign into once.
                  </div>
                </div>

                <div className="mt-4 flex items-center justify-center gap-2 font-mono2 text-[9.5px] text-white/25">
                  <Sparkles size={10} /> zero-prompt Imagen 3 · {tick % 2 ? "pool ready" : "pool idle"}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Bar({ value, hot }: { value: number; hot?: boolean }) {
  return (
    <div className="h-1.5 rounded-full bg-white/[0.07] overflow-hidden">
      <div
        className={cx(
          "h-full rounded-full transition-all duration-700",
          hot ? "bg-gradient-to-r from-amber-400 to-red-400" : "bg-gradient-to-r from-viol-500 to-[#c9f24b]"
        )}
        style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }}
      />
    </div>
  );
}

function Row({ icon, k, v }: { icon: React.ReactNode; k: string; v: string }) {
  return (
    <div className="flex justify-between font-mono2 text-[10px] text-white/40">
      <span className="flex items-center gap-1">{icon}{k}</span>
      <span className="text-white/70">{v}</span>
    </div>
  );
}
