"""Real disk/libvips/SQLite tests; provider responses are isolated test doubles.

No paid service, production cookies, or successful live Gemini generation is
claimed by this suite. The production mock switch remains blocked.
"""
import io
import json
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
    config = tmp_path / "accounts.json"
    config.write_text('{"accounts":[]}')
    monkeypatch.setattr(gemini_pool, "CONFIG_PATH", config)
    pool = gemini_pool.AccountPool()
    pool.accounts = [gemini_pool.AccountState(id=i, status="ready", is_pro=True) for i in (1, 2, 3)]
    monkeypatch.setattr(gemini_pool, "POOL", pool)
    monkeypatch.setattr(gemini_pool, "MOCK", False)
    monkeypatch.setattr(pool, "_wait_jitter", lambda account: None)
    lock = threading.Lock()
    metrics = {"active": set(), "peak": 0, "calls": []}
    raw = reference_png()

    class Provider:
        def __init__(self, account_id):
            self.id = account_id

        def generate_image(self, prompt, references):
            with lock:
                assert self.id not in metrics["active"], "Concurrent requests used the same provider session"
                metrics["active"].add(self.id)
                metrics["peak"] = max(metrics["peak"], len(metrics["active"]))
                metrics["calls"].append((self.id, prompt, references))
            try:
                time.sleep(0.025)
                return raw
            finally:
                with lock:
                    metrics["active"].remove(self.id)

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
def test_dynamic_batches_archive_every_real_print_before_success_and_without_browser_download(worker, size):
    client, module, pool, metrics = worker
    batch_id = create_upload_start(client, size, colour="#0A192F")
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
        assert path.parent == module.OUT_DIR and path.name.startswith("p2p_same-design_")
        assert record["bytes"] == path.stat().st_size < 25_000_000
        assert Path(record["previewPath"]).is_file()
        with Image.open(path) as image:
            assert image.size == (4500, 5400) and image.mode == "RGBA"
            assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)
    events = []
    cursor = 0
    while page := output_store.read_events(module.OUT_DIR, cursor):
        events.extend(page)
        cursor = page[-1][0]
    saved = [ev for _, ev in events if ev["type"] == "design.saved"]
    assert len(saved) == size
    assert {event["design"]["id"] for event in saved} == {r["id"] for r in history}
    assert len(metrics["calls"]) == size
    if size > 1:
        assert metrics["peak"] >= 2
        assert len({call[0] for call in metrics["calls"]}) >= 2
    assert not pool._busy
    assert not (module.OUT_DIR / ".p2p-inputs" / batch_id).exists()
    # Retried start is idempotent: no extra provider calls or history records.
    assert client.post(f"/api/bridge/p2p/batches/{batch_id}/start").status_code == 200
    assert len(metrics["calls"]) == size


def test_exact_prompt_and_single_reference_are_sent_to_each_provider(worker):
    client, module, pool, metrics = worker
    finish(client, create_upload_start(client, 2, colour="#FDFBF7"))
    expected = "Recreate this exact uploaded image in ultra-high quality, 8K resolution, and high definition. Maintain 100% fidelity to the original composition, overall style, colors, characters, fonts, and graphic elements. Enhance all textures, sharpen blurry lines, clean up low-resolution artifacts, and make it look like a crisp, professional graphic design ready for high-quality printing. Do not alter or change the elements, just upgrade the visual clarity, sharpness, and quality. Render the clean artwork on a solid background of: #FDFBF7."
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
    monkeypatch.setattr(output_store, "save_vips_print", fail)
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
    assert client.post("/api/bridge/p2p/batches", json={**payload, "background": "invalid"}).status_code == 422
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


def test_parallel_leases_exclude_busy_and_unverified_accounts(worker):
    client, module, pool, metrics = worker
    pool.accounts[2].is_pro = False
    seen = set()
    lock = threading.Lock()
    def acquire(_):
        account = pool.acquire(parallel=True, require_pro=True, timeout=5)
        try:
            with lock:
                assert account.id not in seen
                seen.add(account.id)
            time.sleep(0.02)
        finally:
            with lock:
                seen.remove(account.id)
            pool.release(account.id)
        return account.id
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(acquire, range(20)))
    assert set(ids) == {1, 2} and not pool._busy
