"""
MerchFlow Pro — offline trend engine for the desktop build.
Mirrors src/lib/trends-engine.ts: deterministic opportunity scoring + seeded
recon datasets. Live Amazon completion is best-effort (offline-safe) and never
required for the UI to function.
"""
from __future__ import annotations

import urllib.parse

import httpx

SUFFIX_CORPUS = [
    "", " lover", " for men", " for women", " funny", " vintage", " retro",
    " mama", " dad", " club", " aesthetic", " graphic", " cute", " saying",
]
PREFIX_WORDS = ["funny", "vintage", "retro", "cute", "cool", "aesthetic", "kawaii", "distressed", "skeleton", "floral"]

MOVERS = [
    {"keyword": "pickleball skeleton retro", "bsrNow": 12410, "bsrPrev": 48200, "delta": 74, "opportunity": 91, "isMerch": True},
    {"keyword": "fishing bass american flag", "bsrNow": 8932, "bsrPrev": 22880, "delta": 61, "opportunity": 84, "isMerch": True},
    {"keyword": "funny welder skull", "bsrNow": 20775, "bsrPrev": 49362, "delta": 58, "opportunity": 78, "isMerch": True},
    {"keyword": "axolotl strawberry kawaii", "bsrNow": 15420, "bsrPrev": 33910, "delta": 55, "opportunity": 87, "isMerch": True},
    {"keyword": "groovy disco roller skate", "bsrNow": 30144, "bsrPrev": 61770, "delta": 51, "opportunity": 72, "isMerch": True},
    {"keyword": "wu tang style crane origami", "bsrNow": 44210, "bsrPrev": 81544, "delta": 46, "opportunity": 41, "isMerch": False},
    {"keyword": "cottagecore frog mushroom", "bsrNow": 11986, "bsrPrev": 21003, "delta": 43, "opportunity": 89, "isMerch": True},
    {"keyword": "vintage sunset mountains camping", "bsrNow": 9834, "bsrPrev": 16890, "delta": 42, "opportunity": 76, "isMerch": True},
    {"keyword": "cowboy frog yeehaw", "bsrNow": 26550, "bsrPrev": 44019, "delta": 40, "opportunity": 81, "isMerch": True},
    {"keyword": "nurse caffeine heartbeat", "bsrNow": 33202, "bsrPrev": 52876, "delta": 37, "opportunity": 66, "isMerch": True},
]

ETSY = [
    {"title": "Retro Sunset Highland Cow PNG — Western Vintage Graphic", "price": 3.40, "estSales": 1284, "rating": 4.9, "bestseller": True,
     "tags": ["highland cow png","western png","retro sunset","cow graphic","vintage cow","heifer png","cow lover","farm png","sublimation","tshirt design","country girl","boho cow","ranch life"]},
    {"title": "Funny Pickleball Skeleton Dink Responsibly Design", "price": 2.90, "estSales": 1107, "rating": 4.8, "bestseller": True,
     "tags": ["pickleball png","skeleton pickleball","dink","funny pickleball","pickleball lover","pickleball player","court png","paddle design","sports png","retro sports","pickle addict","kitchen line","gifts for players"]},
    {"title": "Kawaii Cottagecore Frog on Mushroom Cottage Graphic", "price": 3.10, "estSales": 986, "rating": 5.0, "bestseller": True,
     "tags": ["frog png","cottagecore","kawaii frog","mushroom png","goblincore","cute frog","frog lover","nature png","fairycore","aesthetic png","pond life","funny frog","tshirt graphic"]},
    {"title": "Distressed American Flag Bass Fishing Vintage Emblem", "price": 2.50, "estSales": 902, "rating": 4.7, "bestseller": True,
     "tags": ["bass fishing png","fishing dad","american flag fish","vintage fishing","angler png","largemouth bass","fisherman","lake life","fishing graphic","reel cool","dad png","outdoors png","fisher gift"]},
    {"title": "Groovy Smiley Lightning Bolt — Retro 70s Wavy Lettering", "price": 3.80, "estSales": 744, "rating": 4.9, "bestseller": True,
     "tags": ["groovy png","retro 70s","wavy letters","smiley lightning","checkered png","boho png","good vibes","vintage smile","disco png","funky design","tshirt png","retro font","summer png"]},
    {"title": "Nurse Fuel Caffeine Heartbeat ECG Line Art Design", "price": 2.70, "estSales": 688, "rating": 4.8, "bestseller": True,
     "tags": ["nurse png","nurse life","coffee nurse","ecg heartbeat","nursing student","rn png","medical png","funny nurse","scrub life","night shift","healthcare","stethoscope","nurse week"]},
]

PIN_BOARDS = [
    {"name": "70s Retro Wavy Type", "style": "Groovy serif, wavy baselines, sunset gradients", "trendScore": 94, "weeklyPins": 18400, "palette": ["#E8632B","#F2A93B","#8B5E34","#F6E3C5"]},
    {"name": "Distressed Vintage Badge", "style": "Circular emblems, cracked ink texture, 2-color", "trendScore": 88, "weeklyPins": 12900, "palette": ["#2B2B2B","#C8B06E","#7A4E2D","#EFE6D5"]},
    {"name": "Kawaii Vector Critters", "style": "Thick outline, candy palette, rounded forms", "trendScore": 91, "weeklyPins": 15700, "palette": ["#FF8FB1","#7ED3B2","#FFF1F6","#3B3B58"]},
    {"name": "Single-Weight Linework", "style": "Fine-line illustration, celestial + botanical", "trendScore": 82, "weeklyPins": 9800, "palette": ["#111111","#F5F2EA","#C9A96A","#5C6672"]},
    {"name": "Y2K Chrome & Airbrush", "style": "Liquid chrome, starbursts, airbrush fades", "trendScore": 86, "weeklyPins": 11200, "palette": ["#B9C7FF","#F4F6FF","#7C5AFF","#23233F"]},
    {"name": "Western Cowboy Grit", "style": "Longhorn skulls, desert tones, rope borders", "trendScore": 90, "weeklyPins": 14100, "palette": ["#8A4B2B","#D9B77C","#40342A","#F1E7D6"]},
    {"name": "Coastal Granddaughter", "style": "Preppy stripes, lobsters, soft maritime hues", "trendScore": 79, "weeklyPins": 8600, "palette": ["#9FBFDF","#FDFBF7","#E56B6F","#355070"]},
    {"name": "Dark Academia Occult", "style": "Moths, moons, antique engraving texture", "trendScore": 84, "weeklyPins": 10400, "palette": ["#1B1B22","#B99A55","#6E3B3B","#E6DFD3"]},
]


def _rng(seed_bytes: bytes):
    h = 2166136261
    for b in seed_bytes:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    state = [h]

    def rnd() -> float:
        x = state[0]
        x = (x + 0x6D2B79F5) & 0xFFFFFFFF
        t = (x ^ (x >> 15)) * (1 | x) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (61 | t) & 0xFFFFFFFF)) & 0xFFFFFFFF
        t = (t ^ (t >> 14)) & 0xFFFFFFFF
        state[0] = x
        return t / 4294967296

    return rnd


def score_keyword(keyword: str) -> dict:
    rnd = _rng(keyword.strip().lower().encode())
    words = len(keyword.split())
    demand = round(max(28, min(99, 92 - words * 6 + rnd() * 24)))
    competition = round(18 + rnd() * 74)
    opportunity = round(max(6, min(97, demand * 0.92 + (100 - competition) * 0.5 - 24)))
    return {"demand": demand, "competition": competition, "opportunity": opportunity}


async def get_suggestions(q: str) -> dict:
    q = q.strip()[:80]
    if not q:
        return {"error": "q is required"}
    live = False
    kws: list[str] = []
    try:
        url = (
            "https://completion.amazon.com/api/2017/suggestions?limit=11&prefix="
            + urllib.parse.quote(q)
            + "&suggestion-type=KEYWORD&page-type=Gateway&alias=aps&site-variant=desktop&version=2"
            "&event=onKeyPress&wc=&lop=en_US&last-prefix=0&avg-ks-time=0&fb=1&mid=ATVPDKIKX0DER&plain-mid=1"
        )
        r = await httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
            timeout=4.5,
        ).get(url)
        if r.status_code == 200:
            data = r.json()
            kws = [s["value"] for s in (data.get("suggestions") or []) if s.get("value")]
            live = len(kws) > 0
    except Exception:
        live = False

    if not live:
        base = q.lower()
        kws = sorted({(base + s).strip() for s in SUFFIX_CORPUS} | {f"{p} {base}" for p in PREFIX_WORDS[:4]}, key=len)[:11]

    seen = set()
    items = []
    for k in kws:
        kl = k.lower()
        if kl in seen:
            continue
        seen.add(kl)
        items.append({"keyword": k, "fromCompletion": live, **score_keyword(k)})
    items.sort(key=lambda x: -x["opportunity"])
    return {"source": "live:completion.amazon.com" if live else "simulated:a-z-expansion", "items": items}


def movers() -> dict:
    return {"source": "seed:node-21393915011", "items": MOVERS}


def etsy() -> dict:
    return {"source": "recon:etsy-search", "items": ETSY}


def pinterest() -> dict:
    return {"source": "recon:pinterest-visual", "items": PIN_BOARDS}
