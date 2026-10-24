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
  status TEXT NOT NULL DEFAULT 'auth-required',
  daily_used INTEGER NOT NULL DEFAULT 0,
  daily_cap INTEGER NOT NULL DEFAULT 200,
  total_generated INTEGER NOT NULL DEFAULT 0,
  secure_1psid TEXT NOT NULL DEFAULT '',
  secure_1psidts TEXT NOT NULL DEFAULT '',
  login_flow_id TEXT NOT NULL DEFAULT '',
  verified_at REAL NOT NULL DEFAULT 0,
  is_pro INTEGER NOT NULL DEFAULT 0,
  window_usage INTEGER NOT NULL DEFAULT 0,
  window_started REAL NOT NULL DEFAULT 0,
  cooldown_until REAL NOT NULL DEFAULT 0,
  weekly_used INTEGER NOT NULL DEFAULT 0,
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

# Existing installations may contain unverified account metadata. New session
# columns distinguish real browser-linked credentials from that old data.
ACCOUNT_COLUMNS = {
    "secure_1psid": "TEXT NOT NULL DEFAULT ''",
    "secure_1psidts": "TEXT NOT NULL DEFAULT ''",
    "login_flow_id": "TEXT NOT NULL DEFAULT ''",
    "verified_at": "REAL NOT NULL DEFAULT 0",
    "is_pro": "INTEGER NOT NULL DEFAULT 0",
    "window_usage": "INTEGER NOT NULL DEFAULT 0",
    "window_started": "REAL NOT NULL DEFAULT 0",
    "cooldown_until": "REAL NOT NULL DEFAULT 0",
    "weekly_used": "INTEGER NOT NULL DEFAULT 0",
}


def conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    c = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    os.chmod(DB_PATH, 0o600)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        columns = {row["name"] for row in c.execute("PRAGMA table_info(listings)")}
        for name in ("design_id", "design_path"):
            if name not in columns:
                c.execute(f"ALTER TABLE listings ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        columns = {row["name"] for row in c.execute("PRAGMA table_info(accounts)")}
        for name, definition in ACCOUNT_COLUMNS.items():
            if name not in columns:
                c.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
        # Never import the former JSON list or trust metadata-only rows. Real
        # designs/jobs/listings are deliberately not deleted by this migration.
        c.execute("DELETE FROM accounts WHERE verified_at<=0 OR login_flow_id='' OR length(trim(secure_1psid))<20 OR trim(email)=''")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS verified_account_email ON accounts(lower(email))")

# ------------------------------------------------------------------ helpers

def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with _lock, conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def session_rows() -> list[dict]:
    """Private pool-only read. Never return these credential-bearing rows over HTTP."""
    return _rows("SELECT * FROM accounts WHERE verified_at>0 AND login_flow_id!='' AND length(trim(secure_1psid))>=20 AND trim(email)!='' ORDER BY id")


def list_accounts() -> list[dict]:
    # Explicit projection: session credentials and login-flow identifiers must
    # never escape into browser responses, health payloads or logs.
    return [
        {"id": r["id"], "label": r["label"], "email": r["email"], "provider": r["provider"],
         "status": r["status"], "dailyUsed": r["daily_used"], "dailyCap": r["daily_cap"],
         "totalGenerated": r["total_generated"], "verifiedAt": r["verified_at"],
         "authSource": "browser-login", "hasCookies": True}
        for r in session_rows()
    ]


def next_account_id() -> int:
    rows = _rows("SELECT seq FROM sqlite_sequence WHERE name='accounts'")
    return int(rows[0]["seq"]) + 1 if rows else 1


def persist_browser_session(account_id: int, email: str, provider: str, psid: str, psidts: str, flow_id: str) -> dict:
    """Internal intake, called only after live Gemini verification by the login flow."""
    if not email.strip() or len(psid.strip()) < 20 or not flow_id:
        raise ValueError("A verified browser login, account email and real session cookie are required")
    with _lock, conn() as c:
        existing = c.execute("SELECT * FROM accounts WHERE lower(email)=lower(?)", (email,)).fetchone()
        if existing:
            account_id = existing["id"]
        occupied = c.execute("SELECT email FROM accounts WHERE id=?", (account_id,)).fetchone()
        if occupied and occupied["email"].casefold() != email.casefold():
            raise ValueError("Sign in with the same Google account when reconnecting an existing account")
        c.execute("""INSERT INTO accounts
            (id,label,email,provider,status,secure_1psid,secure_1psidts,login_flow_id,verified_at,is_pro,created_at)
            VALUES (?,?,?,?,'ready',?,?,?,?,1,?)
            ON CONFLICT(id) DO UPDATE SET
              label=excluded.label,email=excluded.email,provider=excluded.provider,status='ready',
              secure_1psid=excluded.secure_1psid,secure_1psidts=excluded.secure_1psidts,
              login_flow_id=excluded.login_flow_id,verified_at=excluded.verified_at,is_pro=1""",
            (account_id,email,email,provider,psid,psidts,flow_id,time.time(),time.time()))
        return dict(c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone())


def save_account_states(rows: list[dict]) -> None:
    with _lock, conn() as c:
        for row in rows:
            c.execute("""UPDATE accounts SET status=?,window_usage=?,window_started=?,
                cooldown_until=?,is_pro=?,daily_used=?,weekly_used=?,total_generated=?
                WHERE id=? AND login_flow_id=? AND verified_at>0""",
                (row["status"],row["window_usage"],row["window_started"],row["cooldown_until"],
                 row["is_pro"],row["daily_used"],row["weekly_used"],row["total_generated"],
                 row["id"],row["login_flow_id"]))


def delete_account(account_id: int) -> bool:
    with _lock, conn() as c:
        return c.execute("DELETE FROM accounts WHERE id=?", (account_id,)).rowcount == 1


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
