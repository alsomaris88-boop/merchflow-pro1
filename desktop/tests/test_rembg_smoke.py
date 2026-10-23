"""Opt-in REAL U2Net/ONNX smoke on a public-domain NASA astronaut image.

Run MERCHFLOW_TEST_REMBG=1 python -m pytest desktop/tests/test_rembg_smoke.py
First run downloads official rembg model weights (internet access required).
This is not a provider-generation test and uses no account or paid service.
"""
import os

import numpy as np
import pytest
from PIL import Image

from desktop.worker import image_lab

pytestmark = pytest.mark.skipif(os.environ.get("MERCHFLOW_TEST_REMBG") != "1", reason="opt-in real ONNX model download")


def test_real_u2net_on_orange_navy_and_textured_backdrops(tmp_path):
    from skimage.data import astronaut
    source = Image.fromarray(astronaut()).convert("RGBA")
    foreground = image_lab.remove_background(source, "u2net")
    for label, colour in [("orange", (230, 100, 10, 255)), ("navy", (5, 15, 55, 255)), ("texture", None)]:
        if colour:
            background = Image.new("RGBA", foreground.size, colour)
        else:
            rng = np.random.default_rng(4)
            arr = np.full((foreground.height, foreground.width, 4), 255, dtype=np.uint8)
            arr[..., :3] = rng.integers(10, 230, arr[..., :3].shape, dtype=np.uint8)
            background = Image.fromarray(arr)
        background.alpha_composite(foreground)
        cutout = image_lab.choke_alpha(image_lab.remove_background(background, "u2net"), 1)
        alpha = np.asarray(cutout.getchannel("A"))
        assert np.mean(alpha > 240) > 0.05, label
        assert np.mean(alpha == 0) > 0.10, label
        assert cutout.getpixel((0, 0))[3] == 0, label
        # Retain the actual smoke artifacts for optional visual review.
        cutout.save(tmp_path / f"real-u2net-{label}.png", dpi=(300, 300))
