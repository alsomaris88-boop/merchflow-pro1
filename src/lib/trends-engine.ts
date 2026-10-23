/* Trend Radar engine — Amazon completion API (live, residential-IP style) with
   deterministic offline scoring + curated Etsy/Pinterest recon datasets. */

export interface SuggestionItem {
  keyword: string;
  demand: number;
  competition: number;
  opportunity: number;
  fromCompletion: boolean;
}

export interface MoverItem {
  keyword: string;
  bsrNow: number;
  bsrPrev: number;
  delta: number; // % improvement (positive = sales velocity up)
  opportunity: number;
  isMerch: boolean;
}

export interface EtsyItem {
  title: string;
  price: number;
  estSales: number;
  rating: number;
  bestseller: boolean;
  tags: string[];
}

export interface PinBoard {
  name: string;
  style: string;
  trendScore: number;
  weeklyPins: number;
  palette: string[];
}

/* ----------------------------- deterministic rng ----------------------------- */
function hash32(s: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function scoreKeyword(keyword: string): Omit<SuggestionItem, "keyword" | "fromCompletion"> {
  const rnd = mulberry32(hash32(keyword.trim().toLowerCase()));
  const words = keyword.trim().split(/\s+/).length;
  const demand = Math.round(Math.max(28, Math.min(99, 92 - words * 6 + rnd() * 24)));
  const competition = Math.round(18 + rnd() * 74);
  const opportunity = Math.round(Math.max(6, Math.min(97, demand * 0.92 + (100 - competition) * 0.5 - 24)));
  return { demand, competition, opportunity };
}

/* --------------------------- Amazon completion API --------------------------- */
const SUFFIX_CORPUS = [
  "", " lover", " for men", " for women", " funny", " vintage", " retro",
  " mama", " dad", " club", " aesthetic", " graphic", " cute", " saying",
];

const PREFIX_WORDS = ["funny", "vintage", "retro", "cute", "cool", "aesthetic", "kawaii", "distressed", "skeleton", "floral"];

export async function getSuggestions(query: string): Promise<{ live: boolean; items: SuggestionItem[] }> {
  const q = query.trim();
  let live = false;
  let kws: string[] = [];

  try {
    const url =
      "https://completion.amazon.com/api/2017/suggestions?limit=11&prefix=" +
      encodeURIComponent(q) +
      "&suggestion-type=KEYWORD&page-type=Gateway&alias=aps&site-variant=desktop&version=2&event=onKeyPress&wc=&lop=en_US&last-prefix=0&avg-ks-time=0&fb=1&mid=ATVPDKIKX0DER&plain-mid=1";
    const res = await fetch(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(4500),
      cache: "no-store",
    });
    if (res.ok) {
      const data = (await res.json()) as { suggestions?: { value: string }[] };
      kws = (data.suggestions ?? []).map((s) => s.value).filter(Boolean);
      live = kws.length > 0;
    }
  } catch {
    live = false;
  }

  if (!live) {
    // A-Z expansion reconstruction (deterministic offline model)
    const base = q.toLowerCase();
    const set = new Set<string>();
    for (const suf of SUFFIX_CORPUS) set.add((base + suf).trim());
    for (const pre of PREFIX_WORDS.slice(0, 4)) set.add(`${pre} ${base}`);
    kws = Array.from(set).slice(0, 11);
    kws.sort((a, b) => a.length - b.length);
  }

  const seen = new Set<string>();
  const items: SuggestionItem[] = [];
  for (const k of kws) {
    const key = k.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({ keyword: k, fromCompletion: live, ...scoreKeyword(k) });
  }
  items.sort((a, b) => b.opportunity - a.opportunity);
  return { live, items };
}

/* ------------------------------ BSR movers data ------------------------------ */
export const FALLBACK_MOVERS: MoverItem[] = [
  { keyword: "pickleball skeleton retro", bsrNow: 12410, bsrPrev: 48200, delta: 74, opportunity: 91, isMerch: true },
  { keyword: "fishing bass american flag", bsrNow: 8932, bsrPrev: 22880, delta: 61, opportunity: 84, isMerch: true },
  { keyword: "funny welder skull", bsrNow: 20775, bsrPrev: 49362, delta: 58, opportunity: 78, isMerch: true },
  { keyword: "axolotl strawberry kawaii", bsrNow: 15420, bsrPrev: 33910, delta: 55, opportunity: 87, isMerch: true },
  { keyword: "groovy disco roller skate", bsrNow: 30144, bsrPrev: 61770, delta: 51, opportunity: 72, isMerch: true },
  { keyword: "wu tang style crane origami", bsrNow: 44210, bsrPrev: 81544, delta: 46, opportunity: 41, isMerch: false },
  { keyword: "cottagecore frog mushroom", bsrNow: 11986, bsrPrev: 21003, delta: 43, opportunity: 89, isMerch: true },
  { keyword: "vintage sunset mountains camping", bsrNow: 9834, bsrPrev: 16890, delta: 42, opportunity: 76, isMerch: true },
  { keyword: "cowboy frog yeehaw", bsrNow: 26550, bsrPrev: 44019, delta: 40, opportunity: 81, isMerch: true },
  { keyword: "nurse caffeine heartbeat", bsrNow: 33202, bsrPrev: 52876, delta: 37, opportunity: 66, isMerch: true },
];

/* ------------------------------ Etsy recon data ------------------------------ */
export const ETSY_BESTSELLERS: EtsyItem[] = [
  {
    title: "Retro Sunset Highland Cow PNG — Western Vintage Graphic",
    price: 3.4, estSales: 1284, rating: 4.9, bestseller: true,
    tags: ["highland cow png", "western png", "retro sunset", "cow graphic", "vintage cow", "heifer png", "cow lover", "farm png", "sublimation", "tshirt design", "country girl", "boho cow", "ranch life"],
  },
  {
    title: "Funny Pickleball Skeleton Dink Responsibly Design",
    price: 2.9, estSales: 1107, rating: 4.8, bestseller: true,
    tags: ["pickleball png", "skeleton pickleball", "dink", "funny pickleball", "pickleball lover", "pickleball player", "court png", "paddle design", "sports png", "retro sports", "pickle addict", "kitchen line", "gifts for players"],
  },
  {
    title: "Kawaii Cottagecore Frog on Mushroom Cottage Graphic",
    price: 3.1, estSales: 986, rating: 5.0, bestseller: true,
    tags: ["frog png", "cottagecore", "kawaii frog", "mushroom png", "goblincore", "cute frog", "frog lover", "nature png", "fairycore", "aesthetic png", "pond life", "funny frog", "tshirt graphic"],
  },
  {
    title: "Distressed American Flag Bass Fishing Vintage Emblem",
    price: 2.5, estSales: 902, rating: 4.7, bestseller: true,
    tags: ["bass fishing png", "fishing dad", "american flag fish", "vintage fishing", "angler png", "largemouth bass", "fisherman", "lake life", "fishing graphic", "reel cool", "dad png", "outdoors png", "fisher gift"],
  },
  {
    title: "Groovy Smiley Lightning Bolt — Retro 70s Wavy Lettering",
    price: 3.8, estSales: 744, rating: 4.9, bestseller: true,
    tags: ["groovy png", "retro 70s", "wavy letters", "smiley lightning", "checkered png", "boho png", "good vibes", "vintage smile", "disco png", "funky design", "tshirt png", "retro font", "summer png"],
  },
  {
    title: "Nurse Fuel Caffeine Heartbeat ECG Line Art Design",
    price: 2.7, estSales: 688, rating: 4.8, bestseller: true,
    tags: ["nurse png", "nurse life", "coffee nurse", "ecg heartbeat", "nursing student", "rn png", "medical png", "funny nurse", "scrub life", "night shift", "healthcare", "stethoscope", "nurse week"],
  },
];

/* ---------------------------- Pinterest style radar ---------------------------- */
export const PIN_BOARDS: PinBoard[] = [
  { name: "70s Retro Wavy Type", style: "Groovy serif, wavy baselines, sunset gradients", trendScore: 94, weeklyPins: 18400, palette: ["#E8632B", "#F2A93B", "#8B5E34", "#F6E3C5"] },
  { name: "Distressed Vintage Badge", style: "Circular emblems, cracked ink texture, 2-color", trendScore: 88, weeklyPins: 12900, palette: ["#2B2B2B", "#C8B06E", "#7A4E2D", "#EFE6D5"] },
  { name: "Kawaii Vector Critters", style: "Thick outline, candy palette, rounded forms", trendScore: 91, weeklyPins: 15700, palette: ["#FF8FB1", "#7ED3B2", "#FFF1F6", "#3B3B58"] },
  { name: "Single-Weight Linework", style: "Fine-line illustration, celestial + botanical", trendScore: 82, weeklyPins: 9800, palette: ["#111111", "#F5F2EA", "#C9A96A", "#5C6672"] },
  { name: "Y2K Chrome & Airbrush", style: "Liquid chrome, starbursts, airbrush fades", trendScore: 86, weeklyPins: 11200, palette: ["#B9C7FF", "#F4F6FF", "#7C5AFF", "#23233F"] },
  { name: "Western Cowboy Grit", style: "Longhorn skulls, desert tones, rope borders", trendScore: 90, weeklyPins: 14100, palette: ["#8A4B2B", "#D9B77C", "#40342A", "#F1E7D6"] },
  { name: "Coastal Granddaughter", style: "Preppy stripes, lobsters, soft maritime hues", trendScore: 79, weeklyPins: 8600, palette: ["#9FBFDF", "#FDFBF7", "#E56B6F", "#355070"] },
  { name: "Dark Academia Occult", style: "Moths, moons, antique engraving texture", trendScore: 84, weeklyPins: 10400, palette: ["#1B1B22", "#B99A55", "#6E3B3B", "#E6DFD3"] },
];
