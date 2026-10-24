"""
MerchFlow Pro — unbreakable SQLite persistence for the desktop build.

Zero external services: a single file DB living in ~/MerchFlow/merchflow.db
(override with MERCHFLOW_DB). Used by the static-export desktop runtime so the
dashboard runs with NO PostgreSQL and NO Node.js.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("MERCHFLOW_DB") or (Path.home() / "MerchFlow" / "merchflow.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT 'chrome',
  status TEXT NOT NULL DEFAULT 'ready',
  daily_used INTEGER NOT NULL DEFAULT 0,
  daily_cap INTEGER NOT NULL DEFAULT 200,
  total_generated INTEGER NOT NULL DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  style_name TEXT NOT NULL DEFAULT '',
  niche_name TEXT NOT NULL DEFAULT '',
  aspect TEXT NOT NULL DEFAULT '4:5',
  background TEXT NOT NULL DEFAULT 'transparent',
  text_override TEXT NOT NULL DEFAULT '',
  account_label TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'done',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  niche TEXT NOT NULL DEFAULT '',
  brand TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  bullet1 TEXT NOT NULL DEFAULT '',
  bullet2 TEXT NOT NULL DEFAULT '',
  tp_title TEXT NOT NULL DEFAULT '',
  tp_description TEXT NOT NULL DEFAULT '',
  main_tag TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  risk_score INTEGER NOT NULL DEFAULT 0,
  design_id TEXT NOT NULL DEFAULT '',
  design_path TEXT NOT NULL DEFAULT '',
  created_at REAL
);
CREATE TABLE IF NOT EXISTS licenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  plan TEXT NOT NULL DEFAULT 'pro',
  status TEXT NOT NULL DEFAULT 'active',
  hwid TEXT,
  seats INTEGER NOT NULL DEFAULT 10,
  created_at REAL
);
"""

DEMO_KEYS = {
    "MF-PRO-DEMO-2026-0001": {"plan": "pro", "seats": 10},
    "MF-TEAM-DEMO-2026-0002": {"plan": "team", "seats": 25},
    "MF-STARTER-DEMO-2026-0003": {"plan": "starter", "seats": 2},
}

# Accounts are NEVER seeded: the Account Manager starts with 0 accounts and
# grows only through a real "+ Add Gemini Account" sign-in. These identities
# were seeded by earlier builds; init() purges them from existing databases.
LEGACY_SEED_EMAILS = (
    "merchflow.alpha@gmail.com", "mf.bravo.studio@gmail.com", "charlie.prints.mf@gmail.com",
    "delta.merchlab@gmail.com", "echo.podworks@gmail.com",
)
LEGACY_SEED_JOBS = (
    ("Retro Wavy", "Pickleball"), ("Kawaii", "Axolotl"),
    ("Distressed Badge", "Welder Skull"), ("Linework", "Celestial Moth"),
)

SEED_LISTINGS = [
    (
        "bass fishing", "Fishing Culture Co", "Vintage Bass Fishing Design",
        "Striking vintage bass fishing artwork with distressed badge styling — made for men and women who live and breathe everything fishing. A conversation-starting graphic that pops on any base colorway.",
        "Pairs effortlessly with denim, joggers or a flannel layer. Ideal for birthdays, holidays and weekend hangouts. A clean, expressive look for anglers.",
        "Bass Fishing | Vintage Graphic Design",
        "Bass fishing design in a vintage style with distressed badge. Perfect for anglers who love the lake.",
        "bass fishing",
        "bass fishing, fishing lover, fishing design, vintage fishing, funny fishing, fishing art, fishing gift, retro fishing, angler, lake life",
        0, 1860,
    ),
    (
        "roller skating", "Skating Culture Co", "Groovy Roller Skating Design",
        "Striking groovy roller skating artwork with 70s sunset palette — made for men and women who live and breathe everything skating.",
        "Pairs effortlessly with denim, joggers or a flannel layer. Ideal for game nights, parties and everyday wear.",
        "Roller Skating | Groovy Graphic Design",
        "Roller skating design in a groovy 70s style with sunset palette. Perfect for skaters.",
        "roller skating",
        "roller skating, skating lover, skating design, groovy skating, funny skating, skating art, skating gift, vintage skating, retro skating, disco skating",
        0, 3960,
    ),
]


def conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    fresh = not DB_PATH.exists()
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        columns = {row["name"] for row in c.execute("PRAGMA table_info(listings)")}
        for name in ("design_id", "design_path"):
            if name not in columns:
                c.execute(f"ALTER TABLE listings ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        account_columns = {row["name"] for row in c.execute("PRAGMA table_info(accounts)")}
        if "profile_dir" not in account_columns:
            c.execute("ALTER TABLE accounts ADD COLUMN profile_dir TEXT NOT NULL DEFAULT ''")
        if "engine" not in account_columns:
            c.execute("ALTER TABLE accounts ADD COLUMN engine TEXT NOT NULL DEFAULT ''")
        # Purge the fake demo accounts (and their fake job history) from older installs.
        marks = ",".join("?" * len(LEGACY_SEED_EMAILS))
        c.execute(f"DELETE FROM accounts WHERE profile_dir='' AND email IN ({marks})", LEGACY_SEED_EMAILS)
        for style, niche in LEGACY_SEED_JOBS:
            c.execute("DELETE FROM jobs WHERE style_name=? AND niche_name=? AND account_label LIKE 'Profile 0%'", (style, niche))
        if fresh and c.execute("SELECT COUNT(*) AS n FROM listings").fetchone()["n"] == 0:
            for row in SEED_LISTINGS:  # sample listing copy only; never accounts
                c.execute(
                    "INSERT INTO listings (niche,brand,title,bullet1,bullet2,tp_title,tp_description,main_tag,tags,risk_score,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (*row[:-1], time.time() - row[-1]),
                )

# ------------------------------------------------------------------ helpers

def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with _lock, conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def list_accounts() -> list[dict]:
    rows = _rows("SELECT * FROM accounts ORDER BY id ASC")
    return [
        {
            "id": r["id"], "label": r["label"], "email": r["email"], "provider": r["provider"],
            "status": r["status"], "dailyUsed": r["daily_used"], "dailyCap": r["daily_cap"],
            "totalGenerated": r["total_generated"], "profileDir": r["profile_dir"], "engine": r["engine"],
        }
        for r in rows
    ]


def list_cdp_accounts() -> list[dict]:
    """Accounts backed by a persistent isolated Chrome profile (headless CDP engine)."""
    return _rows("SELECT * FROM accounts WHERE engine='cdp' AND profile_dir!='' ORDER BY id ASC")


def upsert_gemini_account(account_id: int, label: str, email: str, provider: str, profile_dir: str) -> dict:
    """Record a signed-in Gemini profile; the id is the profile slot (account_<id>)."""
    with _lock, conn() as c:
        c.execute(
            """INSERT INTO accounts (id,label,email,provider,status,profile_dir,engine,created_at)
               VALUES (?,?,?,?,'ready',?,'cdp',?)
               ON CONFLICT(id) DO UPDATE SET label=excluded.label,
                 email=CASE WHEN excluded.email!='' THEN excluded.email ELSE accounts.email END,
                 provider=excluded.provider, status='ready', profile_dir=excluded.profile_dir, engine='cdp'""",
            (int(account_id), label[:40], email[:120], provider[:20], profile_dir[:1000], time.time()),
        )
        return {"ok": True, "item": {"id": int(account_id)}}


def delete_account(account_id: int) -> dict:
    with _lock, conn() as c:
        cur = c.execute("DELETE FROM accounts WHERE id=?", (int(account_id),))
        return {"ok": True, "deleted": cur.rowcount}


def add_account(label: str, email: str, provider: str) -> dict:
    with _lock, conn() as c:
        cur = c.execute(
            "INSERT INTO accounts (label,email,provider,status,created_at) VALUES (?,?,?,?,?)",
            (label[:40], email[:120], provider[:20], "auth-required", time.time()),
        )
        return {"ok": True, "item": {"id": cur.lastrowid}}


def patch_account(account_id: int, status: str | None = None) -> dict:
    with _lock, conn() as c:
        if status:
            c.execute("UPDATE accounts SET status=? WHERE id=?", (status[:20], account_id))
        return {"ok": True}


def list_jobs(limit: int = 24) -> list[dict]:
    return _rows("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))


def add_job(b: dict) -> dict:
    with _lock, conn() as c:
        c.execute(
            "INSERT INTO jobs (style_name,niche_name,aspect,background,text_override,account_label,status,duration_ms,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(b.get("styleName", ""))[:120], str(b.get("nicheName", ""))[:120],
                str(b.get("aspect", "4:5"))[:8], str(b.get("background", "transparent"))[:20],
                str(b.get("textOverride", ""))[:120], str(b.get("accountLabel", ""))[:40],
                str(b.get("status", "done"))[:12],
                min(600000, max(0, int(b.get("durationMs", 0) or 0))), time.time(),
            ),
        )
        return {"ok": True}


def list_listings(limit: int = 40) -> list[dict]:
    rows = _rows("SELECT * FROM listings ORDER BY id DESC LIMIT ?", (limit,))
    return [
        {**{k: v for k, v in r.items()},
         "tpTitle": r["tp_title"], "tpDescription": r["tp_description"],
         "mainTag": r["main_tag"], "riskScore": r["risk_score"], "createdAt": r["created_at"]}
        for r in rows
    ]


def add_listing(b: dict) -> dict:
    s = lambda v, n: str(v if v is not None else "")[:n]
    with _lock, conn() as c:
        cur = c.execute(
            "INSERT INTO listings (niche,brand,title,bullet1,bullet2,tp_title,tp_description,main_tag,tags,risk_score,design_id,design_path,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s(b.get("niche"), 120), s(b.get("brand"), 60), s(b.get("title"), 120),
                s(b.get("bullet1"), 512), s(b.get("bullet2"), 512), s(b.get("tpTitle"), 120),
                s(b.get("tpDescription"), 640), s(b.get("mainTag"), 80), s(b.get("tags"), 640),
                min(100, max(0, int(b.get("riskScore", 0) or 0))),
                s(b.get("designId"), 100), s(b.get("designPath"), 2000), time.time(),
            ),
        )
        row = c.execute("SELECT * FROM listings WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"ok": True, "item": {**dict(row), "riskScore": row["risk_score"],
                "tpTitle": row["tp_title"], "tpDescription": row["tp_description"],
                "mainTag": row["main_tag"], "createdAt": row["created_at"]}}


def license_verify(key: str, hwid: str) -> dict | None:
    """SQLite-first license check; None means fall back to demo keys."""
    with _lock, conn() as c:
        row = c.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if row is None:
            c.execute("SELECT COUNT(*) AS n FROM licenses").fetchone()
            if DEMO_KEYS.get(key):
                c.execute(
                    "INSERT OR IGNORE INTO licenses (key,plan,status,seats,created_at) VALUES (?,?,?,?,?)",
                    (key, DEMO_KEYS[key]["plan"], "active", DEMO_KEYS[key]["seats"], time.time()),
                )
                row = c.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        r = dict(row)
        if r["status"] != "active":
            return {"valid": False, "reason": "License revoked. Contact support."}
        if r["hwid"] and hwid and r["hwid"] != hwid:
            return {"valid": False, "reason": "HWID lock: key already bound to another machine."}
        if not r["hwid"] and hwid:
            c.execute("UPDATE licenses SET hwid=? WHERE id=?", (hwid, r["id"]))
        return {"valid": True, "plan": r["plan"], "seats": r["seats"], "bound": True, "source": "sqlite"}


init()
