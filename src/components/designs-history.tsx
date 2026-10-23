"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckSquare,
  Download,
  FlaskConical,
  FolderOpen,
  HardDrive,
  Images,
  Loader2,
  Package,
  Rocket,
  Search,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Btn, Card, Chip, PageHead, cx } from "@/components/ui";
import {
  DESIGNS_EVENT,
  clearDesigns,
  deleteDesigns,
  formatBytes,
  getDesigns,
  listDesigns,
  sendToPrepare,
  timeAgo,
  type DesignRecord,
} from "@/lib/designs-store";
import type { ViewKey } from "@/components/shell";

interface Row extends DesignRecord {
  url: string;
}

export default function DesignsHistory({ setView }: { setView: (v: ViewKey) => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [zipping, setZipping] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [preview, setPreview] = useState<Row | null>(null);
  const urlsRef = useRef<string[]>([]);

  const revokeAll = useCallback(() => {
    for (const u of urlsRef.current) URL.revokeObjectURL(u);
    urlsRef.current = [];
  }, []);

  const load = useCallback(async () => {
    const recs = await listDesigns();
    revokeAll();
    const mapped = recs.map((r) => {
      const url = URL.createObjectURL(r.blob);
      urlsRef.current.push(url);
      return { ...r, url };
    });
    setRows(mapped);
    setSel((prev) => new Set([...prev].filter((id) => mapped.some((m) => m.id === id))));
    setLoading(false);
  }, [revokeAll]);

  useEffect(() => {
    void load();
    const onChange = () => void load();
    window.addEventListener(DESIGNS_EVENT, onChange);
    return () => {
      window.removeEventListener(DESIGNS_EVENT, onChange);
      revokeAll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter((r) =>
      [r.name, r.niche, r.style, r.aspect, r.account].filter(Boolean).join(" ").toLowerCase().includes(term)
    );
  }, [q, rows]);

  const allSelected = filtered.length > 0 && filtered.every((r) => sel.has(r.id));
  const totalBytes = rows.reduce((s, r) => s + r.bytes, 0);
  const selBytes = rows.filter((r) => sel.has(r.id)).reduce((s, r) => s + r.bytes, 0);

  const toggle = (id: string) =>
    setSel((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const selectAll = () => setSel(allSelected ? new Set() : new Set(filtered.map((r) => r.id)));

  /** Effective working set: selection, or everything visible when nothing is picked. */
  const working = useCallback(() => (sel.size ? filtered.filter((r) => sel.has(r.id)) : filtered), [filtered, sel]);

  const handoff = useCallback(() => {
    const ids = working().map((r) => r.id);
    if (!ids.length) return;
    sendToPrepare(ids);
    setView("prepare");
  }, [setView, working]);

  const downloadZip = useCallback(async () => {
    const set = working();
    if (!set.length || zipping) return;
    setZipping(true);
    try {
      const JSZip = (await import("jszip")).default;
      const zip = new JSZip();
      const recs = await getDesigns(set.map((r) => r.id));
      const used = new Set<string>();
      for (const r of recs) {
        let base = (r.name || "design").replace(/[^a-z0-9_-]+/gi, "-").toLowerCase().slice(0, 48) || "design";
        let fn = `${base}_${r.w}x${r.h}.png`;
        let i = 2;
        while (used.has(fn)) fn = `${base}_${r.w}x${r.h}_${i++}.png`;
        used.add(fn);
        zip.file(fn, r.blob);
      }
      zip.file(
        "manifest.csv",
        [
          ["File", "Niche", "Style", "Aspect", "Background", "Account", "Width", "Height", "Created"].join(","),
          ...recs.map((r) =>
            [
              `${(r.name || "design").replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}.png`,
              r.niche, r.style, r.aspect, r.background, r.account, r.w, r.h,
              new Date(r.createdAt).toISOString(),
            ]
              .map((v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v)))
              .join(",")
          ),
        ].join("\r\n")
      );
      const blob = await zip.generateAsync({ type: "blob", compression: "DEFLATE", compressionOptions: { level: 6 } });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `merchflow-designs_${new Date().toISOString().slice(0, 10)}_${recs.length}.zip`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    } catch {
      /* noop */
    }
    setZipping(false);
  }, [working, zipping]);

  const deleteSelected = useCallback(async () => {
    if (!sel.size) return;
    await deleteDesigns([...sel]);
    setSel(new Set());
  }, [sel]);

  const purgeAll = useCallback(async () => {
    await clearDesigns();
    setSel(new Set());
    setConfirmClear(false);
  }, []);

  return (
    <div>
      <PageHead
        kicker="MODULE 02 · DESIGNS LIBRARY & HISTORY"
        title="Every render, one batch away"
        desc="Designs auto-archive here the moment they finish generating. Multi-select, then hand the whole batch straight to the Prepare Lab, export a ZIP, or purge buffers to reclaim disk and RAM."
        right={
          <div className="flex items-center gap-2">
            <Chip tone="violet"><HardDrive size={11} /> {formatBytes(totalBytes)} cached</Chip>
            <Chip tone="lime"><Images size={11} /> {rows.length} archived</Chip>
          </div>
        }
      />

      {/* batch control bar */}
      <Card className="p-4 mb-4 sticky top-[68px] z-30 backdrop-blur-xl">
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={selectAll}
            disabled={!filtered.length}
            className={cx(
              "flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-[12.5px] font-semibold transition-all disabled:opacity-40",
              allSelected ? "bg-viol-500/20 border-viol-500/45 text-white" : "bg-white/[0.03] border-white/10 text-white/55 hover:text-white"
            )}
          >
            {allSelected ? <CheckSquare size={14} /> : <Square size={14} />}
            {allSelected ? "Deselect all" : "Select all"}
          </button>

          <div
            className={cx(
              "rounded-xl border px-3.5 py-2.5 font-mono2 text-[11.5px] font-semibold tabular-nums transition-colors",
              sel.size ? "bg-[#c9f24b]/10 border-[#c9f24b]/35 text-[#c9f24b]" : "bg-white/[0.03] border-white/10 text-white/45"
            )}
          >
            {sel.size} selected / {rows.length} total
            {sel.size > 0 && <span className="text-white/35"> · {formatBytes(selBytes)}</span>}
          </div>

          <div className="relative flex-1 min-w-[180px]">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="filter by niche, style, account…"
              className="w-full bg-black/30 border border-white/10 rounded-xl pl-8 pr-3 py-2.5 text-[12.5px] placeholder:text-white/25 outline-none focus:border-viol-500/60 transition-colors"
            />
            {q && (
              <button onClick={() => setQ("")} className="absolute right-3 top-1/2 -translate-y-1/2 text-white/30 hover:text-white/70">
                <X size={12} />
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 ml-auto">
            <Btn variant="lime" onClick={handoff} disabled={!filtered.length}>
              <Rocket size={14} /> Send to Prepare {sel.size ? `(${sel.size})` : "(all)"}
            </Btn>
            <Btn variant="primary" onClick={() => void downloadZip()} disabled={!filtered.length || zipping}>
              {zipping ? <Loader2 size={14} className="animate-spin" /> : <Package size={14} />}
              {zipping ? "Zipping…" : "Download ZIP"}
            </Btn>
            <Btn variant="danger" onClick={() => void deleteSelected()} disabled={!sel.size}>
              <Trash2 size={14} /> Delete
            </Btn>
            <Btn variant="ghost" onClick={() => setConfirmClear(true)} disabled={!rows.length}>
              Clear history
            </Btn>
          </div>
        </div>
      </Card>

      {/* grid */}
      {loading ? (
        <div className="py-24 flex justify-center"><Loader2 className="animate-spin text-[#b9a8ff]" size={22} /></div>
      ) : !rows.length ? (
        <Card className="py-20 text-center">
          <FolderOpen size={30} className="mx-auto text-white/20 mb-4" />
          <div className="text-[14px] font-semibold">No designs archived yet</div>
          <div className="text-[12px] text-white/35 mt-1.5 max-w-sm mx-auto leading-relaxed">
            Run a batch in the AI Design Generator or Pixel to Pixel — finished renders land here automatically.
          </div>
          <div className="mt-5 flex justify-center gap-2">
            <Btn variant="lime" onClick={() => setView("generator")}>Open Generator</Btn>
            <Btn variant="ghost" onClick={() => setView("recolor")}>Open Pixel to Pixel</Btn>
          </div>
        </Card>
      ) : !filtered.length ? (
        <Card className="py-16 text-center text-[13px] text-white/35">No designs match “{q}”.</Card>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
          {filtered.map((r) => {
            const on = sel.has(r.id);
            return (
              <div
                key={r.id}
                onClick={() => toggle(r.id)}
                className={cx(
                  "group relative rounded-2xl border overflow-hidden cursor-pointer transition-all duration-300",
                  on ? "border-[#c9f24b]/60 shadow-[0_0_28px_-8px_rgba(201,242,75,0.5)] scale-[0.985]" : "border-white/[0.07] hover:border-viol-500/40"
                )}
              >
                <div className="checkerboard aspect-[4/5] relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={r.url} alt={r.name} className="w-full h-full object-contain" />
                  <div className={cx(
                    "absolute top-2 left-2 h-5 w-5 rounded-md border flex items-center justify-center transition-all",
                    on ? "bg-[#c9f24b] border-[#c9f24b] text-black" : "bg-black/60 border-white/25 text-transparent group-hover:border-white/60"
                  )}>
                    <CheckSquare size={11} />
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); setPreview(r); }}
                    className="absolute top-2 right-2 h-6 w-6 rounded-md bg-black/65 border border-white/15 text-white/70 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center hover:text-white"
                    title="Preview"
                  >
                    <Search size={11} />
                  </button>
                  <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent px-2.5 pt-7 pb-2">
                    <div className="text-[11.5px] font-semibold truncate">{r.name}</div>
                    <div className="font-mono2 text-[9px] text-white/40 truncate">
                      {r.w}×{r.h} · {formatBytes(r.bytes)} · {timeAgo(r.createdAt)}
                    </div>
                  </div>
                </div>
                <div className="px-2.5 py-2 flex items-center justify-between gap-1 bg-black/25">
                  <span className="font-mono2 text-[9px] text-white/35 truncate">{r.style || "style"} → {r.niche || r.name}</span>
                  <span className="font-mono2 text-[8.5px] text-[#b9a8ff] shrink-0">{r.aspect}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {rows.length > 0 && (
        <div className="mt-5 flex items-center justify-between flex-wrap gap-3">
          <div className="font-mono2 text-[10px] text-white/25">
            auto-archived locally (IndexedDB) · blobs purge instantly on clear to free RAM + disk
          </div>
          <Btn variant="ghost" className="py-2" onClick={() => setView("prepare")}>
            <FlaskConical size={13} /> Open Prepare Lab
          </Btn>
        </div>
      )}

      {/* preview lightbox */}
      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6" onClick={() => setPreview(null)}>
          <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" />
          <div className="relative glass-deep rounded-3xl p-5 max-w-[720px] w-full" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="font-display font-bold text-lg tracking-tight">{preview.name}</div>
                <div className="font-mono2 text-[10px] text-white/40 mt-1">
                  {preview.style} → {preview.niche} · {preview.aspect} · {preview.background} · {preview.account} · {formatBytes(preview.bytes)}
                </div>
              </div>
              <button onClick={() => setPreview(null)} className="h-8 w-8 rounded-lg bg-white/[0.04] border border-white/10 flex items-center justify-center text-white/50 hover:text-white">
                <X size={14} />
              </button>
            </div>
            <div className="checkerboard rounded-2xl border border-white/10 overflow-hidden max-h-[56vh] flex items-center justify-center">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={preview.url} alt={preview.name} className="max-w-full max-h-[56vh] object-contain" />
            </div>
            {preview.prompt && (
              <pre className="mt-3 max-h-[90px] overflow-y-auto whitespace-pre-wrap font-mono2 text-[10px] leading-relaxed text-white/45 bg-black/35 border border-white/[0.06] rounded-xl p-3">
                {preview.prompt}
              </pre>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => { const a = document.createElement("a"); a.href = preview.url; a.download = `${preview.name}.png`; a.click(); }}>
                <Download size={13} /> Download PNG
              </Btn>
              <Btn variant="lime" onClick={() => { sendToPrepare([preview.id]); setPreview(null); setView("prepare"); }}>
                <Rocket size={13} /> Send to Prepare
              </Btn>
            </div>
          </div>
        </div>
      )}

      {/* clear confirm */}
      {confirmClear && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6" onClick={() => setConfirmClear(false)}>
          <div className="absolute inset-0 bg-black/75 backdrop-blur-sm" />
          <div className="relative glass-deep rounded-3xl p-6 max-w-[430px] w-full" onClick={(e) => e.stopPropagation()}>
            <div className="h-11 w-11 rounded-2xl bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-300 mb-4">
              <Trash2 size={18} />
            </div>
            <div className="font-display font-bold text-lg tracking-tight">Purge the entire history?</div>
            <div className="text-[12.5px] text-white/45 mt-2 leading-relaxed">
              Deletes all {rows.length} archived designs and frees {formatBytes(totalBytes)} of cached image buffers.
              Files already exported to disk are untouched.
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => setConfirmClear(false)}>Cancel</Btn>
              <Btn variant="danger" onClick={() => void purgeAll()}><Trash2 size={13} /> Purge everything</Btn>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
