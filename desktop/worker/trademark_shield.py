"""
MerchFlow Pro — offline USPTO Class-025 trademark shield (desktop build).
Faithful port of src/lib/phonetics.ts + src/lib/trademark-engine.ts so the
static desktop UI gets byte-equivalent verdicts with zero external services.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- phonetics
def normalize(s: str) -> str:
    s = s.lower()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def metaphone(word: str) -> str:
    w = re.sub(r"[^A-Z]", "", word.upper())
    if not w:
        return ""
    if re.match(r"^(PN|KN|GN|AE|WR|WH)", w):
        w = w[1:]
    if w.startswith("X"):
        w = "S" + w[1:]

    out: list[str] = []
    i = 0

    def at(n: int) -> str:
        return w[n] if 0 <= n < len(w) else ""

    V = "AEIOU"
    while i < len(w) and sum(len(x) for x in out) < 14:
        c, prev, nxt, nxt2 = w[i], at(i - 1), at(i + 1), at(i + 2)

        if c in V:
            if i == 0:
                out.append(c)
        elif c == "B":
            if not (prev == "M" and i == len(w) - 1):
                out.append("B")
        elif c == "C":
            if nxt == "H":
                out.append("X")
                i += 1
            elif nxt == "I" and nxt2 == "A":
                out.append("X")
            elif nxt in "IEY":
                out.append("S")
            else:
                out.append("K")
        elif c == "D":
            if nxt == "G" and nxt2 in "EIY":
                out.append("J")
                i += 2
            else:
                out.append("T")
        elif c == "F":
            out.append("F")
        elif c == "G":
            if nxt == "H":
                if nxt2 in V:
                    out.append("F")
                i += 1
            elif nxt == "N" and nxt2 not in V:
                pass
            elif nxt in "EIY":
                out.append("J")
            else:
                out.append("K")
        elif c == "H":
            if (prev not in V and nxt in V) or (prev in V and nxt in V):
                out.append("H")
        elif c in "JKLMNR":
            out.append(c)
        elif c == "P":
            if nxt == "H":
                out.append("F")
                i += 1
            else:
                out.append("P")
        elif c == "Q":
            out.append("K")
        elif c == "S":
            if nxt == "H" or (nxt == "I" and nxt2 in "AO"):
                out.append("X")
                if nxt == "H":
                    i += 1
            else:
                out.append("S")
        elif c == "T":
            if nxt == "I" and nxt2 in "AO":
                out.append("X")
            elif nxt == "H":
                out.append("0")
                i += 1
            elif not (nxt == "C" and nxt2 == "H"):
                out.append("T")
        elif c == "V":
            out.append("F")
        elif c == "W":
            if nxt in V:
                out.append("W")
        elif c == "X":
            out.append("KS")
        elif c == "Y":
            if nxt in V:
                out.append("Y")
        elif c == "Z":
            out.append("S")
        i += 1
    return "".join(out)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def ngrams(tokens: list[str], max_n: int = 5) -> list[str]:
    out: list[str] = []
    for n in range(1, min(max_n, len(tokens)) + 1):
        for i in range(0, len(tokens) - n + 1):
            out.append(" ".join(tokens[i : i + n]))
    return out


# ------------------------------------------------------------------- data
TM_DATABASE: list[tuple[str, str, str, str]] = [
    ("nike", "Nike, Inc.", "025", "critical"), ("just do it", "Nike, Inc.", "025", "critical"),
    ("jordan", "Nike, Inc. (Jumpman)", "025", "critical"), ("air jordan", "Nike, Inc.", "025", "critical"),
    ("adidas", "adidas AG", "025", "critical"), ("puma", "PUMA SE", "025", "critical"),
    ("new balance", "New Balance Athletics", "025", "critical"), ("under armour", "Under Armour, Inc.", "025", "critical"),
    ("reebok", "Reebok International", "025", "critical"), ("vans", "VF Corporation", "025", "critical"),
    ("converse", "Converse Inc.", "025", "critical"), ("champion", "Hanesbrands Inc.", "025", "critical"),
    ("carhartt", "Carhartt, Inc.", "025", "critical"), ("lululemon", "Lululemon Athletica", "025", "critical"),
    ("north face", "The North Face / VF", "025", "critical"), ("patagonia", "Patagonia, Inc.", "025", "critical"),
    ("supreme", "Chapter 4 Corp.", "025", "critical"), ("bape", "A Bathing Ape", "025", "critical"),
    ("off white", "Off-White LLC", "025", "critical"), ("gucci", "Guccio Gucci S.p.A.", "025", "critical"),
    ("louis vuitton", "LVMH", "025", "critical"), ("chanel", "Chanel, Inc.", "025", "critical"),
    ("prada", "PRADA S.p.A.", "025", "critical"), ("balenciaga", "Balenciaga SA", "025", "critical"),
    ("rolex", "Rolex SA", "025", "critical"), ("tommy hilfiger", "PVH Corp.", "025", "critical"),
    ("calvin klein", "PVH Corp.", "025", "critical"), ("levis", "Levi Strauss & Co.", "025", "critical"),
    ("levi's", "Levi Strauss & Co.", "025", "critical"), ("ralph lauren", "PRL USA Holdings", "025", "critical"),
    ("disney", "Disney Enterprises", "025", "critical"), ("mickey mouse", "Disney Enterprises", "025", "critical"),
    ("minnie mouse", "Disney Enterprises", "025", "critical"), ("marvel", "Marvel Characters, Inc.", "025", "critical"),
    ("avengers", "Marvel Characters, Inc.", "025", "critical"), ("spiderman", "Marvel Characters, Inc.", "025", "critical"),
    ("spider man", "Marvel Characters, Inc.", "025", "critical"), ("star wars", "Lucasfilm Ltd.", "025", "critical"),
    ("mandalorian", "Lucasfilm Ltd.", "025", "critical"), ("grogu", "Lucasfilm Ltd.", "025", "critical"),
    ("baby yoda", "Lucasfilm Ltd.", "025", "critical"), ("darth vader", "Lucasfilm Ltd.", "025", "critical"),
    ("pokemon", "Nintendo / Creatures / GF", "025", "critical"), ("pikachu", "Nintendo / Creatures / GF", "025", "critical"),
    ("nintendo", "Nintendo Co., Ltd.", "025", "critical"), ("super mario", "Nintendo Co., Ltd.", "025", "critical"),
    ("zelda", "Nintendo Co., Ltd.", "025", "critical"), ("hello kitty", "Sanrio Co., Ltd.", "025", "critical"),
    ("sanrio", "Sanrio Co., Ltd.", "025", "critical"), ("barbie", "Mattel, Inc.", "025", "critical"),
    ("hot wheels", "Mattel, Inc.", "025", "critical"), ("harry potter", "Warner Bros.", "025", "critical"),
    ("hogwarts", "Warner Bros.", "025", "critical"), ("gryffindor", "Warner Bros.", "025", "critical"),
    ("fortnite", "Epic Games, Inc.", "025", "critical"), ("minecraft", "Mojang / Microsoft", "025", "critical"),
    ("roblox", "Roblox Corporation", "025", "critical"), ("among us", "Innersloth LLC", "025", "critical"),
    ("peppa pig", "Hasbro / eOne", "025", "critical"), ("paw patrol", "Spin Master Ltd.", "025", "critical"),
    ("bluey", "Ludo Studio / BBC", "025", "critical"), ("cocomelon", "Moonbug Entertainment", "025", "critical"),
    ("spongebob", "Paramount (Nickelodeon)", "025", "critical"), ("squid game", "Netflix, Inc.", "025", "critical"),
    ("stranger things", "Netflix, Inc.", "025", "critical"), ("wednesday addams", "Netflix", "025", "critical"),
    ("simpsons", "20th Television (Disney)", "025", "critical"), ("family guy", "20th Television", "025", "critical"),
    ("rick and morty", "Warner Bros. Discovery", "025", "critical"), ("one piece", "Toei / Shueisha", "025", "critical"),
    ("naruto", "Shueisha / TV Tokyo", "025", "critical"), ("dragon ball", "Toei / Shueisha", "025", "critical"),
    ("studio ghibli", "Studio Ghibli, Inc.", "025", "critical"), ("acotar", "Sarah J. Maas", "025", "high"),
    ("fourth wing", "Rebecca Yarros / Entangled", "025", "high"),
    ("taylor swift", "TAS Rights Management", "025", "critical"), ("swiftie", "TAS Rights Management", "025", "critical"),
    ("eras tour", "TAS Rights Management", "025", "critical"), ("beyonce", "Parkwood", "025", "critical"),
    ("drake", "OVO", "025", "high"), ("weeknd", "The Weeknd / XO", "025", "high"), ("billie eilish", "Universal", "025", "high"),
    ("nba", "NBA Properties, Inc.", "025", "critical"), ("nfl", "NFL Properties LLC", "025", "critical"),
    ("super bowl", "NFL Properties LLC", "025", "critical"), ("mlb", "Major League Baseball", "025", "critical"),
    ("nhl", "National Hockey League", "025", "critical"), ("nascar", "NASCAR, Inc.", "025", "critical"),
    ("fifa", "FIFA", "025", "critical"), ("world cup", "FIFA", "025", "critical"),
    ("olympics", "USOC / IOC", "025", "critical"), ("olympic", "USOC / IOC", "025", "critical"),
    ("premier league", "The FA PL", "025", "critical"), ("champions league", "UEFA", "025", "critical"),
    ("manchester united", "Manchester United Ltd.", "025", "critical"), ("lakers", "Los Angeles Lakers", "025", "critical"),
    ("yankees", "NY Yankees Partnership", "025", "critical"), ("dodgers", "Los Angeles Dodgers LLC", "025", "critical"),
    ("real madrid", "Real Madrid C.F.", "025", "critical"), ("wwe", "WWE", "025", "critical"), ("ufc", "Zuffa, LLC", "025", "critical"),
    ("harley davidson", "H-D U.S.A., LLC", "025", "critical"), ("ford", "Ford Motor Company", "025", "high"),
    ("mustang", "Ford Motor Company", "025", "high"), ("chevrolet", "General Motors LLC", "025", "high"),
    ("jeep", "FCA US LLC", "025", "critical"), ("tesla", "Tesla, Inc.", "025", "critical"),
    ("john deere", "Deere & Company", "025", "critical"), ("caterpillar", "Caterpillar Inc.", "025", "critical"),
    ("coca cola", "The Coca-Cola Company", "025", "critical"), ("coke", "The Coca-Cola Company", "025", "high"),
    ("pepsi", "PepsiCo, Inc.", "025", "high"), ("starbucks", "Starbucks Corporation", "025", "critical"),
    ("dunkin", "Dunkin' Brands", "025", "high"), ("mcdonalds", "McDonald's Corporation", "025", "critical"),
    ("lego", "LEGO Juris A/S", "025", "critical"), ("nerf", "Hasbro, Inc.", "025", "critical"),
    ("monopoly", "Hasbro, Inc.", "025", "high"), ("stanley cup", "Stanley / NHL", "025", "high"),
    ("yeti", "YETI Coolers, LLC", "025", "high"), ("hydro flask", "Helen of Troy", "025", "high"),
    ("nasa", "NASA media guidelines", "N/A", "caution"),
    ("us army", "Dept. of the Army", "025", "high"), ("marines", "USMC Trademark Office", "025", "high"),
    ("air force", "Dept. of the Air Force", "025", "high"), ("fda approved", "Misleading claim policy", "N/A", "caution"),
    ("girl dad", "Registered phrase (contested)", "025", "caution"), ("mama bear", "Registered phrase (contested)", "025", "caution"),
]

POLICY_WORDS = [
    "t-shirt", "t shirt", "tshirt", "tee", "shirt", "shirts", "hoodie", "hoodies",
    "sweatshirt", "tank top", "long sleeve", "apparel", "clothing", "gift", "gifts",
    "present", "premium quality", "high quality", "best quality", "best seller",
    "bestseller", "#1", "free shipping", "sale", "discount", "cheap", "guaranteed", "100%",
]

_CAMEL: list[tuple[str, str, str, str, str, list[str], list[str]]] = []
for _term, _owner, _cls, _risk in TM_DATABASE:
    _norm = normalize(_term)
    _toks = _norm.split()
    _CAMEL.append((_term, _owner, _cls, _risk, _norm, _toks, [metaphone(t) for t in _toks]))

POLICY_REASON = {
    **{p: "Garment word banned in MBA title/bullets" for p in ["t-shirt", "t shirt", "tshirt", "tee", "shirt", "shirts", "hoodie", "hoodies", "sweatshirt", "tank top", "long sleeve", "apparel", "clothing"]},
    **{p: "Policy: promotional word" for p in ["gift", "gifts", "present", "sale", "discount"]},
    **{p: "Policy: unverifiable claim" for p in ["premium quality", "high quality", "best quality", "guaranteed", "100%"]},
    **{p: "Policy: rank claim" for p in ["best seller", "bestseller", "#1"]},
    **{p: "Policy: pricing claim" for p in ["cheap"]},
    "free shipping": "Policy: shipping claim",
}


def _esc(s: str) -> str:
    return re.escape(s)


def _weight(risk: str, kind: str) -> int:
    base = {"critical": 95, "high": 72, "caution": 35}[risk]
    if kind == "phonetic":
        return {"critical": 82, "high": 60, "caution": 30}[risk]
    if kind == "near":
        return {"critical": 68, "high": 55, "caution": 28}[risk]
    return base


def analyze_text(raw: str) -> dict:
    norm = normalize(raw)
    tokens = norm.split()
    grams = set(ngrams(tokens, 5))
    matches: dict[str, dict] = {}
    removed: list[str] = []

    # policy phrases
    for p in POLICY_WORDS:
        pn = normalize(p)
        if not pn:
            continue
        m = re.search(rf"(^|[^a-z0-9])({_esc(pn)})(?![a-z0-9])", norm)
        if m:
            removed.append(p)
            matches[f"policy:{pn}"] = {
                "fragment": m.group(2), "matchedTerm": p, "owner": "Amazon Content Policy",
                "tmClass": "N/A", "type": "policy", "severity": "policy", "weight": 25,
                "detail": POLICY_REASON.get(p, "Policy word"),
            }

    # TM dictionary
    for term, owner, cls, risk, tnorm, ttoks, tcodes in _CAMEL:
        if not ttoks:
            continue
        if len(ttoks) == 1:
            single = ttoks[0]
            for t in tokens:
                if t == single:
                    matches[f"exact:{tnorm}"] = {
                        "fragment": t, "matchedTerm": term, "owner": owner, "tmClass": cls,
                        "type": "exact", "severity": risk, "weight": _weight(risk, "exact"),
                        "detail": "Exact match on a %s mark" % ("registered" if risk == "critical" else "watched"),
                    }
                    break
                if risk != "caution":
                    if len(t) >= 4 and levenshtein(t, single) == 1:
                        matches[f"near:{tnorm}:{t}"] = {
                            "fragment": t, "matchedTerm": term, "owner": owner, "tmClass": cls,
                            "type": "near", "severity": risk, "weight": _weight(risk, "near"),
                            "detail": f'Edit distance 1 from "{term}" — likely evasive misspelling',
                        }
                    elif len(t) >= 3 and metaphone(t) == tcodes[0] and t != single:
                        matches[f"phon:{tnorm}:{t}"] = {
                            "fragment": t, "matchedTerm": term, "owner": owner, "tmClass": cls,
                            "type": "phonetic", "severity": risk, "weight": _weight(risk, "phonetic"),
                            "detail": f'Phonetic alias of "{term}" (Metaphone {tcodes[0]})',
                        }
        else:
            n = len(ttoks)
            if tnorm in grams:
                matches[f"exact:{tnorm}"] = {
                    "fragment": tnorm, "matchedTerm": term, "owner": owner, "tmClass": cls,
                    "type": "exact", "severity": risk, "weight": _weight(risk, "exact"),
                    "detail": f"Exact {n}-gram match on a registered phrase mark",
                }
                continue
            if risk == "caution":
                continue
            for i in range(0, len(tokens) - n + 1):
                win = tokens[i : i + n]
                if " ".join(win) == tnorm:
                    continue
                if all(metaphone(x) == tcodes[k] for k, x in enumerate(win)):
                    matches[f"phon:{tnorm}:{' '.join(win)}"] = {
                        "fragment": " ".join(win), "matchedTerm": term, "owner": owner, "tmClass": cls,
                        "type": "phonetic", "severity": risk, "weight": _weight(risk, "phonetic"),
                        "detail": f'Phonetic phrase alias of "{term}"',
                    }
                    break

    # sanitizer
    sanitized = raw
    for r in removed:
        phrase_re = re.escape(normalize(r)).replace(r"\ ", "[\\s-]*")
        sanitized = re.sub(phrase_re, "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\s*-\s*-\s*", " - ", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized)
    sanitized = re.sub(r"\s+([,.!?:;])", r"\1", sanitized).strip(" ,\t\n")

    matches_list = sorted(matches.values(), key=lambda m: -m["weight"])
    maxw = matches_list[0]["weight"] if matches_list else 0
    highs = len([m for m in matches_list if m["weight"] >= 50])
    score = min(100, maxw + (8 if highs > 1 else 0))
    band = "BLOCKED" if score >= 70 else "HIGH RISK" if score >= 45 else "CAUTION" if score >= 20 else "CLEAR"
    return {
        "score": score, "band": band, "matches": matches_list,
        "sanitized": sanitized, "removed": removed, "scannedGrams": len(grams),
    }
