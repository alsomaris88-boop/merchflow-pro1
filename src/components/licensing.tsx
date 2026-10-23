"use client";

import { useCallback, useEffect, useState } from "react";
import { BadgeCheck, CreditCard, Fingerprint, KeyRound, Loader2, Lock, ShieldCheck, Webhook, XCircle } from "lucide-react";
import { Btn, Card, Chip, Input, PageHead, Section, cx } from "@/components/ui";

interface VerifyResult {
  valid: boolean;
  plan?: string;
  seats?: number;
  bound?: boolean;
  source?: string;
  reason?: string;
}

const PLANS = [
  {
    key: "starter", name: "Starter", price: 19, seats: 2,
    demo: "MF-STARTER-DEMO-2026-0003",
    features: ["2 isolated profiles", "Design generator + lab", "Trademark shield", "Single-machine HWID"],
  },
  {
    key: "pro", name: "Pro", price: 49, seats: 10, popular: true,
    demo: "MF-PRO-DEMO-2026-0001",
    features: ["10 isolated profiles", "Full trend hunter suite", "Dual CSV export hub", "Priority failover queue"],
  },
  {
    key: "team", name: "Team", price: 99, seats: 25,
    demo: "MF-TEAM-DEMO-2026-0002",
    features: ["25 isolated profiles", "Cloud sync foundation", "Multi-seat licensing", "Webhook automations"],
  },
];

function computeHwid(): string {
  try {
    const raw = [
      navigator.userAgent,
      String(navigator.hardwareConcurrency ?? 0),
      String((navigator as { deviceMemory?: number }).deviceMemory ?? 0),
      String(screen.width),
      String(screen.height),
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    ].join("|");
    let h1 = 5381;
    let h2 = 52711;
    for (let i = 0; i < raw.length; i++) {
      const c = raw.charCodeAt(i);
      h1 = ((h1 << 5) + h1) ^ c;
      h2 = ((h2 << 6) + h2) ^ c;
    }
    const hex = ((h1 >>> 0).toString(16) + (h2 >>> 0).toString(16)).toUpperCase().padEnd(16, "0").slice(0, 16);
    return `${hex.slice(0, 4)}-${hex.slice(4, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}`;
  } catch {
    return "0000-0000-0000-0000";
  }
}

export default function Licensing() {
  const [key, setKey] = useState("");
  const [hwid, setHwid] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<VerifyResult | null>(null);

  useEffect(() => {
    const initial = setTimeout(() => setHwid(computeHwid()), 0);
    return () => clearTimeout(initial);
  }, []);

  const verify = useCallback(async (k?: string) => {
    const useKey = (k ?? key).trim();
    if (!useKey || busy) return;
    if (k) setKey(k);
    setBusy(true);
    setRes(null);
    try {
      const r = await fetch("/api/license", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: useKey, hwid: hwid.replace(/-/g, "") }),
      });
      setRes(await r.json());
    } catch {
      setRes({ valid: false, reason: "Verification server unreachable." });
    }
    setBusy(false);
  }, [busy, hwid, key]);

  return (
    <div>
      <PageHead
        kicker="MODULE 09 · SAAS LICENSING & CLOUD SYNC"
        title="License, lock, sync"
        desc="Stripe / LemonSqueezy webhook-backed keys with hardware-ID binding — protected against duplication while legit multi-account profiles stay fully usable under one seat."
        right={<Chip tone="violet"><Lock size={11} /> HWID-bound</Chip>}
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* verify panel */}
        <Card className="p-5 xl:col-span-1" glow>
          <Section icon={<KeyRound size={16} />} title="Activate license" desc="Paste your key. First activation binds this machine's HWID." />
          <div className="space-y-4">
            <div>
              <div className="font-mono2 text-[10px] tracking-[0.2em] text-white/40 mb-1.5">LICENSE KEY</div>
              <Input
                value={key}
                onChange={(e) => setKey(e.target.value.toUpperCase())}
                placeholder="MF-XXXX-XXXX-XXXX-XXXX"
                className="font-mono2 tracking-wider"
                onKeyDown={(e) => { if (e.key === "Enter") void verify(); }}
              />
            </div>
            <div>
              <div className="font-mono2 text-[10px] tracking-[0.2em] text-white/40 mb-1.5">HARDWARE ID <span className="text-white/20">(auto-detected)</span></div>
              <div className="rounded-xl bg-black/40 border border-white/[0.07] px-3.5 py-2.5 font-mono2 text-[12px] text-[#b9a8ff] flex items-center gap-2">
                <Fingerprint size={13} /> {hwid || "…"}
              </div>
            </div>
            <Btn variant="lime" className="w-full justify-center" disabled={busy || !key.trim()} onClick={() => void verify()}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
              {busy ? "Verifying…" : "Verify & bind"}
            </Btn>

            {res && (
              <div className={cx(
                "rounded-xl border p-4 animate-in fade-in",
                res.valid ? "bg-[#c9f24b]/[0.05] border-[#c9f24b]/25" : "bg-red-500/[0.06] border-red-500/30"
              )}>
                {res.valid ? (
                  <>
                    <div className="flex items-center gap-2 text-[#c9f24b] font-bold text-[14px] mb-1.5">
                      <BadgeCheck size={16} /> {res.plan?.toUpperCase()} activated
                    </div>
                    <div className="font-mono2 text-[10.5px] text-white/50 space-y-1">
                      <div>seats: {res.seats} profiles</div>
                      <div>hwid: bound ✓ ({res.source})</div>
                      <div>sync: cloud foundation ready</div>
                    </div>
                  </>
                ) : (
                  <div className="flex items-start gap-2 text-red-300">
                    <XCircle size={16} className="mt-0.5 shrink-0" />
                    <div className="text-[12.5px]">{res.reason}</div>
                  </div>
                )}
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-1">
              <Chip tone="neutral"><Webhook size={10} /> Stripe webhooks</Chip>
              <Chip tone="neutral"><Webhook size={10} /> LemonSqueezy</Chip>
            </div>
          </div>
        </Card>

        {/* plans */}
        <div className="xl:col-span-2 grid sm:grid-cols-3 gap-4">
          {PLANS.map((p) => (
            <Card key={p.key} className={cx("p-5 flex flex-col transition-all duration-300 hover:scale-[1.02]", p.popular && "border-viol-500/40 glow")}>
              {p.popular && <Chip tone="violet" className="self-start mb-3">MOST POPULAR</Chip>}
              {!p.popular && <div className="h-[26px] mb-3" />}
              <div className="font-display font-bold text-lg tracking-tight">{p.name}</div>
              <div className="mt-1 mb-4">
                <span className="font-mono2 text-[26px] font-semibold">${p.price}</span>
                <span className="text-[11px] text-white/35 font-mono2">/mo</span>
              </div>
              <div className="space-y-2 flex-1">
                {p.features.map((f) => (
                  <div key={f} className="flex items-center gap-2 text-[12px] text-white/60">
                    <ShieldCheck size={12} className="text-[#c9f24b] shrink-0" /> {f}
                  </div>
                ))}
              </div>
              <div className="mt-4 space-y-2">
                <div className="rounded-lg bg-black/30 border border-white/[0.06] px-2.5 py-1.5 font-mono2 text-[9px] text-white/35 truncate">{p.demo}</div>
                <Btn variant={p.popular ? "primary" : "ghost"} className="w-full justify-center" onClick={() => void verify(p.demo)}>
                  <CreditCard size={13} /> Try demo key
                </Btn>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
