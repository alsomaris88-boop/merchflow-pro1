"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Globe, Loader2, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { Btn, Card, Chip, PageHead } from "@/components/ui";
import { probeBridge } from "@/lib/bridge";
import { isConnectedAccount, NO_ACCOUNTS_MESSAGE, type ConnectedAccount } from "@/lib/accounts-client";

interface LoginFlow {
  state: "pending" | "authenticated" | "closed" | "failed";
  email?: string;
  error?: string;
}

export default function Accounts() {
  const [items, setItems] = useState<ConnectedAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [engineOnline, setEngineOnline] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("chrome");
  const [flowId, setFlowId] = useState<string | null>(null);
  const [flow, setFlow] = useState<LoginFlow | null>(null);
  const [starting, setStarting] = useState(false);
  const [reconnectSlot, setReconnectSlot] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const loadVersion = useRef(0);

  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    try {
      const response = await fetch("/api/accounts", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.reason || data.detail || "The local account database is unavailable.");
      if (version === loadVersion.current) setItems(Array.isArray(data.items) ? data.items.filter(isConnectedAccount) : []);
    } catch (cause) {
      if (version === loadVersion.current) {
        setItems([]); // never retain an invented or stale fallback list
        setError(cause instanceof Error ? cause.message : "Could not read connected accounts.");
      }
    } finally {
      if (version === loadVersion.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const refresh = () => {
      void load();
      void probeBridge().then((bridge) => setEngineOnline(!!bridge.connected)).catch(() => setEngineOnline(false));
    };
    refresh();
    const timer = setInterval(refresh, 12000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!flowId || flow?.state !== "pending") return;
    let disposed = false;
    const poll = async () => {
      try {
        const response = await fetch(`/api/bridge/login/flow/${encodeURIComponent(flowId)}`, { cache: "no-store" });
        if (!response.ok) return;
        const next = await response.json() as LoginFlow;
        if (disposed) return;
        setFlow(next);
        if (next.state === "authenticated") await load();
      } catch { /* A temporary network failure is not a successful login. */ }
    };
    void poll();
    const timer = setInterval(() => void poll(), 1500);
    return () => { disposed = true; clearInterval(timer); };
  }, [flowId, flow?.state, load]);

  const startLogin = async () => {
    if (starting || flow?.state === "pending") return;
    setStarting(true); setError(""); setFlow(null); setFlowId(null);
    try {
      const response = await fetch("/api/bridge/login/start", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider, ...(reconnectSlot ? { slot: reconnectSlot } : {}) }),
      });
      const data = await response.json();
      if (!response.ok || !data.flowId) throw new Error(data.reason || data.detail || "Could not open the Google sign-in window.");
      setFlowId(data.flowId); setFlow({ state: "pending" });
      // Do not insert an optimistic account. Only SQLite rows returned after
      // the browser sensor verifies real Google credentials may enter the list.
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start browser sign-in.");
    } finally { setStarting(false); }
  };

  const dismiss = () => {
    if (flowId && flow?.state === "pending") void fetch(`/api/bridge/login/flow/${encodeURIComponent(flowId)}`, { method: "POST" }).catch(() => undefined);
    setOpen(false); setFlowId(null); setFlow(null); setError("");
  };

  const disconnect = async (account: ConnectedAccount) => {
    setRemoving(account.id); setError("");
    try {
      const response = await fetch(`/api/accounts?id=${account.id}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.reason || "Could not disconnect the account.");
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Disconnect failed."); }
    finally { setRemoving(null); }
  };

  const add = () => { setReconnectSlot(null); setError(""); setOpen(true); };
  const reconnect = (account: ConnectedAccount) => { setReconnectSlot(account.id); setProvider(account.provider); setError(""); setOpen(true); };
  const pending = flow?.state === "pending";
  const connected = flow?.state === "authenticated";
  return <div>
    <PageHead kicker="MODULE 08 · ACCOUNT MANAGER" title="Your connected Gemini accounts"
      desc="Start with zero accounts. Link your Google account in a real browser window; verified sessions stay in isolated local browser profiles and SQLite."
      right={<div className="flex gap-2 items-center"><Chip tone={engineOnline ? "lime" : "neutral"}>{engineOnline ? "LOCAL ENGINE ONLINE" : "ENGINE OFFLINE"}</Chip><Btn variant="lime" onClick={add}><Plus size={14} />Add Gemini Account</Btn></div>} />
    {error && !open && <div role="alert" className="mb-4 rounded-xl border border-red-400/30 bg-red-500/10 p-4 text-sm text-red-200">{error}</div>}
    <Card className="p-5 mb-4"><div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {[["Connected accounts", items.length], ["Ready now", items.filter((a) => a.authed && a.status === "ready").length], ["Requests running", items.filter((a) => a.status === "active").length], ["Completed renders", items.reduce((sum, a) => sum + (a.totalGenerated || 0), 0)]].map(([label, value]) => <div key={label} className="rounded-xl border border-white/10 p-3"><div className="font-mono2 text-xl text-[#c9f24b]">{value}</div><div className="text-xs text-white/40 mt-1">{label}</div></div>)}
    </div></Card>
    {loading ? <div className="py-16 flex justify-center"><Loader2 className="animate-spin" aria-label="Loading connected accounts" /></div> : !items.length ?
      <Card className="py-16 px-6 text-center" data-testid="accounts-empty">
        <Globe size={30} className="mx-auto text-[#b9a8ff] mb-4" />
        <p className="text-sm text-white/70 max-w-lg mx-auto">{NO_ACCOUNTS_MESSAGE}</p>
        <Btn variant="lime" className="mt-5" onClick={add}><Plus size={14} />Add Gemini Account</Btn>
      </Card> : <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">{items.map((account) => <Card key={account.id} className="p-5" data-testid="connected-account">
        <div className="flex justify-between gap-3 items-start"><div className="min-w-0"><div className="text-sm font-semibold truncate">{account.email}</div><div className="text-xs text-white/40 mt-1">{account.provider === "brave" ? "Brave" : account.provider === "edge" ? "Microsoft Edge" : "Google Chrome"}</div></div><ShieldCheck size={19} className="shrink-0 text-[#c9f24b]" /></div>
        <div className="mt-4"><Chip tone={account.status === "auth-required" ? "amber" : account.status === "cooldown" ? "amber" : "lime"}>{account.status === "auth-required" ? "Reconnect required" : account.status === "cooldown" ? "Cooling down" : account.status === "active" ? "Generating" : "Session connected"}</Chip></div>
        <div className="mt-4 space-y-2 text-xs text-white/45"><div>{account.totalGenerated || 0} completed renders</div><div>Configured window: {account.windowUsage || 0} / {account.windowCap ?? 60}</div><div>Verified {new Date(account.verifiedAt * 1000).toLocaleString()}</div><div className="flex items-center gap-1"><ShieldCheck size={12} />Private local session · no frontend credentials</div></div>
        <Btn variant="ghost" className="mt-4 w-full justify-center" onClick={() => reconnect(account)} aria-label={`Reconnect ${account.email}`}><Globe size={13} />Reconnect account</Btn>
        <Btn variant="ghost" className="mt-2 w-full justify-center" disabled={removing === account.id} onClick={() => void disconnect(account)} aria-label={`Disconnect ${account.email}`}>{removing === account.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}Disconnect account</Btn>
      </Card>)}</div>}

    {open && <div className="fixed inset-0 z-50 flex items-center justify-center p-4"><div className="absolute inset-0 bg-black/75 backdrop-blur-sm" onClick={dismiss} /><Card className="relative w-full max-w-lg p-6" role="dialog" aria-modal="true" aria-label="Connect your Google account">
      <div className="flex justify-between items-start gap-4"><h2 className="text-xl font-bold">Connect your Google account</h2><button type="button" aria-label="Close sign-in" onClick={dismiss}><X size={19} /></button></div>
      {connected ? <div className="text-center py-7"><CheckCircle2 size={32} className="mx-auto text-[#c9f24b] mb-4" /><h3 className="font-semibold">Account connected</h3><p className="text-sm mt-2">{flow.email}</p><p className="text-xs text-white/45 mt-3">Your real session is verified and saved locally. Generation uses the headless session.</p><Btn variant="lime" className="mt-5" onClick={dismiss}>Done</Btn></div> : pending ? <div className="text-center py-8"><Loader2 size={30} className="animate-spin mx-auto text-[#b9a8ff] mb-4" /><h3 className="font-semibold">Complete Google sign-in in the browser window</h3><p className="text-xs text-white/45 mt-3">The account list stays unchanged until Gemini verifies your session. Closing or cancelling sign-in does not create an account.</p><Btn variant="ghost" className="mt-5" onClick={dismiss}>Cancel sign-in</Btn></div> : <>
        <p className="text-sm text-white/50 my-5">Sign in directly with Google in an isolated browser window. Passwords are never entered into MerchFlow.</p>
        <fieldset className="flex gap-3 mb-5"><legend className="text-xs text-white/50 mb-2">Browser</legend>{[["chrome", "Google Chrome"], ["brave", "Brave"], ["edge", "Microsoft Edge"]].map(([key, label]) => <label key={key} className="flex items-center gap-2 border border-white/15 rounded-xl p-3 flex-1 text-sm"><input type="radio" name="login-browser" value={key} checked={provider === key} onChange={() => setProvider(key)} />{label}</label>)}</fieldset>
        <Btn variant="lime" className="w-full justify-center" disabled={starting || !engineOnline} onClick={() => void startLogin()}>{starting ? <Loader2 size={15} className="animate-spin" /> : <Globe size={15} />}{starting ? "Opening browser…" : "Open Google sign-in"}</Btn>
      </>}
      {(error || flow?.error) && <div role="alert" className="mt-4 text-sm text-red-200 bg-red-500/10 rounded-xl p-3 flex gap-2"><AlertTriangle size={15} className="shrink-0" />{error || flow?.error}</div>}
    </Card></div>}
  </div>;
}
