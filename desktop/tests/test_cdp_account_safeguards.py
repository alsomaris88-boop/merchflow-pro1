"""CDP account admission uses real SQLite; only Chrome responses are doubled.

The separate test_gemini_cdp suite drives an actual Chromium login/profile and
image exchange against the isolated local DOM fixture, never live Google.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desktop.worker import browser, db, gemini_cdp, gemini_pool
from desktop.worker.gemini_client import GeminiAuthError

EMAIL = "verified@example.test"


@pytest.fixture
def linked_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "private.sqlite")
    monkeypatch.setattr(gemini_cdp, "PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(gemini_pool, "CLIENTS", {})
    monkeypatch.setattr(browser, "FLOWS", {})
    db.init()
    metrics = {"init": 0, "closed": 0, "fail": False, "during_init": None}

    class ProbeClient:
        def __init__(self, account_id, provider="chrome", label="", user_data_dir=None):
            self.email = EMAIL
            self.user_data_dir = Path(user_data_dir)

        def init(self):
            metrics["init"] += 1
            if metrics["during_init"]:
                metrics["during_init"]()
            if metrics["fail"]:
                raise GeminiAuthError("Rejected headless session")

        def close(self):
            metrics["closed"] += 1

    monkeypatch.setattr(gemini_cdp, "CdpGeminiClient", ProbeClient)
    pool = gemini_pool.AccountPool()
    monkeypatch.setattr(gemini_pool, "POOL", pool)
    return pool, metrics


def observed(pool, slot=None):
    slot = pool.next_free_slot() if slot is None else slot
    folder = gemini_cdp.profile_dir(slot)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "TEST-ONLY-session-marker").write_text("not a real Google credential")
    flow = browser.LoginFlow("chrome", slot, user_data_dir=folder)
    flow.authentication_observed, flow.email = True, EMAIL
    browser.FLOWS[flow.id] = flow
    return flow


def test_cdp_metadata_without_an_observed_browser_login_cannot_register(linked_store):
    pool, metrics = linked_store
    flow = observed(pool)
    flow.authentication_observed = False
    assert not browser._record(flow)["ok"]
    assert metrics["init"] == 0 and db.list_accounts() == pool.cards() == []


def test_cdp_headless_probe_precedes_sqlite_admission_and_private_projection(linked_store):
    pool, metrics = linked_store
    flow = observed(pool)
    metrics["during_init"] = lambda: assert_empty(pool)
    assert browser._record(flow)["ok"]
    assert metrics["init"] == 1 and flow.state == "authenticated"
    [private] = db.session_rows()
    assert private["engine"] == "cdp" and private["profile_dir"] == str(flow.user_data_dir)
    assert private["secure_1psid"] == private["secure_1psidts"] == ""
    [public] = db.list_accounts()
    assert public["authSource"] == "browser-login" and public["hasSession"] and not public["hasCookies"]
    encoded = json.dumps(db.list_accounts() + pool.cards())
    assert flow.id not in encoded and str(flow.user_data_dir) not in encoded


def assert_empty(pool):
    assert db.list_accounts() == db.session_rows() == pool.accounts == pool.cards() == []


def test_cdp_failed_probe_inserts_no_account_or_client(linked_store):
    pool, metrics = linked_store
    metrics["fail"] = True
    assert not browser._record(observed(pool))["ok"]
    assert_empty(pool)
    assert not gemini_pool.CLIENTS


def test_cdp_cancel_during_headless_probe_cannot_commit(linked_store):
    pool, metrics = linked_store
    flow = observed(pool)
    metrics["during_init"] = lambda: browser.cancel_flow(flow.id)
    assert not browser._record(flow)["ok"]
    assert flow.state == "closed"
    assert_empty(pool)
    assert not gemini_pool.CLIENTS


def test_cdp_failed_reconnect_preserves_existing_session_and_client(linked_store):
    pool, metrics = linked_store
    flow = observed(pool)
    assert browser._record(flow)["ok"]
    before = db.session_rows()
    previous_client = gemini_pool.client_for(flow.slot)
    metrics["fail"] = True
    assert not browser._record(observed(pool, flow.slot))["ok"]
    assert db.session_rows() == before and gemini_pool.client_for(flow.slot) is previous_client


def test_duplicate_google_identity_keeps_one_account_and_directs_reconnect(linked_store):
    pool, _ = linked_store
    first = observed(pool)
    assert browser._record(first)["ok"]
    result = browser._record(observed(pool))
    assert not result["ok"] and "already connected" in result["reason"]
    assert len(db.list_accounts()) == len(pool.accounts) == len(pool.cards()) == 1


def test_cdp_counters_and_provenance_survive_restart_without_json(linked_store):
    pool, _ = linked_store
    flow = observed(pool)
    assert browser._record(flow)["ok"]
    pool.note_render(flow.slot)
    restored = gemini_pool.AccountPool()
    [account] = restored.accounts
    assert account.engine == "cdp" and account.login_flow_id == flow.id
    assert account.total_generated == account.window_usage == 1
    assert restored.cards()[0]["email"] == EMAIL


def test_cdp_unlink_removes_profile_sqlite_and_client_on_both_api_shapes(linked_store, tmp_path, monkeypatch):
    pool, _ = linked_store
    from desktop.worker import app as module
    monkeypatch.setattr(module, "OUT_DIR", tmp_path / "outputs")
    for path in ("/api/accounts?id={id}", "/api/bridge/accounts/{id}"):
        flow = observed(pool)
        assert browser._record(flow)["ok"]
        with TestClient(module.app) as client:
            response = client.delete(path.format(id=flow.slot))
            assert response.status_code == 200 and response.json()["profileDeleted"]
        assert not flow.user_data_dir.exists()
        assert not db.session_rows() and not gemini_pool.client_for(flow.slot)
        assert not gemini_pool.AccountPool().accounts


def test_cdp_busy_accounts_cannot_reconnect_or_unlink(linked_store):
    pool, _ = linked_store
    flow = observed(pool)
    assert browser._record(flow)["ok"]
    account = pool.acquire(require_pro=True)
    try:
        with pytest.raises(RuntimeError, match="generating"):
            pool.reserve_login(account.id)
        with pytest.raises(RuntimeError, match="generating"):
            pool.remove(account.id)
        assert flow.user_data_dir.exists() and len(db.list_accounts()) == 1
    finally:
        pool.release(account.id)


def test_cdp_reconnect_reservation_prevents_simultaneous_generation(linked_store):
    pool, _ = linked_store
    flow = observed(pool)
    assert browser._record(flow)["ok"]
    pool.reserve_login(flow.slot)
    try:
        assert pool.ready_pro_count() == 0
        with pytest.raises(RuntimeError, match="No available"):
            pool.acquire(require_pro=True, timeout=0)
    finally:
        pool.finish_login(flow.slot)
    assert pool.ready_pro_count() == 1


def test_cdp_unverified_profile_directory_is_not_enough_to_admit_an_account(linked_store):
    pool, _ = linked_store
    folder = gemini_cdp.profile_dir(4)
    folder.mkdir(parents=True)
    with db.conn() as c:
        c.execute("INSERT INTO accounts(label,email,engine,profile_dir,status) VALUES(?,?,'cdp',?,'ready')", ("Unverified metadata",EMAIL,str(folder)))
    assert not gemini_pool.AccountPool().accounts and not db.list_accounts()
    db.init()
    with db.conn() as c:
        assert c.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0


def test_cdp_profile_deletion_failure_reports_failure_and_keeps_account(linked_store, tmp_path, monkeypatch):
    pool, _ = linked_store
    flow = observed(pool)
    assert browser._record(flow)["ok"]
    def refuse_delete(path):
        raise PermissionError("TEST-ONLY locked profile")
    monkeypatch.setattr(gemini_pool.shutil, "rmtree", refuse_delete)
    with pytest.raises(RuntimeError, match="Could not remove"):
        pool.remove(flow.slot)
    assert len(db.list_accounts()) == 1 and flow.user_data_dir.exists()


def test_cdp_cancelled_new_profile_is_cleaned_before_releasing_reservation(linked_store, monkeypatch):
    pool, metrics = linked_store
    flow = observed(pool)
    flow.new_profile = True
    pool.reserve_login(flow.slot)
    monkeypatch.setattr(browser, "POLL_SECONDS", 0)
    monkeypatch.setattr(browser, "probe_session", lambda port: (True, {"account": EMAIL}))
    monkeypatch.setattr(browser, "_close_window", lambda *args: None)
    metrics["during_init"] = lambda: browser.cancel_flow(flow.id)
    from types import SimpleNamespace
    browser._sensor(flow, SimpleNamespace(poll=lambda: None))
    assert flow.state == "closed" and not flow.user_data_dir.exists()
    assert flow.slot not in pool._logins
    assert_empty(pool)
