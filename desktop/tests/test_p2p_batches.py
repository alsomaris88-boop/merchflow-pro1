"""Real disk/libvips/SQLite tests; provider responses are isolated test doubles.

No paid service, production cookies, or successful live Gemini generation is
claimed by this suite. The production mock switch remains blocked.
"""
import io
import json
import os
from pathlib import Path
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from desktop.worker import gemini_pool, image_lab, output_store, p2p_batches


def test_slot_default_is_two_and_environment_override_is_bounded():
    expected = max(1, min(32, int(os.environ.get("MERCHFLOW_GEMINI_SLOTS_PER_ACCOUNT", "2"))))
    assert gemini_pool.SLOTS_PER_ACCOUNT == expected


def reference_png():
    stream = io.BytesIO()
    Image.new("RGBA", (50, 60), (40, 100, 180, 255)).save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def worker(tmp_path, monkeypatch):
    if not image_lab.HAS_VIPS:
        pytest.fail("These tests require pyvips[binary] from desktop/requirements.txt")
    monkeypatch.setenv("MERCHFLOW_DB", str(tmp_path / "app.sqlite"))
    from desktop.worker import app as module
    monkeypatch.setattr(module, "OUT_DIR", tmp_path / "output")
    monkeypatch.setattr(module.db, "DB_PATH", tmp_path / "app.sqlite")
    module.db.init()
    monkeypatch.setattr(gemini_pool, "TAB_LAUNCH_STAGGER_SECONDS", 0.0)
    pool = gemini_pool.AccountPool()
    pool.accounts = [gemini_pool.AccountState(id=i, status="ready", is_pro=True) for i in (1, 2, 3)]
    monkeypatch.setattr(gemini_pool, "POOL", pool)
    monkeypatch.setattr(gemini_pool, "MOCK", False)
    monkeypatch.setattr(pool, "_wait_jitter", lambda account: None)
    lock = threading.Lock()
    metrics = {"active": {}, "peak": 0, "peak_by_account": {}, "calls": []}
    raw = reference_png()

    class Provider:
        def __init__(self, account_id):
            self.id = account_id

        def generate_image(self, prompt, references):
            with lock:
                call_id = len(metrics["calls"])
                metrics["active"][call_id] = self.id
                metrics["peak"] = max(metrics["peak"], len(metrics["active"]))
                active_for_account = sum(account_id == self.id for account_id in metrics["active"].values())
                metrics["peak_by_account"][self.id] = max(metrics["peak_by_account"].get(self.id,0),active_for_account)
                metrics["calls"].append((self.id, prompt, references))
            try:
                time.sleep(0.025)
                return raw
            finally:
                with lock:
                    metrics["active"].pop(call_id)

    clients = {i: Provider(i) for i in (1, 2, 3)}
    monkeypatch.setattr(gemini_pool, "client_for", clients.get)
    with TestClient(module.app) as client:
        yield client, module, pool, metrics
        # Keep fixtures/monkeypatches alive until all detached work finishes.
        for thread in list(p2p_batches._ACTIVE.values()):
            thread.join(timeout=120)


def create_upload_start(client, size, *, corrupt=False, colour="#000000"):
    ids = [uuid.uuid4().hex for _ in range(size)]
    response = client.post("/api/bridge/p2p/batches", json={"background": colour,
                          "items": [{"id": item, "name": "same-design.png"} for item in ids]})
    assert response.status_code == 200, response.text
    batch_id = response.json()["batch"]["id"]
    for i, item in enumerate(ids):
        response = client.post(f"/api/bridge/p2p/batches/{batch_id}/items/{item}",
                               content=b"corrupt" if corrupt and i == 0 else reference_png(),
                               headers={"Content-Type": "application/octet-stream"})
        assert response.status_code == (400 if corrupt and i == 0 else 200), response.text
    response = client.post(f"/api/bridge/p2p/batches/{batch_id}/start")
    assert response.status_code == 200, response.text
    return batch_id


def finish(client, batch_id):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        batch = client.get(f"/api/bridge/p2p/batches/{batch_id}").json()["batch"]
        if batch["status"] not in {"uploading", "running"}:
            return batch
        time.sleep(0.1)
    pytest.fail("Batch failed to reach a terminal state")


@pytest.mark.parametrize("size", [1, 5, 20, 101])
def test_dynamic_batches_archive_every_native_provider_image_before_success(worker, size):
    client, module, pool, metrics = worker
    batch_id = create_upload_start(client, size, colour="#FFFFFF")
    # No SSE subscription and no active Pixel component is required to finish.
    result = finish(client, batch_id)
    assert result["status"] == "completed", result
    assert result["completed"] == size and result["failed"] == 0
    history = client.get("/api/bridge/designs").json()["items"]
    assert len(history) == size
    assert len({r["path"] for r in history}) == size
    assert all(r["batchId"] == batch_id and r["lossless"] for r in history)
    for record in history:
        path = Path(record["path"])
        assert path.parent == module.OUT_DIR and path.name.startswith("p2p_same-design_50x60_")
        assert record["nativeW"] == record["w"] == 50
        assert record["nativeH"] == record["h"] == 60
        assert record["upscaled"] is False
        assert record["bytes"] == path.stat().st_size < 25_000_000
        assert Path(record["previewPath"]).is_file()
        with Image.open(path) as image:
            assert image.size == (50, 60) and image.mode == "RGBA"
            assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)
    events = []
    cursor = 0
    while page := output_store.read_events(module.OUT_DIR, cursor):
        events.extend(page)
        cursor = page[-1][0]
    saved = [ev for _, ev in events if ev["type"] == "design.saved"]
    assert len(saved) == size
    assert {event["design"]["id"] for event in saved} == {r["id"] for r in history}
    assert len(metrics["calls"]) == size  # one provider result per input, never 3N
    assert len({item["design_id"] for item in result["items"]}) == size
    if size > 1:
        assert metrics["peak"] >= 2
        assert len({call[0] for call in metrics["calls"]}) >= 2
    assert max(metrics["peak_by_account"].values()) <= gemini_pool.SLOTS_PER_ACCOUNT
    assert not pool._busy
    assert not (module.OUT_DIR / ".p2p-inputs" / batch_id).exists()
    # Retried start is idempotent: no extra provider calls or history records.
    assert client.post(f"/api/bridge/p2p/batches/{batch_id}/start").status_code == 200
    assert len(metrics["calls"]) == size


def test_exact_prompt_and_single_reference_are_sent_to_each_provider(worker):
    client, module, pool, metrics = worker
    finish(client, create_upload_start(client, 2, colour="#FFFFFF"))
    expected = "Recreate this exact uploaded graphic artwork in ultra-high resolution, 8K crisp print definition. Maintain 100% fidelity to the original composition, typography, characters, and graphic elements. Do NOT add variations, do NOT alter the subject or layout. Clean up all low-resolution artifacts, blur, and fabric wrinkles, and render the exact clean artwork strictly on a solid background of: #FFFFFF."
    for _, prompt, references in metrics["calls"]:
        assert prompt == expected
        assert len(references) == 1 and references[0][0] == reference_png()
    assert all(row["prompt"] == expected for row in output_store.list_designs(module.OUT_DIR))


def test_corrupt_upload_fails_individually_and_other_designs_archive(worker):
    client, module, pool, metrics = worker
    result = finish(client, create_upload_start(client, 3, corrupt=True))
    assert result["status"] == "partial" and result["completed"] == 2 and result["failed"] == 1
    assert len(output_store.list_designs(module.OUT_DIR)) == len(metrics["calls"]) == 2


def test_archive_failure_is_not_retried_at_provider_or_reported_as_saved(worker, monkeypatch):
    client, module, pool, metrics = worker
    def fail(*args, **kwargs):
        raise OSError("Disk full fixture")
    monkeypatch.setattr(output_store, "save_vips_image", fail)
    result = finish(client, create_upload_start(client, 2))
    assert result["completed"] == 0 and result["failed"] == 2
    assert len(metrics["calls"]) == 2
    assert all("Disk full" in job["reason"] for job in result["items"])
    assert not output_store.list_designs(module.OUT_DIR)
    assert not any(event["type"] == "design.saved" for _, event in output_store.read_events(module.OUT_DIR, 0))


def test_native_rgba_pixels_survive_lossless_png_above_4mb(tmp_path):
    # A high-entropy band creates a genuine >4 MB master, not padded fake bytes.
    rng = np.random.default_rng(25)
    original = Image.new("RGBA", (4500, 5400), (21, 35, 67, 255))
    band = Image.fromarray(rng.integers(0, 256, (300, 4500, 4), dtype=np.uint8))
    original.paste(band, (0, 0))
    raw = io.BytesIO(); original.save(raw, "PNG")
    import pyvips
    source = pyvips.Image.new_from_buffer(raw.getvalue(), "", access="sequential")
    record = output_store.save_vips_print(source, tmp_path, "lossless", {
        "background": "#000000", "prompt": "test fixture", "account": "fixture",
    })
    assert 4_000_000 < record["bytes"] < 25_000_000
    with Image.open(record["path"]) as saved:
        assert saved.mode == "RGBA" and saved.tobytes() == original.tobytes()
    assert output_store.list_designs(tmp_path) == [record]


def test_25mb_ceiling_never_quantizes_or_leaves_partial_archive(tmp_path, monkeypatch):
    import pyvips
    monkeypatch.setattr(output_store, "P2P_MAX_BYTES", 10)
    image = pyvips.Image.black(4500, 5400, bands=4).copy(interpretation="srgb")
    with pytest.raises(ValueError, match="25 MB"):
        output_store.save_vips_print(image, tmp_path, "oversized", {
            "background": "#000000", "prompt": "fixture", "account": "fixture",
        })
    assert not list(tmp_path.rglob("*.png")) and not list(tmp_path.rglob("*.part"))
    assert not output_store.list_designs(tmp_path)


def test_history_outbox_failure_rolls_back_png_thumbnail_and_job(worker, monkeypatch):
    client, module, pool, metrics = worker
    original = output_store.publish_event
    def fail(conn, event):
        if event["type"] == "design.saved":
            raise OSError("History outbox fixture failure")
        original(conn, event)
    monkeypatch.setattr(output_store, "publish_event", fail)
    result = finish(client, create_upload_start(client, 1))
    assert result["completed"] == 0 and result["failed"] == 1
    assert not output_store.list_designs(module.OUT_DIR)
    assert not list(module.OUT_DIR.rglob("*.png"))
    assert not list(module.OUT_DIR.rglob("*.part"))


def test_auth_mock_missing_vips_and_invalid_input_are_never_fake_success(worker, monkeypatch):
    client, module, pool, metrics = worker
    payload = {"items": [{"id": "one", "name": "a.png"}]}
    assert client.post("/api/bridge/p2p/batches", json={**payload, "outputsPerInput": 3}).status_code == 422
    assert client.post("/api/bridge/p2p/batches", json={**payload, "variants": 3}).status_code == 422
    for background in ("invalid", "#808080", "#123ABC"):
        assert client.post("/api/bridge/p2p/batches", json={**payload, "background": background}).status_code == 422
    assert client.post("/api/bridge/p2p/batches", json={"items": []}).status_code == 422
    assert client.post("/api/bridge/p2p/batches", json={"items": payload["items"] * 2}).status_code == 422
    for account in pool.accounts:
        account.is_pro = False
    assert client.post("/api/bridge/p2p/batches", json=payload).status_code == 409
    monkeypatch.setattr(gemini_pool, "MOCK", True)
    assert client.post("/api/bridge/p2p/batches", json=payload).status_code == 409
    monkeypatch.setattr(gemini_pool, "MOCK", False)
    monkeypatch.setattr(image_lab, "HAS_VIPS", False)
    assert client.post("/api/bridge/p2p/batches", json=payload).status_code == 409
    assert not metrics["calls"] and not output_store.list_designs(module.OUT_DIR)


def test_worker_restart_marks_uncertain_jobs_failed_without_losing_archives(worker):
    client, module, pool, metrics = worker
    done = finish(client, create_upload_start(client, 1))
    pending = p2p_batches.create(module.OUT_DIR, p2p_batches.BatchRequest(items=[p2p_batches.BatchItem(id="interrupted", name="interrupted.png")]))
    p2p_batches.recover(module.OUT_DIR)
    recovered = p2p_batches.snapshot(module.OUT_DIR, pending["id"])
    assert recovered["status"] == "failed" and "restarted" in recovered["items"][0]["reason"]
    assert p2p_batches.snapshot(module.OUT_DIR, done["id"])["completed"] == 1
    assert len(output_store.list_designs(module.OUT_DIR)) == 1 and len(metrics["calls"]) == 1


def test_listing_hub_stores_real_design_association_and_rejects_unknown_ids(worker):
    client, module, pool, metrics = worker
    done = finish(client, create_upload_start(client, 1))
    record = done["items"][0]["design"]
    response = client.post("/api/bridge/listings", json={"niche": "fixture", "title": "Fixture", "designId": record["id"]})
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["design_id"] == record["id"] and item["design_path"] == record["path"]
    assert Path(item["design_path"]).is_file()
    assert client.post("/api/bridge/listings", json={"designId": "missing"}).status_code == 404
    assert any(row["design_id"] == record["id"] for row in client.get("/api/bridge/listings").json()["items"])


def test_parallel_leases_balance_accounts_and_respect_slot_ceiling(worker,monkeypatch):
    client, module, pool, metrics = worker
    monkeypatch.setattr(gemini_pool,"SLOTS_PER_ACCOUNT",3)
    pool.accounts[2].is_pro = False
    active = {1:0, 2:0}
    peak = {1:0, 2:0}
    lock = threading.Lock()
    cohort = threading.Barrier(6)
    def acquire(_):
        account = pool.acquire(parallel=True, require_pro=True, timeout=5)
        try:
            with lock:
                active[account.id] += 1
                peak[account.id] = max(peak[account.id],active[account.id])
            cohort.wait(timeout=5)
            time.sleep(0.02)
        finally:
            with lock:
                active[account.id] -= 1
            pool.release(account.id)
        return account.id
    with ThreadPoolExecutor(max_workers=6) as executor:
        ids = list(executor.map(acquire, range(6)))
    assert set(ids) == {1, 2} and peak == {1:3,2:3}
    assert pool._busy == {}


GATE1_REASON = "GATE 1 FAILED: the reference thumbnail never rendered in the Gemini input box"


def test_retry_only_failed_job_retains_source_and_never_duplicates_successful_archives(worker, monkeypatch):
    client, module, pool, metrics = worker
    calls, call_lock = [], threading.Lock()
    retry_entered, release = threading.Event(), threading.Event()
    def render(reference, background, dispatch):
        with call_lock:
            calls.append(reference)
            attempt = len(calls)
        dispatch("TEST FIXTURE ONLY")
        if attempt == 1:
            raise RuntimeError(GATE1_REASON)
        if attempt > 3:
            retry_entered.set()
            assert release.wait(20)
        return reference, "TEST FIXTURE ONLY", gemini_pool.donepixel_prompt(background)
    monkeypatch.setattr(gemini_pool, "render_donepixel", render)
    batch_id = create_upload_start(client, 3)
    first = finish(client, batch_id)
    assert first["status"] == "partial" and first["completed"] == 2 and first["failed"] == 1
    failed = next(job for job in first["items"] if job["state"] == "failed")
    assert failed["reason"] == GATE1_REASON and failed["retryable"]
    source = module.OUT_DIR / ".p2p-inputs" / batch_id / failed["id"]
    assert source.read_bytes() == reference_png()
    saved = {job["id"]: job["design"]["id"] for job in first["items"] if job["design"]}
    url = f'/api/bridge/p2p/batches/{batch_id}/items/{failed["id"]}/retry'
    try:
        response = client.post(url)
        assert response.status_code == 200, response.text
        assert retry_entered.wait(5)
        with ThreadPoolExecutor(max_workers=6) as executor:
            assert all(status == 200 for status in executor.map(lambda _: client.post(url).status_code, range(12)))
        assert len(calls) == 4, "only the one failed input may be dispatched again"
    finally:
        release.set()
    done = finish(client, batch_id)
    assert done["status"] == "completed" and done["completed"] == 3 and done["failed"] == 0
    assert {job["id"]: job["design"]["id"] for job in done["items"] if job["id"] in saved} == saved
    assert len(output_store.list_designs(module.OUT_DIR)) == 3
    assert len(list(module.OUT_DIR.glob("p2p_*.png"))) == 3
    assert len([event for _, event in output_store.read_events(module.OUT_DIR, 0) if event["type"] == "design.saved"]) == 3
    assert not source.exists()
    assert client.post(url).status_code == 200  # late/lost duplicate response is a no-op too
    assert len(calls) == 4 and len(output_store.list_designs(module.OUT_DIR)) == 3


def test_failed_and_interrupted_retry_survive_restart_without_automatic_paid_dispatch(worker, monkeypatch):
    client, module, pool, metrics = worker
    original = gemini_pool.render_donepixel
    def fail(*args):
        raise RuntimeError(GATE1_REASON)
    monkeypatch.setattr(gemini_pool, "render_donepixel", fail)
    batch_id = create_upload_start(client, 1)
    failed = finish(client, batch_id)
    assert failed["status"] == "failed" and not failed["completed"]
    job = failed["items"][0]
    assert job["reason"] == GATE1_REASON and job["retryable"]
    assert not output_store.list_designs(module.OUT_DIR)
    assert p2p_batches._claim_retry(module.OUT_DIR, batch_id, job["id"], False)
    assert p2p_batches.snapshot(module.OUT_DIR, batch_id)["status"] == "retry-uploading"
    partial = module.OUT_DIR / ".p2p-inputs" / batch_id / f'{job["id"]}.interrupted.part'
    partial.write_bytes(b"not a complete original")
    p2p_batches.recover(module.OUT_DIR)
    recovered = p2p_batches.snapshot(module.OUT_DIR, batch_id)
    assert recovered["status"] == "failed" and recovered["items"][0]["retryable"]
    assert "Worker restarted" in recovered["items"][0]["reason"]
    assert not partial.exists() and not metrics["calls"]
    monkeypatch.setattr(gemini_pool, "render_donepixel", original)
    response = client.post(f'/api/bridge/p2p/batches/{batch_id}/items/{job["id"]}/retry')
    assert response.status_code == 200, response.text
    done = finish(client, batch_id)
    assert done["completed"] == 1 and len(metrics["calls"]) == 1
    assert len(output_store.list_designs(module.OUT_DIR)) == 1


def test_retry_missing_legacy_upload_requires_real_reselection_and_validates_binary(worker):
    client, module, pool, metrics = worker
    batch_id = create_upload_start(client, 1, corrupt=True)
    first = finish(client, batch_id)
    job = first["items"][0]
    assert first["status"] == "failed" and not job["retryable"]
    url = f'/api/bridge/p2p/batches/{batch_id}/items/{job["id"]}/retry'
    assert client.post(url).status_code == 409
    bad = client.post(url, content=b"still not an image", headers={"Content-Type": "application/octet-stream"})
    assert bad.status_code == 400 and not metrics["calls"]
    assert p2p_batches.snapshot(module.OUT_DIR, batch_id)["status"] == "failed"
    valid = client.post(url, content=reference_png(), headers={"Content-Type": "application/octet-stream"})
    assert valid.status_code == 200, valid.text
    done = finish(client, batch_id)
    assert done["items"][0]["id"] == job["id"] and done["completed"] == 1
    assert len(metrics["calls"]) == len(output_store.list_designs(module.OUT_DIR)) == 1
    assert not list(module.OUT_DIR.rglob("*.part"))
    assert client.post(f'/api/bridge/p2p/batches/{batch_id}/items/not-a-job/retry').status_code == 404


def test_failed_replacement_preserves_original_and_retry_honours_production_guards(worker, monkeypatch):
    client, module, pool, metrics = worker
    original = gemini_pool.render_donepixel
    monkeypatch.setattr(gemini_pool, "render_donepixel", lambda *args: (_ for _ in ()).throw(RuntimeError(GATE1_REASON)))
    batch_id = create_upload_start(client, 1)
    job = finish(client, batch_id)["items"][0]
    source = module.OUT_DIR / ".p2p-inputs" / batch_id / job["id"]
    url = f'/api/bridge/p2p/batches/{batch_id}/items/{job["id"]}/retry'
    monkeypatch.setattr(p2p_batches, "MAX_UPLOAD_BYTES", 10)
    assert client.post(url, content=reference_png(), headers={"Content-Type": "application/octet-stream"}).status_code == 413
    assert source.read_bytes() == reference_png() and not list(module.OUT_DIR.rglob("*.part"))
    monkeypatch.setattr(gemini_pool, "MOCK", True)
    assert client.post(url).status_code == 409
    monkeypatch.setattr(gemini_pool, "MOCK", False)
    for account in pool.accounts:
        account.is_pro = False
    assert client.post(url).status_code == 409
    for account in pool.accounts:
        account.is_pro = True
    monkeypatch.setattr(gemini_pool, "render_donepixel", original)
    assert client.post(url).status_code == 200
    assert finish(client, batch_id)["completed"] == 1
    assert len(output_store.list_designs(module.OUT_DIR)) == 1


def test_zero_images_never_become_completed(worker, monkeypatch):
    client, module, pool, metrics = worker
    class EmptyProvider:
        def generate_image(self, *args):
            return b""
    monkeypatch.setattr(gemini_pool, "client_for", lambda _: EmptyProvider())
    first = finish(client, create_upload_start(client, 1))
    assert first["status"] == "failed" and first["completed"] == 0
    assert first["items"][0]["reason"] == "Provider returned no image"
    assert not output_store.list_designs(module.OUT_DIR)


def test_long_exact_failure_reason_is_not_truncated_or_replaced_with_zero_images(worker, monkeypatch):
    client, module, pool, metrics = worker
    reason = GATE1_REASON + "\n" + "Échec réel détaillé. " * 80
    def fail(*args):
        raise RuntimeError(reason)
    monkeypatch.setattr(gemini_pool, "render_donepixel", fail)
    second = finish(client, create_upload_start(client, 1))
    assert second["status"] == "failed" and second["items"][0]["reason"] == reason
    assert not output_store.list_designs(module.OUT_DIR)



@pytest.mark.parametrize("size",[5,10])
def test_single_pro_account_scales_to_batch_size_in_separate_leased_slots(worker,monkeypatch,size):
    client,module,pool,metrics=worker
    pool.accounts=pool.accounts[:1]
    monkeypatch.setattr(gemini_pool,"SLOTS_PER_ACCOUNT",10)
    monkeypatch.setattr(p2p_batches,"MAX_PARALLEL",10)
    batch_id=create_upload_start(client,size)
    batch=finish(client,batch_id)
    assert batch["status"]=="completed" and batch["completed"]==size
    assert len(metrics["calls"])==size and metrics["peak"]==size
    assert metrics["peak_by_account"]=={pool.accounts[0].id:size}
    assert pool._busy=={}


def test_same_account_window_budget_reserves_inflight_slots_before_dispatch(worker,monkeypatch):
    client,module,pool,metrics=worker
    pool.accounts=pool.accounts[:1]
    monkeypatch.setattr(gemini_pool,"SLOTS_PER_ACCOUNT",10)
    monkeypatch.setattr(gemini_pool,"WINDOW_CAP",4)
    pool.accounts[0].window_usage=3
    first=pool.acquire(parallel=True,require_pro=True)
    assert first.id==pool.accounts[0].id and pool._busy[first.id]==1
    assert pool.ready_pro_count()==1, "eligible account identities still include in-flight capacity; acquire must wait rather than overspend"
    with pytest.raises(RuntimeError,match="Timed out waiting"):
        pool.acquire(parallel=True,require_pro=True,timeout=0)
    assert pool.note_render(first.id) is True
    pool.release(first.id)
    assert pool.accounts[0].status=="cooldown" and not pool._busy


def test_sibling_quota_cooldown_cannot_be_cleared_by_successful_inflight_render(worker):
    client,module,pool,metrics=worker
    pool.accounts=pool.accounts[:1]
    account=pool.acquire(parallel=True,require_pro=True)
    sibling=pool.acquire(parallel=True,require_pro=True)
    assert account.id==sibling.id and pool._busy[account.id]==2
    pool.degrade(account.id)
    pool.note_render(sibling.id)
    pool.release(sibling.id);pool.release(account.id)
    assert pool.accounts[account.id-1].status=="cooldown"
    assert pool.accounts[account.id-1].window_usage>=gemini_pool.WINDOW_CAP
    assert not pool._busy



def test_acquiring_an_additional_same_account_lease_staggers_the_tab_start(worker, monkeypatch):
    _, _, pool, _ = worker
    pool.accounts = pool.accounts[:1]
    monkeypatch.setattr(gemini_pool, "SLOTS_PER_ACCOUNT", 2)
    monkeypatch.setattr(gemini_pool, "TAB_LAUNCH_STAGGER_SECONDS", 0.08)
    first = pool.acquire(parallel=True, require_pro=True)
    started = time.monotonic()
    second = pool.acquire(parallel=True, require_pro=True, timeout=1)
    assert second.id == first.id
    assert time.monotonic() - started >= 0.07
    assert pool._busy[first.id] == 2
    pool.release(second.id); pool.release(first.id)
    assert not pool._busy and not pool._tab_launch_deadlines


def test_parallel_tabs_bypass_serial_jitter_and_share_an_immediate_deadline(worker,monkeypatch):
    client,module,pool,metrics=worker
    pool.accounts=pool.accounts[:1]
    monkeypatch.setattr(gemini_pool,"SLOTS_PER_ACCOUNT",4)
    monkeypatch.setattr(gemini_pool,"JITTER",(0.08,0.08))
    monkeypatch.setattr(pool,"_wait_jitter",gemini_pool.AccountPool._wait_jitter.__get__(pool))
    pool.accounts[0].last_used=time.time()
    leases=[pool.acquire(parallel=True,require_pro=True) for _ in range(4)]
    assert {account.id for account in leases}=={pool.accounts[0].id}
    assert pool._jitter_deadlines[pool.accounts[0].id]<=time.monotonic()+0.01
    release=threading.Barrier(4)
    starts=[];lock=threading.Lock()
    def await_shared_deadline(account):
        release.wait(timeout=3)
        pool._wait_jitter(account)
        with lock: starts.append(time.monotonic())
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures=[executor.submit(await_shared_deadline,account) for account in leases]
        for future in futures: future.result(timeout=5)
    assert max(starts)-min(starts)<0.025, "same-account start jitter serialized concurrent tabs"
    assert pool._jitter_released=={pool.accounts[0].id}
    for account in leases: pool.release(account.id)
    assert pool._jitter_deadlines=={} and pool._busy=={}



def test_sibling_generic_fault_cannot_shorten_real_window_cooldown(worker,monkeypatch):
    client,module,pool,metrics=worker
    pool.accounts=pool.accounts[:1]
    first=pool.acquire(parallel=True,require_pro=True)
    sibling=pool.acquire(parallel=True,require_pro=True)
    pool.degrade(first.id)
    real_until=pool.accounts[0].cooldown_until
    pool.fault(sibling.id)
    assert pool.accounts[0].cooldown_until>=real_until
    assert pool.accounts[0].status=="cooldown"
    pool.release(first.id);pool.release(sibling.id)
    assert pool.accounts[0].cooldown_until>=real_until and not pool._busy


def test_sibling_degradation_cannot_overwrite_verified_auth_required_state(worker):
    client,module,pool,metrics=worker
    pool.accounts=pool.accounts[:1]
    first=pool.acquire(parallel=True,require_pro=True)
    sibling=pool.acquire(parallel=True,require_pro=True)
    pool.fault(first.id,auth=True)
    pool.degrade(sibling.id)
    pool.fault(sibling.id)
    pool.note_render(first.id)
    pool.release(first.id);pool.release(sibling.id)
    account=pool.accounts[0]
    assert account.status=="auth-required" and not account.is_pro
    assert not pool._busy
