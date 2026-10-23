import { TM_DATABASE, POLICY_WORDS, type TMEntry } from "./trademark-data";
import { metaphone, levenshtein, normalizeText, ngrams } from "./phonetics";

export type MatchType = "exact" | "phonetic" | "near" | "policy";

export interface ShieldMatch {
  fragment: string;
  matchedTerm: string;
  owner: string;
  tmClass: string;
  type: MatchType;
  severity: "critical" | "high" | "caution" | "policy";
  weight: number;
  detail: string;
}

export interface ShieldResult {
  score: number; // 0-100
  band: "CLEAR" | "CAUTION" | "HIGH RISK" | "BLOCKED";
  matches: ShieldMatch[];
  sanitized: string;
  removed: string[];
  scannedGrams: number;
}

interface Prepared {
  entry: TMEntry;
  norm: string;
  tokens: string[];
  codes: string[]; // metaphone per token
}

const PREPARED: Prepared[] = TM_DATABASE.map((entry) => {
  const norm = normalizeText(entry.term);
  const tokens = norm.split(" ").filter(Boolean);
  return { entry, norm, tokens, codes: tokens.map(metaphone) };
});

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function riskWeight(risk: string, type: MatchType): number {
  const base = risk === "critical" ? 95 : risk === "high" ? 72 : 35;
  if (type === "phonetic") return risk === "critical" ? 82 : risk === "high" ? 60 : 30;
  if (type === "near") return risk === "critical" ? 68 : risk === "high" ? 55 : 28;
  return base;
}

export function analyzeText(raw: string): ShieldResult {
  const norm = normalizeText(raw);
  const tokens = norm.split(" ").filter(Boolean);
  const grams = ngrams(tokens, 5);
  const gramSet = new Set(grams);
  const matches = new Map<string, ShieldMatch>();

  /* ---- policy words (n-gram phrase scan) ---- */
  const removed: string[] = [];
  for (const p of POLICY_WORDS) {
    const pn = normalizeText(p.phrase);
    if (!pn) continue;
    const re = new RegExp(`(^|[^a-z0-9])(${esc(pn)})(?![a-z0-9])`, "i");
    const m = norm.match(re);
    if (m) {
      removed.push(p.phrase);
      matches.set(`policy:${pn}`, {
        fragment: m[2],
        matchedTerm: p.phrase,
        owner: "Amazon Content Policy",
        tmClass: "N/A",
        type: "policy",
        severity: "policy",
        weight: 25,
        detail: p.reason,
      });
    }
  }

  /* ---- trademark dictionary ---- */
  for (const p of PREPARED) {
    if (p.tokens.length === 0) continue;

    if (p.tokens.length === 1) {
      const term = p.tokens[0];
      for (const t of tokens) {
        if (t === term) {
          matches.set(`exact:${p.norm}`, {
            fragment: t,
            matchedTerm: p.entry.term,
            owner: p.entry.owner,
            tmClass: p.entry.tmClass,
            type: "exact",
            severity: p.entry.risk,
            weight: riskWeight(p.entry.risk, "exact"),
            detail: `Exact match on a ${p.entry.risk === "critical" ? "registered" : "watched"} mark`,
          });
          break;
        }
        if (p.entry.risk !== "caution") {
          if (t.length >= 4 && levenshtein(t, term) === 1) {
            matches.set(`near:${p.norm}:${t}`, {
              fragment: t,
              matchedTerm: p.entry.term,
              owner: p.entry.owner,
              tmClass: p.entry.tmClass,
              type: "near",
              severity: p.entry.risk,
              weight: riskWeight(p.entry.risk, "near"),
              detail: `Edit distance 1 from "${p.entry.term}" — likely evasive misspelling`,
            });
          } else if (t.length >= 3 && metaphone(t) === p.codes[0] && t !== term) {
            matches.set(`phon:${p.norm}:${t}`, {
              fragment: t,
              matchedTerm: p.entry.term,
              owner: p.entry.owner,
              tmClass: p.entry.tmClass,
              type: "phonetic",
              severity: p.entry.risk,
              weight: riskWeight(p.entry.risk, "phonetic"),
              detail: `Phonetic alias of "${p.entry.term}" (Metaphone ${p.codes[0]})`,
            });
          }
        }
      }
    } else {
      const n = p.tokens.length;
      // exact n-gram
      if (gramSet.has(p.norm)) {
        matches.set(`exact:${p.norm}`, {
          fragment: p.norm,
          matchedTerm: p.entry.term,
          owner: p.entry.owner,
          tmClass: p.entry.tmClass,
          type: "exact",
          severity: p.entry.risk,
          weight: riskWeight(p.entry.risk, "exact"),
          detail: `Exact ${n}-gram match on a ${p.entry.risk === "critical" ? "registered" : "watched"} phrase mark`,
        });
        continue;
      }
      if (p.entry.risk === "caution") continue;
      // phonetic n-gram: same window length, all codes equal, not exact
      for (let i = 0; i <= tokens.length - n; i++) {
        const win = tokens.slice(i, i + n);
        if (win.join(" ") === p.norm) continue;
        const codes = win.map(metaphone);
        if (codes.every((c, k) => c === p.codes[k])) {
          matches.set(`phon:${p.norm}:${win.join(" ")}`, {
            fragment: win.join(" "),
            matchedTerm: p.entry.term,
            owner: p.entry.owner,
            tmClass: p.entry.tmClass,
            type: "phonetic",
            severity: p.entry.risk,
            weight: riskWeight(p.entry.risk, "phonetic"),
            detail: `Phonetic phrase alias of "${p.entry.term}"`,
          });
          break;
        }
      }
    }
  }

  /* ---- sanitize Amazon-policy words out of the raw text ---- */
  let sanitized = raw;
  for (const r of removed) {
    const pn = normalizeText(r).replace(/\s+/g, "\\s*");
    sanitized = sanitized.replace(new RegExp(`(?<![a-zA-Z0-9])${esc(raw.toLowerCase().includes(r) ? r : r)}(?![a-zA-Z0-9])`, "gi"), "");
    // phrase-safe second pass (handles hyphens/spaces)
    sanitized = sanitized.replace(new RegExp(pn.replace(/\\s\*/g, "[\\s-]*"), "gi"), "");
  }
  sanitized = sanitized
    .replace(/\s*-\s*-\s*/g, " - ")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([,.!?:;])/g, "$1")
    .replace(/^[,\s]+|[,\s]+$/g, "")
    .trim();

  const list = Array.from(matches.values()).sort((a, b) => b.weight - a.weight);
  const max = list.length ? list[0].weight : 0;
  const highs = list.filter((m) => m.weight >= 50).length;
  const score = Math.min(100, max + (highs > 1 ? 8 : 0));
  const band: ShieldResult["band"] = score >= 70 ? "BLOCKED" : score >= 45 ? "HIGH RISK" : score >= 20 ? "CAUTION" : "CLEAR";

  return { score, band, matches: list, sanitized, removed, scannedGrams: gramSet.size };
}
