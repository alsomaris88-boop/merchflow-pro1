"use client";

import { useCallback, useRef, useState } from "react";
import { Download, Frame, ImageUp, MoveVertical, Scaling, Shirt } from "lucide-react";
import { Btn, Card, Chip, Field, PageHead, Section, cx } from "@/components/ui";

const GARMENTS = [
  { name: "Black", hex: "#111111" },
  { name: "Dark Heather", hex: "#282828" },
  { name: "Asphalt", hex: "#37383B" },
  { name: "Navy", hex: "#151F30" },
  { name: "Olive", hex: "#3E442B" },
  { name: "Cranberry", hex: "#5C1D24" },
  { name: "White", hex: "#F3F4F6" },
  { name: "Heather Grey", hex: "#C7C9CD" },
];

const TEE_PATH =
  "M150 34 C132 40 108 52 84 72 L28 158 L92 186 L108 150 L108 398 C108 412 118 420 132 420 L268 420 C282 420 292 412 292 398 L292 150 L308 186 L372 158 L316 72 C292 52 268 40 250 34 C240 58 222 68 200 68 C178 68 160 58 150 34 Z";

const PRINT = { x: 132, y: 110, w: 136, h: 170, cx: 200, cy: 195 };

function lumOf(hex: string): number {
  const p = parseInt(hex.slice(1), 16);
  return (0.299 * ((p >> 16) & 255) + 0.587 * ((p >> 8) & 255) + 0.114 * (p & 255)) / 255;
}

export default function Mockup() {
  const [artUrl, setArtUrl] = useState<string | null>(null);
  const artImg = useRef<HTMLImageElement | null>(null);
  const [artName, setArtName] = useState("");
  const [garment, setGarment] = useState(GARMENTS[1]);
  const [scale, setScale] = useState(1);
  const [offsetY, setOffsetY] = useState(0);
  const [scene, setScene] = useState<"studio" | "clean">("studio");

  const light = lumOf(garment.hex) > 0.45;

  const onFile = useCallback((f: File) => {
    const url = URL.createObjectURL(f);
    const img = new Image();
    img.onload = () => {
      artImg.current = img;
      setArtUrl(url);
      setArtName(f.name.replace(/\.[^.]+$/, ""));
    };
    img.src = url;
  }, []);

  const artTransform = `translate(${PRINT.cx} ${PRINT.cy + offsetY}) scale(${scale}) translate(${-PRINT.cx} ${-PRINT.cy})`;

  const exportPng = useCallback(() => {
    const img = artImg.current;
    const K = 3;
    const W = 400 * K;
    const H = 440 * K;
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    const ctx = c.getContext("2d")!;
    ctx.scale(K, K);

    if (scene === "studio") {
      const g = ctx.createRadialGradient(200, 170, 40, 200, 220, 320);
      g.addColorStop(0, "#26263a");
      g.addColorStop(1, "#0b0b14");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, 400, 440);
    }

    // tee body
    const path = new Path2D(TEE_PATH);
    ctx.fillStyle = garment.hex;
    ctx.fill(path);
    // soft inner shading
    ctx.save();
    ctx.clip(path);
    const shade = ctx.createLinearGradient(0, 0, 400, 0);
    shade.addColorStop(0, "rgba(0,0,0,0.28)");
    shade.addColorStop(0.25, "rgba(0,0,0,0)");
    shade.addColorStop(0.75, "rgba(0,0,0,0)");
    shade.addColorStop(1, "rgba(0,0,0,0.28)");
    ctx.fillStyle = shade;
    ctx.fillRect(0, 0, 400, 440);
    const vshade = ctx.createLinearGradient(0, 340, 0, 440);
    vshade.addColorStop(0, "rgba(0,0,0,0)");
    vshade.addColorStop(1, "rgba(0,0,0,0.22)");
    ctx.fillStyle = vshade;
    ctx.fillRect(0, 340, 400, 100);
    ctx.restore();

    // collar rib
    ctx.strokeStyle = "rgba(0,0,0,0.35)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(150, 34);
    ctx.bezierCurveTo(160, 60, 178, 72, 200, 72);
    ctx.bezierCurveTo(222, 72, 240, 60, 250, 34);
    ctx.stroke();
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(152, 38);
    ctx.bezierCurveTo(162, 60, 180, 69, 200, 69);
    ctx.bezierCurveTo(220, 69, 238, 60, 248, 38);
    ctx.stroke();

    // artwork
    if (img) {
      ctx.save();
      ctx.translate(PRINT.cx, PRINT.cy + offsetY);
      ctx.scale(scale, scale);
      ctx.translate(-PRINT.cx, -PRINT.cy);
      ctx.beginPath();
      ctx.rect(PRINT.x, PRINT.y, PRINT.w, PRINT.h);
      ctx.clip();
      const s = Math.min(PRINT.w / img.width, PRINT.h / img.height);
      const dw = img.width * s;
      const dh = img.height * s;
      ctx.globalCompositeOperation = light ? "multiply" : "source-over";
      ctx.globalAlpha = 0.96;
      ctx.drawImage(img, PRINT.x + (PRINT.w - dw) / 2, PRINT.y + (PRINT.h - dh) / 2, dw, dh);
      ctx.restore();
    }

    c.toBlob((b) => {
      if (!b) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(b);
      a.download = `mockup_${(artName || "design").replace(/\s+/g, "-").toLowerCase()}_${garment.name.replace(/\s+/g, "-").toLowerCase()}.png`;
      a.click();
    }, "image/png");
  }, [artName, garment, light, offsetY, scale, scene]);

  return (
    <div>
      <PageHead
        kicker="MODULE 07 · LIVE GARMENT MOCKUP STUDIO"
        title="See it on fabric, instantly"
        desc="Official marketplace garment blanks with real ink behavior — multiply blending on light fabric, vibrant spot ink on dark. Scale, collar placement and export-ready composites in one client-side canvas."
        right={<Chip tone="violet"><Shirt size={11} /> 8 official blanks</Chip>}
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* stage */}
        <Card className="xl:col-span-2 p-6 flex items-center justify-center min-h-[540px] relative overflow-hidden" glow>
          <div className="absolute inset-0 bg-blueprint opacity-60" />
          <div className="relative float-slow">
            <svg width={380} height={418} viewBox="0 0 400 440" className="drop-shadow-[0_30px_60px_rgba(0,0,0,0.55)]">
              <defs>
                <clipPath id="print-area">
                  <rect x={PRINT.x} y={PRINT.y} width={PRINT.w} height={PRINT.h} rx={4} />
                </clipPath>
                <filter id="fabric">
                  <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" result="n" />
                  <feColorMatrix in="n" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0.5 0.5 0.5 0 0" result="m" />
                  <feComposite operator="in" in="m" in2="SourceGraphic" />
                </filter>
                <linearGradient id="hshade" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0" stopColor="#000" stopOpacity="0.26" />
                  <stop offset="0.25" stopColor="#000" stopOpacity="0" />
                  <stop offset="0.75" stopColor="#000" stopOpacity="0" />
                  <stop offset="1" stopColor="#000" stopOpacity="0.26" />
                </linearGradient>
              </defs>
              <path d={TEE_PATH} fill={garment.hex} />
              <path d={TEE_PATH} fill="url(#hshade)" />
              <path d="M108 150 L92 186 L108 196 Z" fill="#000" opacity="0.18" />
              <path d="M292 150 L308 186 L292 196 Z" fill="#000" opacity="0.18" />
              <path d="M114 402 C114 410 122 414 132 414 L268 414 C278 414 286 410 286 402" stroke="#000" strokeOpacity="0.25" strokeWidth="2" fill="none" />
              <path d="M150 34 C160 60 178 72 200 72 C222 72 240 60 250 34" stroke="#000" strokeOpacity="0.4" strokeWidth="3.4" fill="none" />
              <path d="M152 38 C162 60 180 69 200 69 C220 69 238 60 248 38" stroke="#fff" strokeOpacity="0.09" strokeWidth="1.4" fill="none" />
              <rect x={PRINT.x} y={PRINT.y} width={PRINT.w} height={PRINT.h} fill="none" stroke="#fff" strokeOpacity="0.06" strokeDasharray="5 4" rx={4} />
              {artUrl && (
                <g clipPath="url(#print-area)">
                  <image
                    href={artUrl}
                    x={PRINT.x}
                    y={PRINT.y}
                    width={PRINT.w}
                    height={PRINT.h}
                    preserveAspectRatio="xMidYMid meet"
                    transform={artTransform}
                    style={light ? { mixBlendMode: "multiply", opacity: 0.92 } : { opacity: 0.98 }}
                  />
                </g>
              )}
              <path d={TEE_PATH} fill="none" stroke="#ffffff12" strokeWidth="1" />
              <path d={TEE_PATH} fill="#000" opacity="0" filter="url(#fabric)" />
            </svg>
            {/* fabric noise overlay via css */}
            <div className="absolute inset-0 pointer-events-none opacity-[0.05] mix-blend-overlay" style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'%3E%3Cfilter id='f'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23f)'/%3E%3C/svg%3E\")" }} />
          </div>
        </Card>

        {/* controls */}
        <div className="space-y-4">
          <Card className="p-5">
            <Section icon={<ImageUp size={16} />} title="Artwork" desc={artName || "load a prepared PNG"} />
            <div
              className="dropzone rounded-2xl py-6 text-center cursor-pointer"
              onClick={() => document.getElementById("mk-file")?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) onFile(f); }}
            >
              <input id="mk-file" type="file" accept="image/*" className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
              <div className="text-[12.5px] font-semibold">{artName || "Drop print-ready PNG"}</div>
              <div className="text-[10.5px] text-white/35 mt-1">transparent alpha recommended</div>
            </div>
          </Card>

          <Card className="p-5 space-y-4">
            <Field label="Garment blank · official marketplace colors">
              <div className="grid grid-cols-4 gap-2">
                {GARMENTS.map((g) => (
                  <button key={g.hex} title={g.name}
                    onClick={() => setGarment(g)}
                    className={cx("h-10 rounded-xl border-2 transition-all relative", garment.hex === g.hex ? "border-[#c9f24b] scale-105 shadow-[0_0_16px_rgba(201,242,75,0.3)]" : "border-white/12 hover:border-white/30")}
                    style={{ background: g.hex }}>
                    {garment.hex === g.hex && <span className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-[#c9f24b] border-2 border-ink-950" />}
                  </button>
                ))}
              </div>
              <div className="font-mono2 text-[10px] text-white/35 mt-1.5">{garment.name.toUpperCase()} · {garment.hex.toUpperCase()} · {light ? "multiply ink blend" : "spot ink blend"}</div>
            </Field>

            <Field label={`Artwork scale — ${Math.round(scale * 100)}%`}>
              <div className="flex items-center gap-3">
                <Scaling size={14} className="text-white/30" />
                <input type="range" min={60} max={160} value={Math.round(scale * 100)} className="flex-1"
                  style={{ "--fill": `${((scale * 100 - 60) / 100) * 100}%` } as React.CSSProperties}
                  onChange={(e) => setScale(Number(e.target.value) / 100)} />
              </div>
            </Field>

            <Field label={`Vertical placement — ${offsetY > 0 ? "+" : ""}${offsetY}px`}>
              <div className="flex items-center gap-3">
                <MoveVertical size={14} className="text-white/30" />
                <input type="range" min={-60} max={60} value={offsetY} className="flex-1"
                  style={{ "--fill": `${((offsetY + 60) / 120) * 100}%` } as React.CSSProperties}
                  onChange={(e) => setOffsetY(Number(e.target.value))} />
              </div>
            </Field>

            <div className="flex gap-2">
              <Btn variant="ghost" className="flex-1 justify-center py-2 text-[11.5px]" onClick={() => setOffsetY(0)}>Center chest</Btn>
              <Btn variant="ghost" className="flex-1 justify-center py-2 text-[11.5px]" onClick={() => setOffsetY(-24)}>Collar −180px</Btn>
            </div>

            <Field label="Scene">
              <div className="flex gap-2">
                {(["studio", "clean"] as const).map((s) => (
                  <button key={s} onClick={() => setScene(s)}
                    className={cx("flex-1 rounded-xl border px-3 py-2 text-[11.5px] font-semibold capitalize transition-all",
                      scene === s ? "bg-viol-500/20 border-viol-500/45 text-white" : "bg-white/[0.03] border-white/10 text-white/45")}>
                    {s === "studio" ? "Studio gradient" : "Transparent"}
                  </button>
                ))}
              </div>
            </Field>

            <Btn variant="lime" className="w-full justify-center" onClick={exportPng} disabled={!artUrl}>
              <Download size={14} /> Export 1200×1320 mockup
            </Btn>
            <div className="flex items-center gap-2 font-mono2 text-[9.5px] text-white/25">
              <Frame size={10} /> composited client-side · zero upload
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
