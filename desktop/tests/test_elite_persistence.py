import io
from pathlib import Path

import pytest
from PIL import Image

from desktop.worker import elite_tools, output_store


def source_png():
    buffer = io.BytesIO()
    Image.new("RGBA", (40, 60), (15, 20, 25, 255)).save(buffer, "PNG")
    return buffer.getvalue()


def assert_saved(record):
    assert record["saved"] and record["source"] == "prepare"
    with Image.open(record["path"]) as image:
        assert image.format == "PNG"
        assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)


def test_polycanvas_variants_are_registered_verified_pngs(tmp_path, monkeypatch):
    monkeypatch.setattr(elite_tools, "PRODUCTS", {"fixture": {"label": "Fixture product", "w": 80, "h": 96, "mode": "center", "fill": 0.8, "note": "test"}})
    items = elite_tools.adapt_to_products(source_png(), tmp_path, "product", ["fixture"])
    assert len(items) == 1
    assert_saved(items[0]["design"])
    assert items[0]["path"] == items[0]["design"]["path"]
    assert len(output_store.list_designs(tmp_path)) == 1


def test_garment_split_registers_both_actual_png_files(tmp_path):
    result = elite_tools.generate_dark_light_split(source_png(), tmp_path, "garment", "cream")
    assert_saved(result["light"])
    assert_saved(result["dark"])
    assert len(output_store.list_designs(tmp_path)) == 2
    assert Path(result["light"]["path"]) != Path(result["dark"]["path"])
