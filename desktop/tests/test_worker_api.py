import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from desktop.worker import image_lab, output_store


def image_data():
    buffer = io.BytesIO()
    Image.new("RGBA", (64, 40), "red").save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def worker(tmp_path, monkeypatch):
    # No authenticated provider or production account state is used by tests.
    monkeypatch.setenv("MERCHFLOW_DB", str(tmp_path / "test.db"))
    from desktop.worker import app as module
    monkeypatch.setattr(module, "OUT_DIR", tmp_path / "output")
    monkeypatch.setattr(module.db, "DB_PATH", tmp_path / "test.db")
    module.db.init()
    monkeypatch.setattr(module.gemini_pool, "MOCK", False)
    monkeypatch.setattr(module.gemini_pool, "client_for", lambda account_id: None)
    module._ZIP_TICKETS.clear()
    with TestClient(module.app) as client:
        yield client, module


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def fake_provider(module, monkeypatch):
    account = SimpleNamespace(id=1, label="Test fixture provider")
    pool = SimpleNamespace(accounts=[account], acquire=lambda: account, release=lambda i: None, _wait_jitter=lambda a: None, note_render=lambda i: False)
    raw = base64.b64decode(image_data().split(",")[1])
    provider = SimpleNamespace(generate_image=lambda prompt, references: raw)
    monkeypatch.setattr(module.gemini_pool, "POOL", pool)
    monkeypatch.setattr(module.gemini_pool, "client_for", lambda account_id: provider)
    monkeypatch.setattr(image_lab, "remove_background", lambda image, model: image)
    return provider


def test_save_history_download_and_streaming_zip_roundtrip(worker):
    client, module = worker
    response = client.post("/api/bridge/designs", json={"image": image_data(), "name": "real-output", "meta": {"source": "prepare"}})
    assert response.status_code == 200
    design = response.json()["design"]
    assert Path(design["path"]).read_bytes() == client.get(design["fileUrl"]).content
    assert client.get("/api/bridge/designs").json()["items"] == [design]
    assert client.get(design["previewUrl"]).headers["content-type"] == "image/png"
    ticket = client.post("/api/bridge/export", json={"ids": [design["id"]]}).json()
    response = client.get(ticket["url"])
    assert response.status_code == 200 and response.content.startswith(b"PK")
    assert "attachment" in response.headers["content-disposition"]
    assert "content-length" not in response.headers  # actual streaming, not archive-sized buffering
    client.request("DELETE", "/api/bridge/designs", json={"ids": [design["id"]]})
    assert client.get("/api/bridge/designs").json()["items"] == []
    assert Path(design["path"]).is_file()  # history clear never deletes user files


def test_invalid_images_and_arbitrary_download_paths_are_rejected(worker):
    client, module = worker
    assert client.post("/process", json={"image": "%%%"}).status_code == 400
    assert client.post("/api/bridge/designs", json={"image": ""}).status_code == 413
    assert client.post("/api/bridge/export", json={"ids": ["../../secret"]}).status_code == 404
    assert client.get("/api/bridge/designs/not-a-record").status_code == 404
    assert client.get("/api/bridge/export?ticket=unknown").status_code == 404
    assert output_store.list_designs(module.OUT_DIR) == []


def test_process_returns_error_not_fake_success_on_disk_failure(worker, monkeypatch):
    client, module = worker
    def fail(*args, **kwargs):
        raise OSError("disk is read-only")
    monkeypatch.setattr(image_lab, "process_design", fail)
    response = client.post("/process", json={"image": image_data()})
    assert response.status_code == 500 and response.json()["ok"] is False
    assert "read-only" in response.json()["reason"]
    assert client.get("/api/bridge/designs").json()["items"] == []


def test_prepare_api_uses_streamed_libvips_save_and_returns_verified_history_record(worker):
    client, module = worker
    response = client.post("/api/bridge/process", json={
        "image": image_data(), "name": "streamed-api",
        "settings": {"remove": False, "resizeForPrint": False, "despeckle": False},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    design = body["design"]
    assert design["saved"] is True and design["w"] == 64 and design["h"] == 40
    assert body["report"]["engine"] == "pyvips-streaming"
    assert Path(design["path"]).is_file() and Path(design["previewPath"]).is_file()
    assert client.get("/api/bridge/designs").json()["items"] == [design]
    with Image.open(design["path"]) as output:
        assert output.size == (64, 40) and output.mode == "RGBA"
        assert output.info["dpi"] == pytest.approx((300, 300), abs=0.02)


def test_prepare_api_processes_history_design_from_worker_disk_by_id(worker):
    client, module = worker
    saved_response = client.post("/api/bridge/designs", json={
        "image": image_data(), "name": "original-history-design",
    })
    assert saved_response.status_code == 200, saved_response.text
    original = saved_response.json()["design"]
    original_bytes = Path(original["path"]).read_bytes()

    # No image/Base64 field: the worker resolves the registered ID and reads
    # the original PNG from its own disk before running the normal pipeline.
    response = client.post("/api/bridge/process", json={
        "designId": original["id"], "name": "history-direct-process",
        "settings": {"remove": False, "resizeForPrint": False, "despeckle": False},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    processed = body["design"]
    assert processed["id"] != original["id"]
    assert processed["w"] == 64 and processed["h"] == 40
    assert body["report"]["engine"] == "pyvips-streaming"
    assert Path(processed["path"]).is_file()
    assert Path(original["path"]).read_bytes() == original_bytes
    assert len(output_store.list_designs(module.OUT_DIR)) == 2


def test_prepare_expands_native_history_after_cutout_and_choke(worker):
    client, module = worker
    native = Image.new("RGBA", (64, 40), (250, 120, 20, 255))
    from PIL import ImageDraw
    ImageDraw.Draw(native).rectangle((18, 8, 46, 32), fill=(240, 20, 180, 255))
    buffer = io.BytesIO(); native.save(buffer, "PNG")
    saved = client.post("/api/bridge/designs", json={
        "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
        "name": "native-history",
    })
    assert saved.status_code == 200, saved.text
    original = saved.json()["design"]
    assert (original["w"], original["h"]) == (64, 40)

    response = client.post("/api/bridge/process", json={
        "designId": original["id"], "name": "native-prepare",
        "settings": {"remove": True, "mode": "without_ai", "precision": "keep_holes",
                      "colorTolerance": 24, "despeckle": False, "defringe": 1,
                      "resizeForPrint": True},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    prepared = body["design"]
    assert (prepared["w"], prepared["h"], prepared["dpi"]) == (4500, 5400, 300)
    assert (prepared["nativeW"], prepared["nativeH"]) == (64, 40)
    assert Path(original["path"]).is_file() and Path(prepared["path"]).is_file()
    with Image.open(prepared["path"]) as output:
        assert output.size == (4500, 5400) and output.mode == "RGBA"
        assert output.info["dpi"] == pytest.approx((300, 300), abs=0.02)


def test_empty_background_cutout_returns_actionable_client_error_not_500(worker):
    client, module = worker
    native = Image.new("RGBA", (64, 40), (0, 0, 0, 255))
    buffer = io.BytesIO(); native.save(buffer, "PNG")
    response = client.post("/api/bridge/process", json={
        "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
        "settings": {"remove": True, "mode": "without_ai", "resizeForPrint": True},
    })
    assert response.status_code == 422
    assert response.json() == {
        "ok": False,
        "reason": "Subject matches the border color. Try With AI mode or lower color tolerance.",
    }
    assert output_store.list_designs(module.OUT_DIR) == []


def test_prepare_api_returns_not_found_for_unknown_history_design_id(worker):
    client, _ = worker
    response = client.post("/api/bridge/process", json={
        "designId": "does-not-exist",
        "settings": {"remove": False},
    })
    assert response.status_code == 404
    assert "not in history" in response.json()["reason"]


def test_no_authenticated_provider_never_creates_a_design(worker):
    client, module = worker
    payload = {"styleImage": image_data(), "nicheImages": [{"id": "one", "image": image_data()}]}
    assert client.post("/generate/stream", json=payload).status_code == 409
    assert client.post("/p2p", json=payload).status_code == 409
    assert output_store.list_designs(module.OUT_DIR) == []


def test_explicit_mock_mode_is_blocked_for_production(worker, monkeypatch):
    client, module = worker
    monkeypatch.setattr(module.gemini_pool, "MOCK", True)
    response = client.post("/generate/stream", json={"styleImage": image_data(), "nicheImages": [{"id": "one", "image": image_data()}]})
    assert response.status_code == 409 and "Mock" in response.json()["detail"]
    assert output_store.list_designs(module.OUT_DIR) == []


def test_provider_result_is_archived_before_a_success_event(worker, monkeypatch):
    client, module = worker
    fake_provider(module, monkeypatch)
    response = client.post("/api/bridge/generate", json={"styleImage": image_data(), "nicheImages": [{"id": "one", "name": "fixture", "image": image_data()}]})
    assert response.status_code == 200
    stream = events(response)
    image = next(event for event in stream if event["type"] == "image")
    assert image["saved"] and Path(image["path"]).is_file()
    assert image["w"] == 4500 and image["h"] == 5400
    assert client.get(image["fileUrl"]).content.startswith(output_store.PNG_SIGNATURE)
    assert client.get("/api/bridge/designs").json()["items"][0]["id"] == image["design"]["id"]
    assert stream[-1] == {"type": "done", "completed": 1, "failed": 0}


def test_failed_postprocessing_produces_failed_event_not_image(worker, monkeypatch):
    client, module = worker
    fake_provider(module, monkeypatch)
    def fail(*args):
        raise OSError("PNG write failed")
    monkeypatch.setattr(image_lab, "postprocess_generate", fail)
    response = client.post("/generate/stream", json={"styleImage": image_data(), "nicheImages": [{"id": "one", "image": image_data()}]})
    stream = events(response)
    assert not any(event["type"] == "image" for event in stream)
    assert any(event["type"] == "failed" and "PNG write failed" in event["reason"] for event in stream)
    assert stream[-1] == {"type": "done", "completed": 0, "failed": 1}
    assert output_store.list_designs(module.OUT_DIR) == []


def test_fusion_injects_exact_client_directive_and_upload_order(worker, monkeypatch):
    """Module 01 contract: the mandatory Style × Niche directive goes out
    VERBATIM on every fusion job, with the niche uploaded as IMAGE 1 and the
    style reference as IMAGE 2."""
    client, module = worker
    calls = []
    account = SimpleNamespace(id=1, label="Test fixture provider")
    pool = SimpleNamespace(accounts=[account], acquire=lambda: account, _wait_jitter=lambda a: None, note_render=lambda i: False)
    raw = base64.b64decode(image_data().split(",")[1])
    monkeypatch.setattr(module.gemini_pool, "POOL", pool)
    monkeypatch.setattr(module.gemini_pool, "client_for", lambda account_id: SimpleNamespace(
        generate_image=lambda prompt, references: (calls.append((prompt, references)), raw)[1]))
    monkeypatch.setattr(image_lab, "remove_background", lambda image, model: image)
    payload = {"styleImage": image_data(), "background": "black", "aspect": "1:1",
               "nicheImages": [{"id": "one", "name": "fixture", "image": image_data()}]}
    response = client.post("/api/bridge/generate", json=payload)
    assert response.status_code == 200 and calls
    prompt, references = calls[0]
    # The exact client directive body is embedded untouched.
    assert prompt.startswith(module.gemini_pool.FUSION_DIRECTIVE)
    for clause in [
        "[MANDATORY MULTIMODAL DIRECTIVE: STYLE × NICHE COMPLETE FUSION]",
        "IMAGE 1 = The NICHE SUBJECT (The character/object/theme to be drawn)",
        "IMAGE 2 = The ART STYLE REFERENCE (The visual DNA, aesthetic, and medium)",
        "solid pitch-black background (#000000)",
        "Generate the finished master apparel graphic now.",
    ]:
        assert clause in prompt
    # Runtime operator settings arrive as an addendum, never rewriting the directive.
    assert "[RUNTIME OPERATOR SETTINGS" in prompt and "1:1" in prompt
    # Upload order is the contract: niche = IMAGE 1 first, style = IMAGE 2 second.
    assert [name for _, name in references] == ["niche_one_IMAGE1.png", "style_ref_IMAGE2.png"]


def test_pixel_api_uses_recreation_prompt_not_palette_transfer(worker, monkeypatch):
    client, module = worker
    provider = fake_provider(module, monkeypatch)
    calls = []
    def render(prompt, references):
        calls.append((prompt, references))
        return base64.b64decode(image_data().split(",")[1])
    provider.generate_image = render
    response = client.post("/api/bridge/pixel-to-pixel", json={"background": "original", "nicheImages": [{"id": "one", "name": "concept", "image": image_data()}]})
    stream = events(response)
    saved = next(event for event in stream if event["type"] == "image")
    assert max(saved["w"], saved["h"]) == 8192
    assert saved["design"]["source"] == "pixel-to-pixel"
    assert "Recreate" in calls[0][0] and "retain the reference background" in calls[0][0]
    assert len(calls[0][1]) == 1
