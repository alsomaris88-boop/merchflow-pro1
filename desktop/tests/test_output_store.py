import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from desktop.worker import output_store


def test_verified_png_has_embedded_dpi_and_persistent_history(tmp_path):
    image = Image.new("RGBA", (120, 160), (20, 100, 200, 140))
    raw, design = output_store.save_image(image, tmp_path, "../../same:name", {"source": "prepare"})
    assert design["saved"] and design["bytes"] == len(raw)
    assert Path(design["path"]).parent == tmp_path
    assert Path(design["path"]).read_bytes() == raw
    with Image.open(io.BytesIO(raw)) as check:
        assert check.format == "PNG" and check.size == (120, 160)
        assert check.mode == "RGBA" and check.getpixel((0, 0))[3] == 140
        assert check.info["dpi"] == pytest.approx((300, 300), abs=0.02)
    with Image.open(io.BytesIO(raw)) as check:
        check.verify()
    assert output_store.list_designs(tmp_path) == [design]
    assert output_store.get_design(tmp_path, design["id"]) == design
    assert output_store.file_path(tmp_path, design, preview=True).is_file()


def test_write_failure_never_registers_or_leaves_a_partial_png(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(Image.Image, "save", fail)
    with pytest.raises(OSError, match="disk full"):
        output_store.save_image(Image.new("RGBA", (10, 10)), tmp_path, "failed")
    assert output_store.list_designs(tmp_path) == []
    assert not list(tmp_path.rglob("*.png")) and not list(tmp_path.glob("*.part"))


def test_registry_failure_rolls_back_files(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("registry unavailable")
    monkeypatch.setattr(output_store, "_connect", fail)
    with pytest.raises(OSError, match="registry unavailable"):
        output_store.save_image(Image.new("RGBA", (10, 10)), tmp_path, "failed")
    assert not list(tmp_path.rglob("*.png"))


def test_duplicate_names_are_unique_under_concurrent_saves(tmp_path):
    def save(_):
        return output_store.save_image(Image.new("RGBA", (12, 18), "red"), tmp_path, "same")[1]
    with ThreadPoolExecutor(max_workers=4) as executor:
        designs = list(executor.map(save, range(16)))
    assert len({r["id"] for r in designs}) == 16
    assert len({r["path"] for r in designs}) == 16
    assert len(output_store.list_designs(tmp_path)) == 16


def test_stream_zip_contains_all_55_duplicate_named_images_with_intact_pngs(tmp_path):
    records = [output_store.save_image(Image.new("RGBA", (12, 18), (i, 20, 30, 255)), tmp_path, "same")[1] for i in range(55)]
    chunks = list(output_store.stream_zip(tmp_path, records))
    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        assert len(archive.namelist()) == 55
        assert len(set(archive.namelist())) == 55
        assert all("300dpi" in name for name in archive.namelist())
        assert archive.testzip() is None
        for filename, record in zip(archive.namelist(), records):
            assert archive.read(filename) == Path(record["path"]).read_bytes()
            with Image.open(io.BytesIO(archive.read(filename))) as image:
                assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)


def test_streaming_zip_does_not_buffer_a_large_png(tmp_path):
    rng = np.random.default_rng(42)
    image = Image.fromarray(rng.integers(0, 256, (600, 600, 4), dtype=np.uint8))
    _, record = output_store.save_image(image, tmp_path, "large")
    assert record["bytes"] > 1024 * 1024
    chunks = list(output_store.stream_zip(tmp_path, [record]))
    assert max(map(len, chunks)) <= 256 * 1024
    assert sum(map(len, chunks)) > record["bytes"]


def test_deleted_history_leaves_user_png_untouched(tmp_path):
    _, design = output_store.save_image(Image.new("RGBA", (10, 10)), tmp_path, "keep")
    output_store.delete_records(tmp_path, [design["id"]])
    assert output_store.list_designs(tmp_path) == []
    assert Path(design["path"]).is_file()
    with pytest.raises(FileNotFoundError):
        output_store.get_design(tmp_path, design["id"])


def test_missing_file_is_not_reported_as_saved_history(tmp_path):
    _, design = output_store.save_image(Image.new("RGBA", (10, 10)), tmp_path, "missing")
    Path(design["path"]).unlink()
    assert output_store.list_designs(tmp_path) == []
    with pytest.raises(FileNotFoundError):
        output_store.get_design(tmp_path, design["id"])


def test_invalid_input_and_outside_paths_are_rejected(tmp_path):
    with pytest.raises(Exception):
        output_store.save_raw(b"not an image", tmp_path, "bad")
    with pytest.raises(ValueError):
        output_store.file_path(tmp_path, {"path": str(tmp_path.parent / "secret.png")})
    assert output_store.list_designs(tmp_path) == []
