"use client";

import { useEffect, useRef, useState, type ReactNode, type HTMLAttributes, type ButtonHTMLAttributes } from "react";
import { Check, Copy, Loader2 } from "lucide-react";
import clsx from "clsx";

export const cx = clsx;

/* ---------------------------------- Card ---------------------------------- */
export function Card({
  className,
  children,
  glow,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
  glow?: boolean;
}) {
  return (
    <div
      {...props}
      className={cx(
        "glass rounded-2xl relative overflow-hidden",
        glow && "shadow-[0_0_60px_-18px_rgba(124,90,255,0.55)]",
        className
      )}
    >
      {children}
    </div>
  );
}

/* ------------------------------ Section header ----------------------------- */
export function Section({
  icon,
  title,
  desc,
  right,
}: {
  icon: ReactNode;
  title: string;
  desc?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 mb-5">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 h-9 w-9 rounded-xl bg-gradient-to-br from-viol-500/30 to-viol-500/5 border border-viol-500/30 flex items-center justify-center text-[#b9a8ff]">
          {icon}
        </div>
        <div>
          <h3 className="font-display font-bold text-lg tracking-tight leading-tight">{title}</h3>
          {desc && <p className="text-[12.5px] text-white/40 mt-0.5 max-w-xl leading-relaxed">{desc}</p>}
        </div>
      </div>
      {right}
    </div>
  );
}

/* --------------------------------- Buttons --------------------------------- */
export function Btn({
  children,
  onClick,
  variant = "primary",
  className,
  disabled,
  title,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  variant?: "primary" | "ghost" | "lime" | "danger";
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const styles = {
    primary:
      "bg-viol-500 hover:bg-[#8d6fff] text-white shadow-[0_8px_30px_-8px_rgba(124,90,255,0.7)] border border-viol-500/60",
    lime: "bg-[#c9f24b] hover:bg-[#d8ff60] text-black shadow-[0_8px_30px_-8px_rgba(201,242,75,0.5)] border border-[#c9f24b]/70",
    ghost: "bg-white/[0.04] hover:bg-white/[0.09] text-white/80 border border-white/10",
    danger: "bg-red-500/10 hover:bg-red-500/20 text-red-300 border border-red-500/30",
  } as const;
  return (
    <button
      type="button"
      {...props}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={cx(
        "inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-[13px] font-semibold tracking-tight transition-all duration-200 active:scale-[0.97] disabled:opacity-40 disabled:pointer-events-none",
        styles[variant],
        className
      )}
    >
      {children}
    </button>
  );
}

/* ---------------------------------- Inputs --------------------------------- */
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-[11px] font-mono2 uppercase tracking-[0.14em] text-white/45">{label}</span>
        {hint && <span className="text-[10px] font-mono2 text-white/30">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx(
        "w-full bg-black/30 border border-white/10 rounded-xl px-3.5 py-2.5 text-[13.5px] placeholder:text-white/25 outline-none focus:border-viol-500/60 focus:ring-2 focus:ring-viol-500/20 transition-all",
        props.className
      )}
    />
  );
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(
        "w-full bg-black/30 border border-white/10 rounded-xl px-3.5 py-3 text-[13.5px] placeholder:text-white/25 outline-none focus:border-viol-500/60 focus:ring-2 focus:ring-viol-500/20 transition-all resize-y min-h-[90px] leading-relaxed",
        props.className
      )}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx(
        "w-full bg-black/30 border border-white/10 rounded-xl px-3 py-2.5 text-[13px] outline-none focus:border-viol-500/60 transition-colors appearance-none cursor-pointer [&>option]:bg-ink-800",
        props.className
      )}
    />
  );
}

/* ---------------------------------- Badges --------------------------------- */
export function Chip({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "violet" | "lime" | "red" | "amber";
  className?: string;
}) {
  const tones = {
    neutral: "bg-white/[0.06] text-white/60 border-white/10",
    violet: "bg-viol-500/15 text-[#b9a8ff] border-viol-500/30",
    lime: "bg-[#c9f24b]/10 text-[#c9f24b] border-[#c9f24b]/30",
    red: "bg-red-500/10 text-red-300 border-red-500/30",
    amber: "bg-amber-400/10 text-amber-300 border-amber-400/30",
  } as const;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-lg border px-2 py-[3px] text-[10.5px] font-mono2 font-semibold tracking-wide",
        tones[tone],
        className
      )}
    >
      {children}
    </span>
  );
}

/* ------------------------------- Copy button ------------------------------- */
export function CopyBtn({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          /* noop */
        }
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
      className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] hover:bg-white/[0.09] px-2.5 py-1.5 text-[11px] font-semibold text-white/70 transition-all active:scale-95"
    >
      {done ? <Check size={12} className="text-[#c9f24b]" /> : <Copy size={12} />}
      {done ? "Copied" : label}
    </button>
  );
}

/* -------------------------------- Char meter ------------------------------- */
export function CharMeter({ value, max }: { value: number; max: number }) {
  const pct = Math.min(100, (value / max) * 100);
  const over = value > max;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1 flex-1 rounded-full bg-white/[0.07] overflow-hidden">
        <div
          className={cx("h-full rounded-full transition-all duration-300", over ? "bg-red-400" : pct > 85 ? "bg-amber-300" : "bg-[#c9f24b]")}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={cx("font-mono2 text-[10.5px] tabular-nums", over ? "text-red-300" : "text-white/40")}>
        {value}/{max}
      </span>
    </div>
  );
}

/* --------------------------------- Toggle ---------------------------------- */
export function Toggle({ on, set, label, ariaLabel }: { on: boolean; set: (v: boolean) => void; label?: string; ariaLabel?: string }) {
  return (
    <button
      onClick={() => set(!on)}
      aria-label={ariaLabel}
      aria-pressed={on}
      className="flex items-center gap-2.5 group"
      type="button"
    >
      <span
        className={cx(
          "relative h-[22px] w-[40px] rounded-full border transition-all duration-300",
          on ? "bg-viol-500/40 border-viol-500/60" : "bg-white/[0.05] border-white/15"
        )}
      >
        <span
          className={cx(
            "absolute top-[2px] h-[16px] w-[16px] rounded-full transition-all duration-300",
            on ? "left-[20px] bg-[#c9f24b] shadow-[0_0_10px_rgba(201,242,75,0.8)]" : "left-[3px] bg-white/50"
          )}
        />
      </span>
      {label && <span className="text-[12.5px] text-white/60 group-hover:text-white/90 transition-colors">{label}</span>}
    </button>
  );
}

/* ------------------------------ Ring progress ------------------------------ */
export function Ring({
  value,
  size = 84,
  stroke = 7,
  color = "#c9f24b",
  label,
  sub,
}: {
  value: number; // 0..1
  size?: number;
  stroke?: number;
  color?: string;
  label?: string;
  sub?: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(255,255,255,0.08)" strokeWidth={stroke} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={color}
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - Math.min(1, Math.max(0, value)))}
          style={{ transition: "stroke-dashoffset 0.8s cubic-bezier(0.22,1,0.36,1)", filter: `drop-shadow(0 0 6px ${color}66)` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-mono2 font-semibold text-sm leading-none">{label}</span>
        {sub && <span className="text-[9px] font-mono2 text-white/35 mt-0.5">{sub}</span>}
      </div>
    </div>
  );
}

/* ------------------------------ Count-up hook ------------------------------ */
export function useCountUp(target: number, ms = 900) {
  const [v, setV] = useState(0);
  const raf = useRef(0);
  useEffect(() => {
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms);
      const e = 1 - Math.pow(1 - p, 3);
      setV(Math.round(target * e));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, ms]);
  return v;
}

/* --------------------------------- Spinner --------------------------------- */
export function Spin({ className }: { className?: string }) {
  return <Loader2 size={14} className={cx("animate-spin", className)} />;
}

/* ------------------------------- Page header ------------------------------- */
export function PageHead({
  kicker,
  title,
  desc,
  right,
}: {
  kicker: string;
  title: string;
  desc: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 mb-7">
      <div>
        <div className="font-mono2 text-[10.5px] tracking-[0.3em] uppercase text-[#b9a8ff] mb-2 flex items-center gap-2">
          <span className="h-1 w-1 rounded-full bg-[#c9f24b] pulse-dot" />
          {kicker}
        </div>
        <h1 className="font-display text-3xl md:text-[34px] font-bold tracking-tight leading-none text-gradient">{title}</h1>
        <p className="text-[13px] text-white/40 mt-2.5 max-w-2xl leading-relaxed">{desc}</p>
      </div>
      {right}
    </div>
  );
}
