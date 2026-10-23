# Pipeline validation — 2026-10-23

## Dynamic Donepixel + automatic History archive

Final checks for this update:

- **60 Python tests passed, 1 intentional opt-in model skip** (209.48s).
  Includes existing image/launcher regressions and 14 new Donepixel cases:
  dynamic **1/5/20/101** batches, actual 4500×5400 RGBA/300-DPI files,
  thumbnails, committed SQLite jobs/designs and ordered events; exclusive
  concurrent Pro leases, retries, failure/rollback, recovery and Listing links.
- Lossless PNG pixel equality above **4 MB** is verified, with no palette
  quantization. The strict acceptance ceiling is **<25,000,000 bytes**.
- **16 TypeScript unit tests passed**: includes dynamic binary uploads, bounded
  upload concurrency, exact colours, raw/SSE proxying and persistent Listing
  handoff and fresh identities when regenerating retained files; existing
  SSE/download protections remain covered.
- **16 Chromium browser tests passed, 1 separately opt-in legacy Prepare test
  skipped** (1.0min), against the built static desktop UI served by FastAPI. Exact swatches/default Black/custom hex and dynamic
  ingestion/removal/Clear for 1/5/20/101 pass. Deep integration verifies real SSE
  archive events, physical print files, optional streaming ZIP, selected real-ID
  Listing save and durable History. Reload testing restores an already queued
  batch, completes it server-side and opens History without any auto-download.
  A repeated-colour run archives two distinct jobs/designs from the same retained
  file selection. The earlier Next-proxy suite also passed (15 tests, 1 skip).
- ESLint, TypeScript, Python compileall and whitespace checks passed.
- Both normal production and static desktop Next builds passed. All API source
  routes were verified restored after static export to `out/`.

The integration provider is an explicit **test-only deterministic harness**,
not live Gemini. libvips, filesystem verification, SQLite, SSE, streaming ZIP
and Listing persistence are real. Validation used native libvips **8.18.7**.
All temporary browser-validation servers were stopped afterward. These
results do not claim a live authenticated Gemini run or a native Windows smoke.

Automatic archive routing is independent of the displayed module: completed
files are saved before success and appear in History immediately. The UI does
not force a module jump or browser download. The older 8192px/four-background
endpoint remains compatible; the current UI outputs a 4500×5400 print canvas
and discloses native provider dimensions and any Lanczos upscaling.

## Previously passed core baseline (retained history)

- Python worker syntax compilation (`compileall`).
- 35 Python regression tests: verified PNG writes and rollback, embedded 300 DPI,
  collision-free naming, alpha preservation/choke/decontamination, native print
  dimensions, all four 8192px recreation background exports, disk History,
  ZIP64 streaming, download safety, provider/auth/write failure behavior, and
  save-before-success SSE ordering.
- Fresh-registry concurrency stress: 320 verified saves with eight threads
  across ten fresh databases, with no database-lock errors or name collisions.
- Opt-in **real U2Net / ONNX** smoke on a public-domain NASA astronaut image
  composited onto orange, navy and textured backgrounds.
- Live **Next proxy → FastAPI → real U2Net → disk/history** request, producing a
  verified 4500×5400 PNG with 300 DPI metadata and transparent outer canvas.
- 7 TypeScript unit tests: chunked UTF-8/CRLF SSE, handler/error propagation,
  saved-file confirmation, navigation-safe History handoff and non-Blob ZIP
  download wiring.
- 6 Chromium browser tests, including an actual worker batch of **55 valid
  images + 1 corrupt file**. Verified:
  - one processing request in flight at a time;
  - corrupt file fails individually, 55 successes retained;
  - one downloadable ZIP containing 55 unique, actual 4500×5400 PNGs;
  - every ZIP entry retains embedded 300 DPI metadata;
  - output files and History agree;
  - History survives reload and hands all 55 items to the newly mounted Lab;
  - provider authentication failures never become simulated output;
  - all four Pixel to Pixel background selections reach the recreation route.
- ESLint and TypeScript checks.
- Normal production Next.js build **without** a configured `DATABASE_URL`.
- Static desktop build to `out/`, with API source restoration.

## Windows launcher follow-up

The full Python suite was re-run after the launcher fix: **46 passed, 1
intentional opt-in model skip**. This includes 11 new launcher regressions for
unconditional venv-bound requirements installation, crash logging/exit status,
visible persistent consoles, embedded PowerShell parsing, healthy readiness,
the original date-binding failure, early crash detection, deadline expiry and
rejection of an unrelated service. Python compilation and `git diff --check`
also passed.

Executable PowerShell checks used **PowerShell 7.6.8 on Linux** with isolated
health-response test doubles; no production listener or engine was launched by
these tests. Native `cmd.exe` and Windows PowerShell 5.1 were not available, so
this is not a Windows startup smoke test. The launcher now waits for the actual
worker `/api/health` fields (`ok`, `connected`, `unified`), which match the API.
That launcher-only follow-up did not change the frontend or image pipeline;
its prior validation results are retained above. The Donepixel update now
re-runs those launcher/image regressions as part of the 60-test passing suite.

## Scope and limits

Validation ran on Linux with Node 20 / Python 3.13. The previous core browser batch test
intentionally disabled cutout to exercise 55 real print/save/ZIP operations;
actual neural cutout is covered separately by the real U2Net smoke and live
proxy request.

Provider-success tests use clearly isolated deterministic fixtures. **No live
authenticated Gemini generation was performed**, no account cookies were used,
and no paid rendering service was called. BiRefNet's selectable model/cache
wiring is implemented, but its large model weights were not exercised here.
The Windows app window, target-machine dependencies and real authenticated
provider still require a target-machine smoke test.

The legacy 8K API exports 8192 pixels on the longest edge. Smaller provider images are
explicitly identified as Lanczos-upscaled, not native 8K generation. Neural
matting results still need artwork-specific visual review; an absolute
all-images/fine-detail/zero-halo guarantee is not claimed.

See `desktop/README.md` for reproducible commands and runtime requirements.
