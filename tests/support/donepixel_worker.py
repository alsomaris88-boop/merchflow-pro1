"""EXPLICIT TEST HARNESS: deterministic provider; real libvips/disk/SQLite/SSE.

Never imported by the production engine. Does not contact Gemini or use paid
accounts. All output/DB/account state is confined to a temporary test directory.
Run only for Playwright integration validation, then stop the process.
"""
import os
from pathlib import Path
import sys
import tempfile
import time

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

    class TestProvider:
        def generate_image(self, prompt, images):
            assert prompt.startswith("Recreate this exact uploaded graphic artwork in ultra-high resolution")
            assert len(images) == 1
            time.sleep(1)  # leave time to navigate to History before completion
            return images[0][0]

    providers = {i: TestProvider() for i in (1, 2, 3)}
    gemini_pool.client_for = providers.get
    print("TEST HARNESS ONLY: fixture provider; no live Gemini. Temporary outputs:", root, flush=True)
    uvicorn.run(app.app, host="0.0.0.0", port=8123, log_level="warning")
