"""Deterministic matting fixtures isolate pixel logic; real ONNX smoke is separate."""
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from desktop.worker import image_lab, output_store


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def neural_mask(monkeypatch):
    calls = []
    monkeypatch.setattr(image_lab, "_session", lambda model: model)
    def remove(image, **kwargs):
        calls.append(kwargs)
        result = image.convert("RGBA")
        alpha = Image.new("L", image.size)
        draw = ImageDraw.Draw(alpha)
        draw.rectangle((8, 8, image.width - 9, image.height - 9), fill=255)
        result.putalpha(alpha)
        return result
    monkeypatch.setattr(image_lab, "_rembg_remove", remove)
    return calls


@pytest.mark.parametrize("background", [(255, 100, 0), (0, 0, 80), "texture"])
def test_cutout_uses_neural_inference_not_a_colour_key(background, neural_mask):
    arr = np.full((64, 64, 4), 255, dtype=np.uint8)
    if background == "texture":
        arr[..., :3] = np.random.default_rng(2).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    else:
        arr[..., :3] = background
    arr[10:54, 10:54, :3] = (240, 240, 240)
    result = image_lab.remove_background(Image.fromarray(arr), "birefnet-general")
    assert result.getpixel((0, 0)) == (0, 0, 0, 0)
    assert result.getpixel((32, 32))[3] == 255
    assert neural_mask[0]["session"] == "birefnet-general"
    assert neural_mask[0]["alpha_matting"] is True


def test_existing_transparent_letter_counters_are_never_filled(neural_mask):
    source = Image.new("RGBA", (64, 64), "white")
    source.putpixel((32, 32), (10, 10, 10, 0))
    result = image_lab.remove_background(source)
    assert result.getpixel((32, 32))[3] == 0


@pytest.mark.parametrize("pixels", [1, 2])
def test_cv2_alpha_choke_is_exactly_one_or_two_pixels(pixels):
    source = Image.new("RGBA", (24, 24))
    ImageDraw.Draw(source).rectangle((4, 4, 19, 19), fill="white")
    result = image_lab.choke_alpha(source, pixels)
    assert result.getchannel("A").getbbox() == (4 + pixels, 4 + pixels, 20 - pixels, 20 - pixels)


def test_mixed_edge_rgb_is_decontaminated_without_background_colour_assumptions():
    image = Image.new("RGBA", (12, 12))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 9, 9), fill=(255, 130, 0, 120))
    draw.rectangle((3, 3, 8, 8), fill=(220, 220, 240, 255))
    result = image_lab._decontaminate_edges(image)
    assert result.getpixel((2, 5)) == (220, 220, 240, 120)


def test_model_sessions_are_cached_and_old_model_is_released(monkeypatch):
    calls = []
    def create(name, **kwargs):
        calls.append((name, kwargs))
        return object()
    monkeypatch.setattr(image_lab, "HAS_REMBG", True)
    monkeypatch.setattr(image_lab, "_SESSIONS", {})
    monkeypatch.setattr(image_lab, "_rembg_new_session", create)
    first = image_lab._session("u2net")
    assert image_lab._session("u2net") is first
    image_lab._session("birefnet")
    assert [item[0] for item in calls] == ["u2net", "birefnet-general"]
    assert len(image_lab._SESSIONS) == 1


def test_missing_rembg_is_an_error_never_a_heuristic_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(image_lab, "HAS_REMBG", False)
    with pytest.raises(RuntimeError, match="requires rembg"):
        image_lab.process_design(png_bytes(Image.new("RGBA", (64, 64), "orange")), {}, tmp_path)
    assert output_store.list_designs(tmp_path) == []


def test_inference_failure_and_empty_foreground_fail_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(image_lab, "_session", lambda model: object())
    monkeypatch.setattr(image_lab, "_rembg_remove", lambda image, **kwargs: Image.new("RGBA", image.size))
    with pytest.raises(RuntimeError, match="empty foreground"):
        image_lab.process_design(png_bytes(Image.new("RGBA", (32, 32), "orange")), {}, tmp_path)
    assert output_store.list_designs(tmp_path) == []


def test_prepare_exports_real_4500x5400_png_at_300dpi(tmp_path, neural_mask):
    source = Image.new("RGBA", (64, 64), "orange")
    ImageDraw.Draw(source).rectangle((8, 8, 55, 55), fill="white")
    raw, report = image_lab.process_design(png_bytes(source), {"remove": True, "despeckle": False}, tmp_path, "test-print")
    assert report["saved"] and report["path"] == report["design"]["path"]
    assert report["bytes"] == len(raw)
    assert Path(report["path"]).read_bytes() == raw
    with Image.open(io.BytesIO(raw)) as output:
        assert output.size == (4500, 5400)
        assert output.info["dpi"] == pytest.approx((300, 300), abs=0.02)
        assert output.getpixel((0, 0))[3] == 0
        assert output.getchannel("A").getbbox()[1] == 351  # 350px collar + one export pixel choke


@pytest.mark.parametrize("background", ["transparent", "black", "white", "original"])
def test_pixel_recreation_exports_8k_and_honours_all_backgrounds(tmp_path, monkeypatch, background):
    source = Image.new("RGBA", (80, 40), (40, 90, 160, 255))
    calls = []
    def cutout(image, model):
        calls.append(model)
        result = Image.new("RGBA", image.size)
        ImageDraw.Draw(result).rectangle((20, 10, 59, 29), fill="red")
        return result
    monkeypatch.setattr(image_lab, "remove_background", cutout)
    raw, report = image_lab.pixel_to_pixel(png_bytes(source), {"background": background}, tmp_path, "recreation")
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (8192, 4096)
        assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)
        corner = image.getpixel((0, 0))
        if background == "transparent":
            assert corner[3] == 0
        elif background == "black":
            assert corner == (0, 0, 0, 255)
        elif background == "white":
            assert corner == (255, 255, 255, 255)
        else:
            assert corner == (40, 90, 160, 255) and calls == []
    assert report["nativeW"] == 80 and report["nativeH"] == 40 and report["upscaled"] is True
    assert report["design"]["source"] == "pixel-to-pixel"
    assert len(output_store.list_designs(tmp_path)) == 1


def test_solid_generation_background_is_not_removed_from_final_export(tmp_path, monkeypatch):
    monkeypatch.setattr(image_lab, "remove_background", lambda image, model: image)
    raw, report = image_lab.postprocess_generate(png_bytes(Image.new("RGBA", (16, 16), "red")), {"background": "black", "aspect": "1:1"}, tmp_path, "solid")
    with Image.open(io.BytesIO(raw)) as output:
        assert output.size == (4500, 4500)
        assert output.getpixel((0, 0)) == (0, 0, 0, 255)
    assert report["design"]["background"] == "black"
