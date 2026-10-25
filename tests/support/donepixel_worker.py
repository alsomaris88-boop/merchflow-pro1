"""EXPLICIT TEST HARNESS: deterministic provider; real libvips/disk/SQLite/SSE.

Never imported by the production engine. Does not contact Gemini or use paid
accounts. All output/DB/account state is confined to a temporary test directory.
Run only for Playwright integration validation, then stop the process.
"""
import os
import hashlib
import io
import threading
from pathlib import Path
import sys
import tempfile
import time

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

with tempfile.TemporaryDirectory(prefix="merchflow-p2p-browser-") as temp:
    root = Path(temp)
    os.environ["MERCHFLOW_OUT"] = str(root / "output")
    os.environ["MERCHFLOW_DB"] = str(root / "app.sqlite")
    from desktop.worker import app, gemini_pool
    import uvicorn

    pool = gemini_pool.AccountPool()
    pool.accounts = [gemini_pool.AccountState(id=i, email="TEST FIXTURE - NO LIVE GEMINI", status="ready", is_pro=True) for i in (1, 2, 3)]
    pool._wait_jitter = lambda account: None
    gemini_pool.POOL = pool

    failed_references: set[str] = set()
    failure_lock = threading.Lock()

    class TestProvider:
        def generate_image(self, prompt, images):
            assert prompt.startswith("Recreate this exact uploaded graphic artwork in ultra-high resolution")
            assert len(images) == 1
            time.sleep(1)  # leave time to observe real pending UI / navigate to History
            raw = images[0][0]
            with Image.open(io.BytesIO(raw)) as image:
                marker = image.convert("RGB").getpixel((0, 0))
            # Explicit fixture-only outcomes selected by test artwork pixels.
            # Never imported by production; no Google call or fake disk archive.
            if marker == (7, 17, 27):
                key = hashlib.sha256(raw).hexdigest()
                with failure_lock:
                    first = key not in failed_references
                    failed_references.add(key)
                if first:
                    raise RuntimeError("GATE 1 FAILED: the reference thumbnail never rendered in the Gemini input box")
            if marker == (8, 18, 28):
                raise RuntimeError("Provider returned no image")
            return raw

    providers = {i: TestProvider() for i in (1, 2, 3)}
    gemini_pool.client_for = providers.get
    print("TEST HARNESS ONLY: fixture provider; no live Gemini. Temporary outputs:", root, flush=True)
    uvicorn.run(app.app, host="0.0.0.0", port=8123, log_level="warning")
