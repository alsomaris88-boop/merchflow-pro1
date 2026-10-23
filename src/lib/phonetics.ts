/* Phonetic + edit-distance primitives for evasive-spelling trademark detection */

export function normalizeText(s: string): string {
  return s
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Metaphone (simplified, Lawrence Philips) — maps words to phonetic codes */
export function metaphone(input: string): string {
  let word = input.toUpperCase().replace(/[^A-Z]/g, "");
  if (!word) return "";
  const vowels = "AEIOU";
  const isV = (c: string) => vowels.includes(c);

  if (/^(PN|KN|GN|AE|WR|WH)/.test(word)) word = word.slice(1);
  if (word.startsWith("X")) word = "S" + word.slice(1);

  let out = "";
  let i = 0;
  const at = (n: number) => (n >= 0 && n < word.length ? word[n] : "");

  while (i < word.length && out.length < 14) {
    const c = word[i];
    const prev = at(i - 1);
    const next = at(i + 1);
    const next2 = at(i + 2);

    if (isV(c)) {
      if (i === 0) out += c;
    } else if (c === "B") {
      if (!(prev === "M" && i === word.length - 1)) out += "B";
    } else if (c === "C") {
      if (next === "H") {
        out += "X";
        i++;
      } else if (next === "I" && next2 === "A") {
        out += "X";
      } else if ("IEY".includes(next)) {
        out += "S";
      } else {
        out += "K";
      }
    } else if (c === "D") {
      if (next === "G" && "EIY".includes(next2)) {
        out += "J";
        i += 2;
      } else {
        out += "T";
      }
    } else if (c === "F") {
      out += "F";
    } else if (c === "G") {
      if (next === "H") {
        if (isV(next2)) out += "F";
        i++;
      } else if (next === "N" && !isV(next2)) {
        /* silent */
      } else if ("EIY".includes(next)) {
        out += "J";
      } else {
        out += "K";
      }
    } else if (c === "H") {
      if (!(isV(prev) && isV(next)) && !(isV(prev) && !isV(next))) out += "H";
      else if (!isV(prev) && isV(next)) out += "H";
    } else if ("JKLMNR".includes(c)) {
      out += c;
    } else if (c === "P") {
      if (next === "H") {
        out += "F";
        i++;
      } else out += "P";
    } else if (c === "Q") {
      out += "K";
    } else if (c === "S") {
      if (next === "H") {
        out += "X";
        i++;
      } else if (next === "I" && "AO".includes(next2)) {
        out += "X";
      } else out += "S";
    } else if (c === "T") {
      if (next === "I" && "AO".includes(next2)) {
        out += "X";
      } else if (next === "H") {
        out += "0";
        i++;
      } else if (!(next === "C" && next2 === "H")) {
        out += "T";
      }
    } else if (c === "V") {
      out += "F";
    } else if (c === "W") {
      if (isV(next)) out += "W";
    } else if (c === "X") {
      out += "KS";
    } else if (c === "Y") {
      if (isV(next)) out += "Y";
    } else if (c === "Z") {
      out += "S";
    }
    i++;
  }
  return out;
}

/** Classic Levenshtein edit distance */
export function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Array<number>(n + 1);
  let curr = new Array<number>(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
    }
    [prev, curr] = [curr, prev];
  }
  return prev[n];
}

/** Sliding 1..maxN gram windows over tokens */
export function ngrams(tokens: string[], maxN = 5): string[] {
  const out: string[] = [];
  for (let n = 1; n <= Math.min(maxN, tokens.length); n++) {
    for (let i = 0; i <= tokens.length - n; i++) {
      out.push(tokens.slice(i, i + n).join(" "));
    }
  }
  return out;
}
