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
    from desktop.worker.gemini_client import GeminiAutomationError
    import uvicorn

    pool = gemini_pool.AccountPool()
    # These visibly labeled identities exist only inside this isolated test
    # process and temporary SQLite/output directory. They carry no cookies or
    # real profile and never contact Gemini; the login marker lets the UI
    # exercise authenticated-only controls without production test accounts.
    pool.accounts = [
        gemini_pool.AccountState(
            id=i, email=f"TEST FIXTURE - NO LIVE GEMINI {i}", login_flow_id=f"fixture-only-{i}",
            verified_at=time.time(), engine="cdp", profile_dir=str(root / "fixture-profiles" / str(i)),
            status="ready", is_pro=True,
        )
        for i in (1, 2, 3)
    ]
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
                    raise GeminiAutomationError("GATE 1 FAILED: the reference thumbnail never rendered in the Gemini input box")
            if marker == (8, 18, 28):
                raise GeminiAutomationError("Provider returned no image")
            return raw

    providers = {i: TestProvider() for i in (1, 2, 3)}
    gemini_pool.client_for = providers.get
    print("TEST HARNESS ONLY: fixture provider; no live Gemini. Temporary outputs:", root, flush=True)
    uvicorn.run(app.app, host="0.0.0.0", port=8123, log_level="warning")
