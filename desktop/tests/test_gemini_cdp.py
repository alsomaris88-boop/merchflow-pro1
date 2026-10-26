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
import inspect
import hashlib
import os
import shutil
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import threading

import pytest
from PIL import Image

from desktop.worker import browser, db, gemini_cdp, gemini_pool
from desktop.worker.gemini_client import GeminiAuthError, GeminiAutomationError, GeminiRenderError, ModelDegradedError

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
    """Temp SQLite + isolated profiles root; no real state touched."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite")
    db.init()
    monkeypatch.setattr(gemini_pool, "CLIENTS", {})
    monkeypatch.setattr(browser, "FLOWS", {})
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
    flow = browser.LoginFlow("chrome", account_id, user_data_dir=udir)
    flow.authentication_observed, flow.email = True, "fixture.user@gmail.com"
    assert pool.register_cdp_profile(flow)["ok"]
    return gemini_pool.client_for(account_id)


def colour_of(data: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB").getpixel((5, 5))


# ------------------------------------------------------- zero-seed accounts
def test_account_manager_starts_empty_and_purges_unverified_metadata_without_deleting_history(isolated):
    tmp_path, pool = isolated
    assert not hasattr(db, "SEED_ACCOUNTS")
    assert db.list_accounts() == [] and pool.accounts == [] and pool.cards() == []
    with db.conn() as c:  # what older builds seeded into existing databases
        c.execute("INSERT INTO accounts (label,email,provider,status) VALUES ('Profile 01','merchflow.alpha@gmail.com','chrome','active')")
        c.execute("INSERT INTO accounts (label,email,created_at) VALUES ('Profile 02','legacy@example.test',10000)")
        c.execute("INSERT INTO jobs (style_name,niche_name,background,account_label,duration_ms,created_at) VALUES ('Kawaii','Axolotl','white','Profile 02',68000,9280)")
        c.execute("INSERT INTO jobs (style_name,niche_name,background,account_label,duration_ms,created_at) VALUES ('Kawaii','Axolotl','white','Profile 02',68000,11000)")
    db.init()
    assert db.list_accounts() == []
    assert len(db.list_jobs()) == 1 and db.list_jobs()[0]["created_at"] == 11000  # genuine later work survives


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
    db.persist_browser_profile(3, "someone@gmail.com", "chrome", str(udir), "TEST-ONLY-previously-verified-flow")
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
    private = db.session_rows()[0]
    assert private["profile_dir"].endswith("account_1")
    assert "profileDir" not in row and row["authSource"] == "browser-login"
    assert gemini_cdp.devtools_version(flow["port"]) is None, "the setup window must be closed"

    # Future generations: same profile, headless, no credentials requested.
    client = gemini_pool.client_for(1)
    assert isinstance(client, gemini_cdp.CdpGeminiClient)
    prompt = gemini_pool.donepixel_prompt("#000000")
    reference = png((320, 400), (200, 100, 50))
    result = client.generate_image(prompt, [(reference, "concept-reference.png")])
    cmdline = Path(f"/proc/{client._proc.pid}/cmdline").read_bytes().split(b"\0")
    assert b"--headless=new" in cmdline and f"--user-data-dir={private['profile_dir']}".encode() in cmdline
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


def test_silent_paste_without_file_input_or_menu_uses_original_fetch(live):
    fake, pool, _ = live
    fake.mode, fake.download, fake.query_string = "no-controls", False, True
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


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_signed_out_profile_raises_auth_error(live, locale):
    fake, pool, _ = live
    fake.locale = locale
    udir = gemini_cdp.profile_dir(5)
    udir.mkdir(parents=True)
    client = gemini_cdp.CdpGeminiClient(5, "chrome", user_data_dir=udir)
    try:
        with pytest.raises(GeminiAuthError):
            client.generate_image("Recreate this.", [(png(), "ref.png")])
    finally:
        client.close()


# ---------------------------------------- French / icon-only / strict gate regression
def test_upload_selector_vocabulary_includes_requested_french_and_universal_controls():
    assert set(["open upload file menu", "upload", "add file", "attach", "tools", "importer",
                "ajouter", "joindre", "téléverser", "outils", "plus", "+"]) <= set(gemini_cdp.SELECTORS["uploadButtonAria"])
    assert set(["upload files", "upload file", "upload image", "add image", "importer des fichiers",
                "importer une image", "ajouter des fichiers", "téléverser"]) <= set(gemini_cdp.SELECTORS["uploadMenuText"])


@pytest.mark.parametrize("mode", ["chooser", "svg", "mat", "testid", "drop", "no-controls"])
def test_silent_paste_ignores_french_upload_controls_and_preserves_image1_image2(live, monkeypatch, mode):
    fake, pool, _ = live
    fake.locale, fake.http_thumbnail, fake.delay_ms = "fr", True, 300
    fake.mode = mode
    forbid_upload_protocol(monkeypatch)
    client = signed_in_profile(pool)
    niche, style = png(colour=(1, 8, 3)), png(colour=(4, 2, 9))
    result = client.generate_image(gemini_pool.FUSION_DIRECTIVE,
                                   [(niche, "niche_IMAGE1.png"), (style, "style_IMAGE2.png")])
    assert client.email == "fixture.user@gmail.com"  # French Compte Google was actually read
    assert Image.open(io.BytesIO(result)).size == fake_gemini.FULL
    assert client.last_extraction["method"] == "download-full-size", "French download control must work too"
    [sent] = fake.submissions
    # contenteditable/FormData normalises paragraph whitespace; every directive
    # word and Unicode symbol must survive, with the literal separately unchanged.
    assert " ".join(sent["prompt"].split()) == " ".join(gemini_pool.FUSION_DIRECTIVE.split())
    assert [name for name, _ in sent["files"]] == ["01_niche_IMAGE1.png", "02_style_IMAGE2.png"]
    assert [data for _, data in sent["files"]] == [niche, style]
    assert sent["delivery"] == {"paste":2,"drop":0,"menu":0,"chooser":0,"fileInput":0,"imagesPill":0}
    assert client.last_extraction["upload"] == "clipboard-paste"


def test_real_browser_gate_rejects_avatar_history_hidden_broken_empty_and_preexisting_images(live):
    fake, pool, _ = live
    client = signed_in_profile(pool)
    port = client._ensure_browser()
    target, session = client._open_tab(port)
    try:
        client._wait_ready(session)
        session.evaluate("""(() => {
          const root = document.querySelector('input-area-v2');
          // A nested text-only .input-container must not hide sibling previews.
          const inner = document.createElement('div'); inner.className='input-container';
          root.appendChild(inner); inner.appendChild(root.querySelector('rich-textarea'));
          const make = (parent, attrs) => { const img = Object.assign(document.createElement('img'), attrs);
            img.style.cssText='width:80px;height:96px'; parent.appendChild(img); return img; };
          make(document.querySelector('header'), { src:'/img/0-1=s512', alt:'Google Account' });
          make(document.querySelector('#chat'), { src:'/img/0-1=s512' });
          const avatar = make(root, { src:'/img/0-1=s512', className:'avatar' });
          const hidden = document.createElement('div'); hidden.hidden=true; root.appendChild(hidden);
          make(hidden,{src:'/img/0-1=s512'});
          const transparent = document.createElement('div'); transparent.style.opacity='0'; root.appendChild(transparent);
          make(transparent,{src:'/img/0-1=s512'});
          make(root,{src:'/missing.png',className:'attachment-thumbnail'});
          const empty = document.createElement('div'); empty.className='attachment-card'; empty.textContent='not a thumbnail'; root.appendChild(empty);
          return true;
        })()""")
        time.sleep(0.3)
        assert session.evaluate(gemini_cdp.GATE1_JS)["chips"] == 0
        assert not client._gate1(session, 1, 0.3)
        # Decorative image already in the composer cannot satisfy the NEW upload.
        session.evaluate("""(() => { const img=document.createElement('img'); img.id='brand'; img.src='/img/0-1=s512';
          img.style.cssText='width:80px;height:96px'; document.querySelector('input-area-v2').appendChild(img); })()""")
        time.sleep(0.3)
        baseline = set(session.evaluate(gemini_cdp.GATE1_JS)["tokens"])
        assert len(baseline) == 1
        session.evaluate("document.querySelector('#brand').replaceWith(document.querySelector('#brand').cloneNode(true))")
        assert not client._gate1(session, 1, 0.3, baseline), "framework re-render of the same logo must not pass"
        session.evaluate("""(() => { const img=document.createElement('img'); img.src='/img/0-2=s512';
          img.setAttribute('aria-hidden','true'); img.style.cssText='width:80px;height:96px';
          document.querySelector('input-area-v2').appendChild(img);
          const bar=document.createElement('div'); bar.id='upload-bar'; bar.setAttribute('role','progressbar');
          bar.style.cssText='display:none;width:20px;height:20px'; document.querySelector('input-area-v2').appendChild(bar); })()""")
        time.sleep(0.3)
        assert client._gate1(session, 1, 0.5, baseline), "real HTTP thumbnail + hidden spinner must pass"
        session.evaluate("document.querySelector('#upload-bar').style.display='block'")
        assert not client._gate1(session, 1, 0.3, baseline), "a real in-progress upload must still block"
        session.evaluate("document.querySelector('rich-textarea').remove()")
        assert session.evaluate(gemini_cdp.GATE1_JS)["chips"] == 0, "never fall back to the entire chat/document"
    finally:
        session.close()
        gemini_cdp.close_page(port, target["id"])


def test_partial_upload_never_appends_duplicate_references_to_fool_gate1(live, monkeypatch):
    fake, pool, _ = live
    fake.max_upload_files = 1
    monkeypatch.setattr(gemini_cdp, "UPLOAD_TIMEOUT", 0.7)
    client = signed_in_profile(pool)
    with pytest.raises(GeminiRenderError, match="GATE 1 FAILED: the reference thumbnail never rendered"):
        client.generate_image(gemini_pool.FUSION_DIRECTIVE, [(png(), "IMAGE1.png"), (png(), "IMAGE2.png")])
    assert fake.submissions == [], "no prompt/send is allowed before genuine complete attachment verification"


# ------------------------------------- silent clipboard / original-only production contract
FORBIDDEN_UPLOAD_COMMANDS = {"DOM.setFileInputFiles", "Page.setInterceptFileChooserDialog", "Input.dispatchDragEvent"}

def forbid_upload_protocol(monkeypatch):
    original = gemini_cdp.CDPSession.send
    def checked(session, method, *args, **kwargs):
        assert method not in FORBIDDEN_UPLOAD_COMMANDS, f"Forbidden UI/file-chooser upload command: {method}"
        return original(session, method, *args, **kwargs)
    monkeypatch.setattr(gemini_cdp.CDPSession, "send", checked)


def test_clipboard_contract_has_no_menu_picker_native_file_or_preview_canvas_fallback():
    source = inspect.getsource(gemini_cdp)
    assert "ClipboardEvent('paste'" in source and "new DataTransfer()" in source
    assert "DragEvent('drop'" in source
    for method in FORBIDDEN_UPLOAD_COMMANDS:
        assert method not in source
    assert "CLICK_UPLOAD_JS" not in source and "CLICK_UPLOAD_MENU_JS" not in source
    assert "CANVAS_JS" not in source and "canvas-toDataURL" not in source
    assert "navigator.clipboard" not in source and "pyperclip" not in source


def test_exact_8k_templates_are_byte_identical_and_colour_is_the_only_substitution():
    assert hashlib.sha256(gemini_pool.FUSION_DIRECTIVE.encode()).hexdigest() == "cd28a8b6c29a84515a4547bb7a5dbe1f7c99bf7f4ff72f695bcd767ef0143d3e"
    assert hashlib.sha256(gemini_pool.DONEPIXEL_PROMPT.encode()).hexdigest() == "297b8e4121d26f1282c671db36029cdcf230c3f04f4b84bf6ceff4088c0f2c96"
    for colour in ("#000000", "#FFFFFF"):
        assert gemini_pool.donepixel_prompt(colour) == gemini_pool.DONEPIXEL_PROMPT.replace("{Selected_Color_Hex}",colour)
    payload = {"background":"black", "aspect":"5:6", "textOverride":"EXACT TEXT"}
    assert gemini_pool._build_prompt(payload).startswith(gemini_pool.FUSION_DIRECTIVE + "\n\n")


@pytest.mark.parametrize("mode,drops", [("both",0),("paste-no-prevent",0),("delayed",0),("drop",1)])
def test_real_chromium_silent_paste_drop_fallback_never_delivers_twice(live, monkeypatch, mode, drops):
    fake, pool, _ = live
    fake.mode, fake.clipboard_mode, fake.delay_ms = "no-controls", mode, 300
    forbid_upload_protocol(monkeypatch)
    monkeypatch.setattr(gemini_cdp,"UPLOAD_TIMEOUT",1.0)
    client = signed_in_profile(pool)
    reference = png()
    result = client.generate_image(gemini_pool.donepixel_prompt("#000000"),[(reference,"reference.png")])
    assert Image.open(io.BytesIO(result)).size == fake.full_size
    [sent] = fake.submissions
    assert [data for _,data in sent["files"]] == [reference]
    assert sent["delivery"] == {"paste":1,"drop":drops,"menu":0,"chooser":0,"fileInput":0,"imagesPill":0}
    assert client.last_extraction["upload"] == ("clipboard-paste/drop" if drops else "clipboard-paste")
    assert client.last_extraction["fullResolution"] is True


def test_clipboard_preserves_original_jpeg_bytes_and_sniffs_mime_despite_png_filename(live, monkeypatch):
    fake,pool,_ = live
    fake.mode = "no-controls"
    forbid_upload_protocol(monkeypatch)
    stream=io.BytesIO();Image.new("RGB",(300,300),(10,50,70)).save(stream,"JPEG")
    original=stream.getvalue()
    signed_in_profile(pool).generate_image(gemini_pool.donepixel_prompt("#FFFFFF"),[(original,"concept-reference.png")])
    [sent]=fake.submissions
    assert sent["files"][0][1] == original and sent["mimes"] == ["image/jpeg"]
    assert sent["delivery"]["paste"] == 1 and sent["delivery"]["drop"] == 0


@pytest.mark.parametrize("mode,drops,reason", [("both",0,"reference thumbnail"),("none",1,"reference thumbnail"),("duplicate",0,"duplicate attachments")])
def test_rejected_or_duplicate_synthetic_paste_fails_before_send_without_fake_thumbnail(live, monkeypatch, mode, drops, reason):
    fake,pool,_=live
    fake.mode,fake.clipboard_mode="no-controls",mode
    fake.reject_upload = mode != "duplicate"
    monkeypatch.setattr(gemini_cdp,"UPLOAD_TIMEOUT",0.7)
    forbid_upload_protocol(monkeypatch)
    client=signed_in_profile(pool)
    observed=[];original=client._attach
    def attach(session,paths):
        try: return original(session,paths)
        finally: observed.append((session.evaluate("window.__fixtureDelivery"),session.evaluate("window.__merchflowClipboard || null")))
    monkeypatch.setattr(client,"_attach",attach)
    with pytest.raises(GeminiAutomationError,match=reason):
        client.generate_image(gemini_pool.donepixel_prompt("#000000"),[(png(),"reference.png")])
    assert fake.submissions == []
    delivery,pending=observed[0]
    assert delivery == {"paste":1,"drop":drops,"menu":0,"chooser":0,"fileInput":0,"imagesPill":0}
    assert pending is None, "no Base64/File/DataTransfer reference is retained after failure"


def test_original_failure_never_falls_back_to_fetching_or_canvas_saving_the_preview(live, monkeypatch):
    fake,pool,_=live
    fake.mode,fake.download,fake.full_error,fake.query_string="no-controls",False,True,True
    forbid_upload_protocol(monkeypatch)
    client=signed_in_profile(pool)
    with pytest.raises(GeminiAutomationError,match="preview/canvas fallback is disabled"):
        client.generate_image(gemini_pool.donepixel_prompt("#000000"),[(png(),"reference.png")])
    assert len(fake.submissions)==1, "uncertain generation must not be silently reissued"
    assert any("=s0?fixture=keep" in r["url"] for r in fake.asset_requests)
    assert not any("=s512" in r["url"] and r["destination"]=="empty" for r in fake.asset_requests)
    assert not client.last_extraction.get("fullResolution")


def test_a_full_size_endpoint_returning_only_thumbnail_pixels_is_rejected(live):
    fake,pool,_=live
    fake.full_is_preview=True
    client=signed_in_profile(pool)
    with pytest.raises(GeminiAutomationError,match="preview-sized 512×640"):
        client.generate_image(gemini_pool.donepixel_prompt("#000000"),[(png(),"reference.png")])
    assert len(fake.submissions)==1 and not client.last_extraction.get("fullResolution")


def test_an_actual_8k_original_is_extracted_byte_identically_without_resize(live):
    fake,pool,_=live
    fake.full_size=(6554,8192)
    client=signed_in_profile(pool)
    result=client.generate_image(gemini_pool.donepixel_prompt("#000000"),[(png(),"reference.png")])
    assert Image.open(io.BytesIO(result)).size==fake.full_size
    assert result==fake_gemini.png(fake.full_size,fake_gemini.COLOURS[0])
    assert client.last_extraction["fullResolution"] and client.last_extraction["height"]==8192
    assert len(fake.submissions)==1


def test_full_directive_is_repaired_if_native_text_insertion_was_truncated(live, monkeypatch):
    fake,pool,_=live
    original=gemini_cdp.CDPSession.send
    def truncated(session,method,params=None,**kwargs):
        if method=="Input.insertText": params={**params,"text":params["text"][:80]}
        return original(session,method,params,**kwargs)
    monkeypatch.setattr(gemini_cdp.CDPSession,"send",truncated)
    client=signed_in_profile(pool)
    client.generate_image(gemini_pool.FUSION_DIRECTIVE,[(png(),"IMAGE1.png"),(png(colour=(80,2,4)),"IMAGE2.png")])
    [sent]=fake.submissions
    assert " ".join(sent["prompt"].split())==" ".join(gemini_pool.FUSION_DIRECTIVE.split())
    assert len(sent["files"])==2


@pytest.mark.parametrize("headless",[True,False])
def test_profile_reattachment_never_reuses_a_visible_browser_for_generation(isolated, monkeypatch, headless):
    folder=gemini_cdp.profile_dir(8);folder.mkdir(parents=True)
    (folder/"DevToolsActivePort").write_text("12345\n/browser/test-only\n")
    monkeypatch.setattr(gemini_cdp,"devtools_version",lambda port:{"User-Agent":"HeadlessChrome/156" if headless else "Chrome/156"})
    client=gemini_cdp.CdpGeminiClient(8,user_data_dir=folder)
    if headless: assert client._owned_port()==12345
    else:
        with pytest.raises(GeminiAutomationError,match="visible browser"):
            client._owned_port()



def test_existing_real_draft_attachment_is_rejected_before_new_paste_or_send(live):
    fake,pool,root=live
    client=signed_in_profile(pool)
    old=root/"old.png";new=root/"new.png"
    old.write_bytes(png());new.write_bytes(png(colour=(20,90,50)))
    port=client._ensure_browser();target,session=client._open_tab(port)
    try:
        client._wait_ready(session)
        assert gemini_cdp._paste_image(session,old)
        assert client._gate1(session,1,2)
        with pytest.raises(GeminiAutomationError,match="already contains a reference"):
            client._attach(session,[str(new)])
        assert session.evaluate("window.__fixtureDelivery.paste")==1
        assert fake.submissions==[]
    finally:
        session.evaluate(gemini_cdp.CLEAR_CLIPBOARD_JS)
        session.close();gemini_cdp.close_page(port,target["id"])



@pytest.mark.parametrize("text",[
    "You've reached a temporary network problem. Please try again tomorrow.",
    "Service momentanément indisponible. Réessayez demain.",
])
def test_generic_service_retry_text_is_not_misclassified_as_quota(isolated, monkeypatch, text):
    class Clock:
        value=0
        def monotonic(self): self.value+=2; return self.value
        def sleep(self,seconds): pass
    class Session:
        def evaluate(self,script):
            return {"responses":1,"images":0,"downloads":0,"stop":False,"text":text}
    monkeypatch.setattr(gemini_cdp,"time",Clock())
    client=gemini_cdp.CdpGeminiClient(99)
    with pytest.raises(GeminiAutomationError,match="answered without an image"):
        client._await_image(Session(),{"responses":0})



def test_one_authenticated_headless_profile_serves_concurrent_tabs_with_isolated_downloads(live):
    fake,pool,_=live
    fake.mode="no-controls"
    fake.unique_assets=True
    fake.delay_ms=350
    client=signed_in_profile(pool)
    reference=png()
    def generate(prompt):
        image=client.generate_image(prompt,[(reference,"same-name.png")])
        return image,dict(client.last_extraction)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures=[executor.submit(generate,prompt) for prompt in (
            "CONCURRENCY RED", "CONCURRENCY BLUE", "CONCURRENCY RED",
        )]
        results=[future.result(timeout=90) for future in futures]
    assert [colour_of(data) for data,_ in results]==[(220,40,40),(30,80,220),(220,40,40)]
    assert all(record["method"]=="download-full-size" and record["fullResolution"] for _,record in results)
    assert Counter(submission["prompt"] for submission in fake.submissions)==Counter(["CONCURRENCY RED","CONCURRENCY BLUE","CONCURRENCY RED"])
    assert all(submission["delivery"]["paste"]==1 and submission["delivery"]["drop"]==0 for submission in fake.submissions)
    assert len({submission["requestId"] for submission in fake.submissions})==3


def test_diagnostics_are_thread_local_between_concurrent_tab_jobs(isolated):
    client=gemini_cdp.CdpGeminiClient(700)
    barrier=threading.Barrier(2)
    def write(value):
        client.last_extraction={"job":value}
        barrier.wait(timeout=3)
        return client.last_extraction["job"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(write,value) for value in ("red","blue")]
        assert sorted(future.result(timeout=4) for future in futures)==["blue","red"]
