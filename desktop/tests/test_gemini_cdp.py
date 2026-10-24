"""CDP Gemini engine: zero-seed accounts, isolated-profile login, headless
image-to-image generation through the chat input, exactly-one extraction.

Integration tests drive REAL headless Chromium against tests/support/fake_gemini.py
(a local DOM stand-in — never Google). A live Google sign-in still needs a
manual smoke test on the target Windows machine.
"""
from __future__ import annotations

import glob
import importlib.util
import io
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from desktop.worker import browser, db, gemini_cdp, gemini_pool
from desktop.worker.gemini_client import GeminiAuthError, GeminiRenderError, ModelDegradedError

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("fake_gemini", ROOT / "tests/support/fake_gemini.py")
fake_gemini = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_gemini)


def png(size=(300, 300), colour=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def find_chrome() -> str | None:
    for cand in [os.environ.get("MERCHFLOW_TEST_CHROME"), *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))]:
        if cand and Path(cand).exists():
            return cand
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Temp SQLite + temp pool config + temp profiles root; no real state touched."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite")
    db.init()
    monkeypatch.setattr(gemini_pool, "CONFIG_PATH", tmp_path / "gemini_accounts.json")
    monkeypatch.setattr(gemini_pool, "CLIENTS", {})
    monkeypatch.setattr(gemini_cdp, "PROFILES_ROOT", tmp_path / "profiles")
    pool = gemini_pool.AccountPool()
    monkeypatch.setattr(gemini_pool, "POOL", pool)
    yield tmp_path, pool
    gemini_pool.shutdown_clients()


@pytest.fixture
def live(isolated, monkeypatch):
    chrome = find_chrome()
    if not chrome:
        pytest.skip("Chromium not installed; CDP integration tests need a real browser")
    fake = fake_gemini.FakeGemini()
    monkeypatch.setattr(gemini_cdp, "APP_URL", fake.app_url)
    monkeypatch.setattr(gemini_cdp, "GENERATION_TIMEOUT", 45.0)
    monkeypatch.setattr(gemini_cdp, "READY_TIMEOUT", 20.0)
    base = 20000 + (os.getpid() % 2000) * 10
    monkeypatch.setattr(gemini_cdp, "CDP_BASE_PORT", base)
    monkeypatch.setattr(gemini_cdp, "LOGIN_BASE_PORT", base + 5)
    monkeypatch.setenv("MERCHFLOW_CHROME", chrome)
    extra = "--no-sandbox --disable-dev-shm-usage" + ("" if os.environ.get("DISPLAY") else " --headless=new")
    monkeypatch.setenv("MERCHFLOW_CHROME_EXTRA_ARGS", extra)
    tmp_path, pool = isolated
    yield fake, pool, tmp_path
    gemini_pool.shutdown_clients()
    fake.close()


def signed_in_profile(pool, account_id: int = 1) -> gemini_cdp.CdpGeminiClient:
    """What a completed manual sign-in leaves behind: a profile holding a persistent session."""
    udir = gemini_cdp.profile_dir(account_id)
    udir.mkdir(parents=True, exist_ok=True)
    seeder = gemini_cdp.CdpGeminiClient(account_id, "chrome", user_data_dir=udir)
    port = seeder._ensure_browser()
    target = gemini_cdp.new_page(port, gemini_cdp.APP_URL.replace("/app", "/login"))
    time.sleep(1.5)
    gemini_cdp.close_page(port, target["id"])
    seeder.close()
    db.upsert_gemini_account(account_id, f"Profile {account_id:02d}", "fixture.user@gmail.com", "chrome", str(udir))
    pool.register_cdp_profile(account_id, "fixture.user@gmail.com", "chrome", str(udir))
    return gemini_pool.client_for(account_id)


def colour_of(data: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB").getpixel((5, 5))


# ------------------------------------------------------- zero-seed accounts
def test_account_manager_starts_with_zero_accounts_and_purges_legacy_fakes(isolated):
    tmp_path, pool = isolated
    assert not hasattr(db, "SEED_ACCOUNTS")
    assert db.list_accounts() == [] and pool.accounts == [] and pool.cards() == []
    with db.conn() as c:  # what older builds seeded into existing databases
        c.execute("INSERT INTO accounts (label,email,provider,status) VALUES ('Profile 01','merchflow.alpha@gmail.com','chrome','active')")
        c.execute("INSERT INTO jobs (style_name,niche_name,account_label) VALUES ('Kawaii','Axolotl','Profile 02')")
    db.init()
    assert db.list_accounts() == []
    assert db.list_jobs() == []


def test_pool_never_invents_placeholder_slots(isolated, monkeypatch):
    tmp_path, _ = isolated
    assert gemini_pool.AccountPool().accounts == []          # no config file at all
    config = tmp_path / "gemini_accounts.json"
    config.write_text(json.dumps({"accounts": [{"id": i, "email": "", "secure_1psid": ""} for i in (1, 2, 3)]}))
    assert gemini_pool.AccountPool().accounts == []          # legacy never-signed-in rows are dropped


def test_sqlite_profile_is_restored_as_lazy_headless_cdp_worker(isolated):
    tmp_path, _ = isolated
    udir = gemini_cdp.profile_dir(3)
    udir.mkdir(parents=True)
    db.upsert_gemini_account(3, "Profile 03", "someone@gmail.com", "chrome", str(udir))
    pool = gemini_pool.AccountPool()
    [account] = pool.accounts
    assert (account.id, account.engine, account.status, account.is_pro) == (3, "cdp", "ready", True)
    client = gemini_pool.client_for(3)
    assert isinstance(client, gemini_cdp.CdpGeminiClient)
    assert client._proc is None, "no Chrome may start until a job leases the account"
    card = pool.cards()[0]
    assert card["transport"] == "cdp-headless" and card["cdpPort"] == gemini_cdp.cdp_port(3)


# ---------------------------------------------------------- launch contract
def test_login_and_headless_launch_contract(isolated):
    udir = gemini_cdp.profile_dir(7)
    assert udir.name == "account_7" and udir.parent == gemini_cdp.PROFILES_ROOT
    login = gemini_cdp.chrome_args("chrome", udir, 9507, headless=False)
    assert f"--user-data-dir={udir}" in login
    assert "--app=https://gemini.google.com/app" in login
    assert not any(a.startswith("--headless") for a in login)
    worker = gemini_cdp.chrome_args("chrome", udir, gemini_cdp.cdp_port(1), headless=True)
    assert "--headless=new" in worker and f"--user-data-dir={udir}" in worker
    assert "--remote-debugging-port=9222" in worker
    assert [gemini_cdp.cdp_port(n) for n in (1, 2, 3)] == [9222, 9223, 9224]


def test_app_url_matching_targets_only_the_chat_app():
    ok = ["https://gemini.google.com/app", "https://gemini.google.com/app/abc123", "https://gemini.google.com/u/1/app"]
    bad = ["https://accounts.google.com/ServiceLogin", "https://gemini.google.com/glic", "https://lens.google.com/app", ""]
    assert all(gemini_cdp.is_app_url(u) for u in ok)
    assert not any(gemini_cdp.is_app_url(u) for u in bad)


def test_session_detection_requires_app_url_editor_and_no_sign_in_cta():
    base = {"href": "https://gemini.google.com/app", "editor": True, "signIn": False, "account": "a@gmail.com"}
    assert gemini_cdp.signed_in(base)
    assert not gemini_cdp.signed_in({**base, "signIn": True})
    assert not gemini_cdp.signed_in({**base, "href": "https://accounts.google.com/signin"})
    assert not gemini_cdp.signed_in({**base, "account": None})
    assert gemini_cdp.signed_in({**base, "account": None}, [{"name": "__Secure-1PSID", "value": "x"}])


def test_pending_login_reports_ok_so_the_ui_keeps_polling(tmp_path):
    flow = browser.LoginFlow("chrome", 2, 9502, tmp_path / "account_2")
    assert flow.public()["ok"] is True and flow.public()["state"] == "pending"
    flow.state = "closed"
    assert flow.public()["ok"] is False


# ------------------------------------------------- real Chromium integration
def test_add_account_login_records_sqlite_closes_window_then_generates_headless(live):
    fake, pool, tmp_path = live
    flow = browser.spawn_login_flow("chrome")
    assert flow["ok"] and flow["state"] == "pending" and flow["slot"] == 1
    assert flow["profileDir"].endswith("account_1")
    time.sleep(3)
    assert browser.flow_status(flow["flowId"])["state"] == "pending", "a signed-out Gemini page must not count"

    # The human signs in inside the window (simulated: the page reaches the session-setting endpoint).
    deadline = time.monotonic() + 20
    target = None
    while time.monotonic() < deadline and target is None:
        target = next((t for t in gemini_cdp.list_pages(flow["port"]) if gemini_cdp.is_app_url(t["url"])), None)
        time.sleep(0.3)
    assert target, "the setup window must open on the Gemini app URL"
    s = gemini_cdp.CDPSession(target["webSocketDebuggerUrl"])
    s.send("Page.navigate", {"url": fake.app_url.replace("/app", "/login")})
    s.close()

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and browser.flow_status(flow["flowId"])["state"] == "pending":
        time.sleep(0.5)
    status = browser.flow_status(flow["flowId"])
    assert status["state"] == "authenticated", status
    assert status["email"] == "fixture.user@gmail.com"
    [row] = db.list_accounts()
    assert (row["id"], row["engine"], row["email"]) == (1, "cdp", "fixture.user@gmail.com")
    assert row["profileDir"].endswith("account_1")
    assert gemini_cdp.devtools_version(flow["port"]) is None, "the setup window must be closed"

    # Future generations: same profile, headless, no credentials requested.
    client = gemini_pool.client_for(1)
    assert isinstance(client, gemini_cdp.CdpGeminiClient)
    prompt = gemini_pool.donepixel_prompt("#000000")
    reference = png((320, 400), (200, 100, 50))
    result = client.generate_image(prompt, [(reference, "concept-reference.png")])
    cmdline = Path(f"/proc/{client._proc.pid}/cmdline").read_bytes().split(b"\0")
    assert b"--headless=new" in cmdline and f"--user-data-dir={row['profileDir']}".encode() in cmdline
    assert Image.open(io.BytesIO(result)).size == fake_gemini.FULL
    [sent] = fake.submissions
    assert sent["prompt"].strip() == prompt
    assert [data for _, data in sent["files"]] == [reference]


def test_exactly_one_full_size_image_is_kept_from_a_multi_image_response(live):
    fake, pool, _ = live
    fake.images = 3
    client = signed_in_profile(pool)
    result = client.generate_image(gemini_pool.donepixel_prompt("#FFFFFF"), [(png(), "ref.png")])
    assert isinstance(result, bytes)
    assert Image.open(io.BytesIO(result)).size == fake_gemini.FULL, "never the =s512 thumbnail"
    assert colour_of(result) == fake_gemini.COLOURS[0], "the first generated image, only"
    assert client.last_extraction["method"] == "download-full-size"
    assert client.last_extraction["imagesInResponse"] == 3


def test_attachment_menu_file_chooser_and_full_resolution_fetch_fallback(live):
    fake, pool, _ = live
    fake.mode, fake.download = "chooser", False   # no static input, no download button
    client = signed_in_profile(pool)
    result = client.generate_image("Recreate this.", [(png(), "ref.png")])
    assert Image.open(io.BytesIO(result)).size == fake_gemini.FULL
    assert client.last_extraction["method"] == "fetch-full-resolution"
    assert len(fake.submissions[0]["files"]) == 1


def test_fusion_references_upload_in_image1_image2_order(live):
    fake, pool, _ = live
    client = signed_in_profile(pool)
    niche, style = png(colour=(1, 1, 1)), png(colour=(2, 2, 2))
    client.generate_image(gemini_pool.FUSION_DIRECTIVE, [(niche, "niche_x_IMAGE1.png"), (style, "style_ref_IMAGE2.png")])
    names = [name for name, _ in fake.submissions[0]["files"]]
    assert names == ["01_niche_x_IMAGE1.png", "02_style_ref_IMAGE2.png"]
    assert [data for _, data in fake.submissions[0]["files"]] == [niche, style]


def test_refusal_and_quota_fail_loudly_without_fake_output(live):
    fake, pool, _ = live
    client = signed_in_profile(pool)
    fake.reply, fake.delay_ms = "refuse", 300
    with pytest.raises(GeminiRenderError, match="refused"):
        client.generate_image("Recreate this.", [(png(), "ref.png")])
    fake.reply = "quota"
    with pytest.raises(ModelDegradedError):
        client.generate_image("Recreate this.", [(png(), "ref.png")])


def test_signed_out_profile_raises_auth_error(live):
    fake, pool, _ = live
    udir = gemini_cdp.profile_dir(5)
    udir.mkdir(parents=True)
    client = gemini_cdp.CdpGeminiClient(5, "chrome", user_data_dir=udir)
    try:
        with pytest.raises(GeminiAuthError):
            client.generate_image("Recreate this.", [(png(), "ref.png")])
    finally:
        client.close()
