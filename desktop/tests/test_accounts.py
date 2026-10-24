"""Real SQLite/account contracts; Google/CDP responses are isolated test doubles.

No Google passwords, production cookies or live authenticated provider calls
are used. The production intake must observe a browser login and then verify
Gemini before any account row or client becomes visible.
"""
import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from desktop.worker import browser, db, gemini_cdp, gemini_client, gemini_pool

COOKIE = "TEST-ONLY-session-cookie-not-a-real-Google-credential"
EMAIL = "artist@example.test"


@pytest.fixture
def account_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "private.sqlite")
    db.init()
    monkeypatch.setattr(gemini_pool, "CLIENTS", {})
    monkeypatch.setattr(browser, "FLOWS", {})
    metrics = {"init": 0, "closed": 0, "fail": False}

    class VerifiedClient:
        def __init__(self, psid, psidts=None, label=""):
            self.email = None
            self.psid = psid

        def init(self):
            metrics["init"] += 1
            if metrics["fail"]:
                raise gemini_client.GeminiAuthError("Rejected test login")
            self.email = EMAIL

        def close(self):
            metrics["closed"] += 1

    monkeypatch.setattr(gemini_client, "GeminiClient", VerifiedClient)
    pool = gemini_pool.AccountPool()
    monkeypatch.setattr(gemini_pool, "POOL", pool)
    return pool, metrics


def observed_flow(pool):
    flow = browser.LoginFlow("chrome", pool.next_free_slot())
    flow.authentication_observed = True  # only the real CDP sensor does this in production
    browser.FLOWS[flow.id] = flow
    return flow


def test_empty_sqlite_starts_with_exactly_zero_accounts_and_clients(account_store):
    pool, metrics = account_store
    assert db.list_accounts() == db.session_rows() == pool.accounts == pool.cards() == []
    assert gemini_pool.CLIENTS == {} and metrics["init"] == 0
    assert pool.ready_pro_count() == 0
    with pytest.raises(RuntimeError):
        pool.acquire(timeout=0)


def test_migration_purges_unverified_metadata_and_preserves_other_data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "old.sqlite")
    old_schema = db.SCHEMA
    for name in db.ACCOUNT_COLUMNS:
        old_schema = re.sub(rf"^  {name} [^\n]+\n", "", old_schema, flags=re.MULTILINE)
    with db.conn() as conn:
        conn.executescript(old_schema)
        conn.execute("INSERT INTO accounts(label,email,status) VALUES('Unverified metadata','unverified@example.test','ready')")
        conn.execute("INSERT INTO jobs(niche_name) VALUES('Preserve existing job')")
        conn.execute("INSERT INTO listings(title) VALUES('Preserve existing listing')")
    db.init(); db.init()
    assert db.list_accounts() == db.session_rows() == []
    assert db.list_jobs()[0]["niche_name"] == "Preserve existing job"
    assert db.list_listings()[0]["title"] == "Preserve existing listing"
    assert gemini_pool.AccountPool().accounts == []


def test_pending_browser_flow_does_not_create_an_account(account_store):
    pool, metrics = account_store
    flow = browser.LoginFlow("chrome", 1)
    result = pool.register_session(flow, COOKIE, "", EMAIL)
    assert not result["ok"] and metrics["init"] == 0
    assert not db.list_accounts() and not pool.accounts


def test_verified_browser_session_is_committed_only_after_gemini_verification(account_store):
    pool, metrics = account_store
    flow = observed_flow(pool)
    result = pool.register_session(flow, COOKIE, "TEST-ONLY-rotating-cookie", EMAIL)
    assert result["ok"] and metrics["init"] == 1 and flow.state == "authenticated"
    rows = db.session_rows()
    assert len(rows) == len(pool.accounts) == 1
    assert rows[0]["secure_1psid"] == COOKIE
    assert rows[0]["login_flow_id"] == flow.id and rows[0]["verified_at"] > 0
    assert rows[0]["email"] == EMAIL
    assert COOKIE not in repr(pool.accounts[0])
    public = json.dumps(db.list_accounts() + pool.cards())
    assert COOKIE not in public and "secure_1psid" not in public and flow.id not in public
    assert all(item["authSource"] == "browser-login" for item in pool.cards())


def test_failed_verification_never_saves_a_row_or_ghost_pool_member(account_store):
    pool, metrics = account_store
    metrics["fail"] = True
    result = pool.register_session(observed_flow(pool), COOKIE, "", EMAIL)
    assert not result["ok"] and metrics["closed"] == 1
    assert db.list_accounts() == db.session_rows() == pool.accounts == []
    assert not gemini_pool.CLIENTS


def test_cancelled_flow_cannot_save_after_network_verification(account_store, monkeypatch):
    pool, metrics = account_store
    flow = observed_flow(pool)
    original = gemini_client.GeminiClient.init
    def cancel_during_init(client):
        original(client)
        browser.cancel_flow(flow.id)
    monkeypatch.setattr(gemini_client.GeminiClient, "init", cancel_during_init)
    result = pool.register_session(flow, COOKIE, "", EMAIL)
    assert not result["ok"] and flow.state == "closed"
    assert not db.list_accounts() and not pool.accounts and not gemini_pool.CLIENTS


def test_failed_reconnect_preserves_the_existing_real_session(account_store):
    pool, metrics = account_store
    assert pool.register_session(observed_flow(pool), COOKIE, "", EMAIL)["ok"]
    before = db.session_rows()
    old_client = gemini_pool.client_for(pool.accounts[0].id)
    metrics["fail"] = True
    assert not pool.register_session(observed_flow(pool), COOKIE + "-bad", "", EMAIL)["ok"]
    assert db.session_rows() == before and gemini_pool.client_for(pool.accounts[0].id) is old_client


def test_repeated_real_login_for_same_google_account_is_not_duplicated(account_store):
    pool, metrics = account_store
    assert pool.register_session(observed_flow(pool), COOKIE, "", EMAIL)["ok"]
    assert pool.register_session(observed_flow(pool), COOKIE + "-renewed", "", EMAIL)["ok"]
    assert len(db.list_accounts()) == len(pool.cards()) == len(pool.accounts) == 1
    assert db.session_rows()[0]["secure_1psid"] == COOKIE + "-renewed"


def test_real_sessions_and_counters_survive_pool_restart_in_sqlite(account_store):
    pool, metrics = account_store
    assert pool.register_session(observed_flow(pool), COOKIE, "", EMAIL)["ok"]
    account_id = pool.accounts[0].id
    pool.note_render(account_id)
    restored = gemini_pool.AccountPool()
    assert len(restored.accounts) == 1
    assert restored.accounts[0].secure_1psid == COOKIE
    assert restored.accounts[0].total_generated == restored.accounts[0].window_usage == 1
    assert restored.cards()[0]["email"] == EMAIL


def test_disconnecting_removes_credentials_and_stays_empty_after_restart(account_store):
    pool, metrics = account_store
    assert pool.register_session(observed_flow(pool), COOKIE, "", EMAIL)["ok"]
    account_id = pool.accounts[0].id
    assert pool.remove(account_id)
    assert not pool.cards() and not db.session_rows() and not gemini_pool.client_for(account_id)
    assert not gemini_pool.AccountPool().accounts
    assert pool.next_free_slot() > account_id  # do not reuse an old browser profile


def test_pending_real_browser_windows_are_not_database_accounts(account_store, tmp_path, monkeypatch):
    pool, metrics = account_store
    monkeypatch.setattr(gemini_cdp, "PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(browser, "resolve_exe", lambda provider: "TEST-BROWSER-ONLY")
    monkeypatch.setattr(gemini_cdp, "popen", lambda *a, **k: SimpleNamespace(pid=123, poll=lambda: None))
    monkeypatch.setattr(browser.threading.Thread, "start", lambda self: None)
    first = browser.spawn_login_flow("chrome")
    second = browser.spawn_login_flow("chrome")
    assert first["ok"] and second["ok"] and first["slot"] != second["slot"]
    assert first["state"] == second["state"] == "pending"
    assert not db.list_accounts() and not pool.accounts and metrics["init"] == 0


def test_manual_api_intake_is_rejected_and_public_endpoints_never_expose_cookies(account_store, tmp_path, monkeypatch):
    pool, metrics = account_store
    from desktop.worker import app as module
    monkeypatch.setattr(module, "OUT_DIR", tmp_path / "output")
    with TestClient(module.app) as client:
        assert client.get("/api/accounts").json()["items"] == []
        assert client.get("/accounts/status").json()["profiles"] == []
        assert client.get("/health").status_code == 200  # an empty pool is a healthy engine
        for path in ("/api/accounts", "/accounts/cookies", "/api/bridge/accounts"):
            assert client.post(path, json={"id": 1, "email": EMAIL, "psid": COOKIE}).status_code == 405
        assert client.patch("/api/accounts", json={"id": 1, "status": "ready"}).status_code == 405
        assert not db.list_accounts()
        assert pool.register_session(observed_flow(pool), COOKIE, "", EMAIL)["ok"]
        for path in ("/api/accounts", "/accounts/status", "/api/bridge/accounts", "/health"):
            response = client.get(path)
            assert response.status_code == 200 and COOKIE not in response.text and "secure_1psid" not in response.text
        account_id = pool.accounts[0].id
        assert client.delete(f"/api/accounts?id={account_id}").status_code == 200
        assert client.get("/api/accounts").json()["items"] == []


def test_gemini_rpc_downloads_only_one_image_when_response_contains_three_candidates(monkeypatch):
    client = gemini_client.GeminiClient("TEST-ONLY-NO-LIVE-COOKIE")
    urls = [f"https://lh3.googleusercontent.com/gg-dl/candidate-{i}" for i in range(3)]
    monkeypatch.setattr(client.client, "post", lambda *a, **k: SimpleNamespace(status_code=200, text=json.dumps(urls)))
    downloaded = []
    monkeypatch.setattr(client, "_download", lambda url: downloaded.append(url) or b"ONE-IMAGE-ONLY")
    try:
        assert client._rpc("test prompt", [], 10) == b"ONE-IMAGE-ONLY"
        assert downloaded == [urls[0]]
    finally:
        client.close()


def test_original_resolution_download_is_not_forced_to_2048(monkeypatch):
    requests = []
    def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(content=b"actual-response" * 1000, raise_for_status=lambda: None)
    monkeypatch.setattr(gemini_client.httpx, "get", get)
    gemini_client.GeminiClient._download("https://lh3.googleusercontent.com/gg-dl/artwork=s8192")
    assert requests == ["https://lh3.googleusercontent.com/gg-dl/artwork=s0"]
