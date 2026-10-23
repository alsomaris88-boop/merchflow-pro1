"use client";

import { useCallback, useState } from "react";
import { AlertTriangle, CheckCircle2, Eraser, Fingerprint, ScanLine, ScanText, ShieldAlert, ShieldCheck, Type } from "lucide-react";
import { Btn, Card, Chip, CopyBtn, PageHead, Ring, Section, TextArea, cx } from "@/components/ui";
import type { ShieldResult } from "@/lib/trademark-engine";
import GraphicOcr from "@/components/graphic-ocr";

const EXAMPLES = [
  { label: "Critical mark", text: "Just do it - vintage nike inspired design" },
  { label: "Phonetic alias", text: "Nikey parody swoosh athletic design" },
  { label: "Policy words", text: "Retro bass fishing dad - best seller premium quality t-shirt" },
  { label: "Clean niche", text: "Groovy pickleball skeleton sunset retro wave" },
];

const BAND_TONE: Record<string, "lime" | "amber" | "red"> = {
  CLEAR: "lime",
  CAUTION: "amber",
  "HIGH RISK": "red",
  BLOCKED: "red",
};
const BAND_COLOR: Record<string, string> = {
  CLEAR: "#c9f24b",
  CAUTION: "#fbbf24",
  "HIGH RISK": "#fb7185",
  BLOCKED: "#ef4444",
};

const TYPE_LABEL: Record<string, string> = {
  exact: "EXACT N-GRAM",
  phonetic: "PHONETIC ALIAS",
  near: "EDIT-DISTANCE 1",
  policy: "POLICY WORD",
};

export default function Trademark() {
  const [text, setText] = useState("Retro bass fishing dad - best seller t-shirt, just do it energy");
  const [res, setRes] = useState<ShieldResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"text" | "ocr">("text");

  const scan = useCallback(async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const r = await fetch("/api/trademark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      setRes(await r.json());
    } catch {
      setRes(null);
    }
    setBusy(false);
  }, [busy, text]);

  return (
    <div>
      <PageHead
        kicker="MODULE 04 · ZERO-STRIKE TRADEMARK SHIELD"
        title="USPTO Class 025 screening"
        desc="N-gram scanning (1–5), Metaphone phonetic-alias detection and Levenshtein edit-distance matching catch evasive misspellings like “Nikey”. The Amazon policy sanitizer strips garment and promotional words automatically."
        right={<Chip tone="lime"><ShieldCheck size={11} /> {120}+ watched apparel marks</Chip>}
      />

      {/* tabs */}
      <div className="flex gap-2 mb-5">
        {([
          { key: "text" as const, label: "Listing Text Scan", icon: <Type size={13} /> },
          { key: "ocr" as const, label: "Graphic OCR Scan", icon: <ScanText size={13} /> },
        ]).map((t) => (
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

      {tab === "ocr" && <GraphicOcr />}

      {tab === "text" && (
      <>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* input */}
        <Card className="p-5">
          <Section
            icon={<ScanLine size={16} />}
            title="Scan listing copy"
            desc="Paste any title, bullets, tags or brand name. One pass screens every field."
          />
          <TextArea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="Paste title, bullet points or tags…" />
          <div className="flex flex-wrap gap-2 mt-3">
            {EXAMPLES.map((e) => (
              <button
                key={e.label}
                onClick={() => { setText(e.text); setRes(null); }}
                className="rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/[0.07] px-2.5 py-1.5 text-[10.5px] font-mono2 text-white/55 transition-colors"
              >
                {e.label}
              </button>
            ))}
          </div>
          <div className="flex items-center justify-between mt-4">
            <span className="font-mono2 text-[10.5px] text-white/30">{text.length} chars · 1–5 gram scan · metaphone + lev</span>
            <Btn onClick={() => void scan()} disabled={busy || !text.trim()} variant="lime">
              {busy ? "Scanning…" : <><Fingerprint size={14} /> Run full shield</>}
            </Btn>
          </div>
        </Card>

        {/* verdict */}
        <Card className={cx("p-5 relative overflow-hidden", res && "glow")}>
          {!res ? (
            <div className="h-full min-h-[280px] flex flex-col items-center justify-center text-center">
              <div className="relative h-24 w-24 mb-5">
                <div className="absolute inset-0 rounded-full border border-viol-500/25" />
                <div className="absolute inset-3 rounded-full border border-viol-500/15" />
                <div className="absolute inset-0 rounded-full border-t-2 border-[#b9a8ff] animate-spin" style={{ animationDuration: "2.4s" }} />
                <div className="absolute inset-0 flex items-center justify-center">
                  <ShieldCheck size={26} className="text-[#b9a8ff]" />
                </div>
              </div>
              <div className="text-[14px] font-semibold">Shield standing by</div>
              <div className="text-[11.5px] text-white/35 mt-1 max-w-[260px]">Every phrase is cross-referenced before a single listing leaves the hub.</div>
            </div>
          ) : (
            <div>
              <div className="flex items-center gap-5 mb-5">
                <Ring
                  value={res.score / 100}
                  color={BAND_COLOR[res.band]}
                  label={`${res.score}`}
                  sub="RISK"
                  size={92}
                />
                <div>
                  <Chip tone={BAND_TONE[res.band]} className="text-[11px] px-3 py-1.5 mb-2">
                    {res.band === "CLEAR" ? <CheckCircle2 size={12} /> : res.band === "CAUTION" ? <AlertTriangle size={12} /> : <ShieldAlert size={12} />}
                    {res.band}
                  </Chip>
                  <div className="font-mono2 text-[11px] text-white/40">
                    {res.matches.length} hit{res.matches.length === 1 ? "" : "s"} · {res.scannedGrams} grams scanned
                  </div>
                  <div className="font-mono2 text-[10px] text-white/25 mt-1">USPTO 025 + EUIPO watchlist + Amazon policy DB</div>
                </div>
              </div>

              <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1">
                {res.matches.map((m, i) => (
                  <div key={i} className="rounded-xl bg-white/[0.03] border border-white/[0.07] px-3.5 py-2.5 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[12.5px] font-semibold">
                        <span className="text-red-300">“{m.fragment}”</span>
                        <span className="text-white/35"> → </span>
                        <span>{m.matchedTerm}</span>
                      </div>
                      <div className="text-[10.5px] text-white/40 mt-0.5">{m.detail}</div>
                      <div className="font-mono2 text-[9.5px] text-white/25 mt-1">{m.owner} · Class {m.tmClass}</div>
                    </div>
                    <Chip tone={m.type === "policy" ? "amber" : m.weight >= 70 ? "red" : "amber"} className="shrink-0">
                      {TYPE_LABEL[m.type]}
                    </Chip>
                  </div>
                ))}
                {!res.matches.length && (
                  <div className="rounded-xl border border-[#c9f24b]/25 bg-[#c9f24b]/[0.05] p-4 text-center text-[12px] text-[#c9f24b] font-semibold">
                    Zero hits — this copy is safe to publish.
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* sanitizer */}
      {res && (
        <Card className="mt-4 p-5">
          <Section
            icon={<Eraser size={16} />}
            title="Amazon policy sanitizer"
            desc={res.removed.length ? `Stripped ${res.removed.length} prohibited term(s): ${res.removed.join(", ")}` : "No prohibited terms found — copy already compliant."}
            right={<CopyBtn text={res.sanitized} label="Copy sanitized" />}
          />
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <div className="font-mono2 text-[9.5px] tracking-[0.2em] text-white/35 mb-2">RAW INPUT</div>
              <div className="rounded-xl bg-black/30 border border-white/[0.06] p-4 text-[12.5px] text-white/50 leading-relaxed min-h-[70px]">{text}</div>
            </div>
            <div>
              <div className="font-mono2 text-[9.5px] tracking-[0.2em] text-[#c9f24b] mb-2">SANITIZED · MBA COMPLIANT</div>
              <div className="rounded-xl bg-[#c9f24b]/[0.04] border border-[#c9f24b]/20 p-4 text-[12.5px] text-white/85 leading-relaxed min-h-[70px]">{res.sanitized || "(empty after sanitize)"}</div>
            </div>
          </div>
        </Card>
      )}
      </>
      )}
    </div>
  );
}
