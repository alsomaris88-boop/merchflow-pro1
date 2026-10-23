"use client";

import { useCallback, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Eye, ImageUp, Loader2, ScanText, ShieldAlert, X } from "lucide-react";
import { Btn, Card, Chip, Section, cx } from "@/components/ui";
import type { ShieldResult } from "@/lib/trademark-engine";

interface Token {
  text: string;
  conf: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface OcrState {
  busy: boolean;
  stage: string;
  engine?: string;
  tokens: Token[];
  text: string;
  imageW: number;
  imageH: number;
  shield?: ShieldResult;
  error?: string;
}

const EMPTY: OcrState = { busy: false, stage: "", tokens: [], text: "", imageW: 0, imageH: 0 };

export default function GraphicOcr() {
  const [url, setUrl] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [st, setSt] = useState<OcrState>(EMPTY);
  const fileRef = useRef<File | null>(null);

  const reset = useCallback(() => {
    setSt(EMPTY);
  }, []);

  const onFile = useCallback((f: File) => {
    fileRef.current = f;
    setUrl(URL.createObjectURL(f));
    setName(f.name.replace(/\.[^.]+$/, ""));
    reset();
  }, [reset]);

  const scan = useCallback(async () => {
    const f = fileRef.current;
    if (!f || st.busy) return;
    setSt({ ...EMPTY, busy: true, stage: "preparing image" });

    // flatten alpha onto white + upscale small art — OCR accuracy booster
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = rej;
      i.src = URL.createObjectURL(f);
    });
    const scale = Math.max(1, Math.min(2.2, 1400 / Math.max(img.width, img.height)));
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * scale);
    c.height = Math.round(img.height * scale);
    const ctx = c.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    const dataUrl = c.toDataURL("image/png");

    let tokens: Token[] = [];
    let engine = "";
    let error: string | undefined;

    // 1) try the local engine (tesseract binary — fastest + most accurate)
    try {
      setSt((s) => ({ ...s, stage: "local engine OCR" }));
      const r = await fetch("/api/bridge/elite/ocr", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: dataUrl, name }),
      });
      const d = await r.json();
      if (d.ok && Array.isArray(d.tokens)) {
        tokens = d.tokens as Token[];
        engine = "local tesseract";
      }
    } catch {
      /* fall through */
    }

    // 2) in-browser WASM fallback
    if (!engine) {
      try {
        setSt((s) => ({ ...s, stage: "in-browser OCR (wasm)" }));
        const { default: Tesseract } = await import("tesseract.js");
        const res = await Tesseract.recognize(dataUrl, "eng");
        const words = (res.data as unknown as { words?: { text: string; confidence: number; bbox: { x0: number; y0: number; x1: number; y1: number } }[] }).words ?? [];
        tokens = words
          .filter((w) => w.text.trim().length > 1 && w.confidence >= 55 && /[A-Za-z]{2,}/.test(w.text))
          .map((w) => ({
            text: w.text.trim(),
            conf: Math.round(w.confidence),
            x: w.bbox.x0,
            y: w.bbox.y0,
            w: w.bbox.x1 - w.bbox.x0,
            h: w.bbox.y1 - w.bbox.y0,
          }));
        engine = "in-browser wasm";
      } catch (e) {
        error = e instanceof Error ? e.message : "OCR failed";
      }
    }

    const text = tokens.map((t) => t.text).join(" ");

    // 3) run the extracted text through the Trademark Shield
    let shield: ShieldResult | undefined;
    if (text.trim()) {
      try {
        setSt((s) => ({ ...s, stage: "USPTO Class 025 screening" }));
        const r = await fetch("/api/trademark", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        shield = (await r.json()) as ShieldResult;
      } catch {
        /* noop */
      }
    }

    setSt({
      busy: false, stage: "", engine, tokens, text,
      imageW: c.width, imageH: c.height, shield, error,
    });
  }, [name, st.busy]);

  const flagged = st.shield?.matches.filter((m) => m.type !== "policy") ?? [];
  const hasRisk = flagged.length > 0;

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      {/* upload + overlay */}
      <Card className="p-5">
        <Section
          icon={<ScanText size={16} />}
          title="Scan artwork for embedded brand words"
          desc="AI renders often hallucinate logos and brand names inside the graphic. Amazon's vision AI reads them — this catches them first."
        />
        {!url ? (
          <div
            className="dropzone rounded-2xl py-16 text-center cursor-pointer"
            onClick={() => document.getElementById("ocr-file")?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) onFile(f); }}
          >
            <input id="ocr-file" type="file" accept="image/*" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
            <ImageUp size={22} className="mx-auto text-white/25 mb-3" />
            <div className="text-[13px] font-semibold">Drop a design file</div>
            <div className="text-[11px] text-white/35 mt-1">PNG · JPG · transparent or flat</div>
          </div>
        ) : (
          <>
            <div className="checkerboard rounded-2xl border border-white/10 relative overflow-hidden" style={{ aspectRatio: "4/5" }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={url} alt={name} className="absolute inset-0 w-full h-full object-contain" />
              {/* token boxes */}
              {st.tokens.length > 0 && st.imageW > 0 && (
                <svg viewBox={`0 0 ${st.imageW} ${st.imageH}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="xMidYMid meet">
                  {st.tokens.map((t, i) => {
                    const risky = flagged.some((m) =>
                      t.text.toLowerCase().includes(m.fragment.split(" ")[0]) ||
                      m.fragment.toLowerCase().includes(t.text.toLowerCase())
                    );
                    return (
                      <g key={i}>
                        <rect x={t.x} y={t.y} width={t.w} height={t.h} fill={risky ? "rgba(239,68,68,0.22)" : "rgba(124,90,255,0.14)"}
                          stroke={risky ? "#ef4444" : "#7c5aff"} strokeWidth={Math.max(2, st.imageW / 420)} rx={3} />
                      </g>
                    );
                  })}
                </svg>
              )}
              <button onClick={() => { setUrl(null); fileRef.current = null; reset(); }}
                className="absolute top-2 right-2 h-7 w-7 rounded-lg bg-black/70 border border-white/15 flex items-center justify-center text-white/60 hover:text-white">
                <X size={13} />
              </button>
            </div>
            <div className="mt-4 flex items-center justify-between gap-3">
              <div className="font-mono2 text-[10.5px] text-white/35 truncate">
                {name}{st.engine ? ` · ${st.engine}` : ""}
              </div>
              <Btn variant="lime" onClick={() => void scan()} disabled={st.busy}>
                {st.busy ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
                {st.busy ? st.stage || "Scanning…" : "Run OCR scan"}
              </Btn>
            </div>
          </>
        )}
      </Card>

      {/* verdict */}
      <Card className={cx("p-5", hasRisk && "border-red-500/35")}>
        {!st.engine && !st.busy ? (
          <div className="h-full min-h-[300px] flex flex-col items-center justify-center text-center">
            <div className="h-12 w-12 rounded-2xl bg-viol-500/12 border border-viol-500/25 flex items-center justify-center text-[#b9a8ff] mb-4">
              <ScanText size={20} />
            </div>
            <div className="text-[14px] font-semibold">Visual IP Sentry standing by</div>
            <div className="text-[11.5px] text-white/35 mt-1.5 max-w-[280px] leading-relaxed">
              Every text token found inside the artwork is screened against the USPTO Class 025 database.
            </div>
          </div>
        ) : st.busy ? (
          <div className="h-full min-h-[300px] flex flex-col items-center justify-center">
            <Loader2 size={24} className="animate-spin text-[#b9a8ff] mb-3" />
            <div className="font-mono2 text-[11px] text-white/45">{st.stage}</div>
          </div>
        ) : (
          <>
            <div className={cx(
              "rounded-2xl border p-4 mb-4 flex items-start gap-3",
              hasRisk ? "bg-red-500/[0.09] border-red-500/35" : "bg-[#c9f24b]/[0.05] border-[#c9f24b]/30"
            )}>
              {hasRisk ? <ShieldAlert size={18} className="text-red-300 mt-0.5 shrink-0" /> : <CheckCircle2 size={18} className="text-[#c9f24b] mt-0.5 shrink-0" />}
              <div>
                <div className={cx("text-[13.5px] font-bold", hasRisk ? "text-red-300" : "text-[#c9f24b]")}>
                  {hasRisk ? "Risk: Brand keyword detected inside graphic" : "No registered marks found inside the graphic"}
                </div>
                <div className="text-[11.5px] text-white/45 mt-1 leading-relaxed">
                  {st.tokens.length
                    ? `${st.tokens.length} text token${st.tokens.length === 1 ? "" : "s"} read from the artwork${hasRisk ? " — do not upload until removed." : "."}`
                    : "No legible text detected in the artwork — pure graphic art."}
                </div>
              </div>
            </div>

            {flagged.length > 0 && (
              <div className="space-y-2 mb-4 max-h-[200px] overflow-y-auto pr-1">
                {flagged.map((m, i) => (
                  <div key={i} className="rounded-xl bg-red-500/[0.06] border border-red-500/25 px-3.5 py-2.5">
                    <div className="text-[12.5px] font-semibold">
                      <span className="text-red-300">“{m.fragment}”</span>
                      <span className="text-white/35"> → </span>
                      <span>{m.matchedTerm}</span>
                    </div>
                    <div className="font-mono2 text-[9.5px] text-white/30 mt-1">{m.owner} · Class {m.tmClass} · {m.type}</div>
                  </div>
                ))}
              </div>
            )}

            {st.tokens.length > 0 && (
              <>
                <div className="font-mono2 text-[9.5px] tracking-[0.2em] text-white/35 mb-2">EXTRACTED TOKENS</div>
                <div className="flex flex-wrap gap-1.5 mb-4">
                  {st.tokens.map((t, i) => {
                    const risky = flagged.some((m) =>
                      t.text.toLowerCase().includes(m.fragment.split(" ")[0]) ||
                      m.fragment.toLowerCase().includes(t.text.toLowerCase())
                    );
                    return (
                      <span key={i} className={cx(
                        "rounded-lg border px-2 py-1 text-[10.5px] font-mono2",
                        risky ? "bg-red-500/12 border-red-500/40 text-red-200" : "bg-white/[0.04] border-white/10 text-white/55"
                      )}>
                        {t.text}<span className="text-white/25"> {t.conf}%</span>
                      </span>
                    );
                  })}
                </div>
              </>
            )}

            {st.error && (
              <div className="rounded-xl border border-amber-400/30 bg-amber-400/[0.07] px-3.5 py-2.5 flex items-start gap-2">
                <AlertTriangle size={14} className="text-amber-300 mt-0.5 shrink-0" />
                <div className="text-[11.5px] text-amber-200/85">{st.error}</div>
              </div>
            )}

            <div className="flex items-center gap-2 mt-2">
              <Chip tone="neutral">{st.engine}</Chip>
              {st.shield && <Chip tone={st.shield.band === "CLEAR" ? "lime" : "red"}>shield {st.shield.score}/100</Chip>}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
