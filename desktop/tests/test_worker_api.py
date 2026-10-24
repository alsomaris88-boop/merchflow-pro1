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
    def fail(*args):
        raise OSError("disk is read-only")
    monkeypatch.setattr(image_lab, "process_design", fail)
    response = client.post("/process", json={"image": image_data()})
    assert response.status_code == 500 and response.json()["ok"] is False
    assert "read-only" in response.json()["reason"]
    assert client.get("/api/bridge/designs").json()["items"] == []


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
