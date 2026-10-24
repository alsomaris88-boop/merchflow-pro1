# Pipeline validation — 2026-10-24

## Integrated account / single-output / startup refactor on current CDP main

The user-approved integration preserves upstream commits through `fba9f91`:
raw-CDP Chrome profiles, the byte-identical mandatory Fusion directive and
niche-first/style-second uploads, and the one-click Send to Prepare handoff.
The earlier verified `c8408a4` was not force-pushed over those changes.

Current final checks:

- **108 Python tests passed, 1 opt-in real-model smoke skipped** (299.69s).
  Includes 14 account/legacy-provider tests, 13 additional CDP account safeguards,
  all 13 upstream CDP tests (including real Chromium driving a local Gemini DOM
  fixture), the mandatory Fusion/upload-order regression, and 18 launcher cases.
  Existing cutout, Prepare, disk/ZIP, History and
  1/5/20/101 batch regressions continue to pass.
- **22 TypeScript unit tests passed.** New coverage verifies the exact empty
  message, rejection of unverified metadata, empty/offline account proxy behavior,
  blocked manual account/cookie intake and durable disconnect. Upload manifests
  explicitly request one output per input.
- **13 Chromium browser tests passed, 1 opt-in 55-file Prepare test skipped**
  (59.7s), against the actual built static desktop UI and FastAPI. Empty and
  unverified account lists, pending/cancelled sign-in, browser-launch failures,
  Black/White/default Black, 1/5/20/101 ingestion, live History/SSE, optional ZIP,
  Listing linkage, reload recovery, repeat-colour generation and the real saved-ID
  Send to Prepare handoff all pass.
- ESLint, TypeScript, Python compilation and whitespace checks passed.
- Normal Next production build and static desktop build passed; API source
  restoration was verified. The static UI is generated into `out/`.
- Both real account HTTP lists remain empty after fixture-backed native image
  integration; no test-provider profiles are exposed as connected accounts.
  The isolated validation service was stopped afterward.

Account upgrades remove unverified metadata and precisely fingerprinted factory
demo activity without deleting later genuine jobs/listings/design files. Only
an observed real Google/Gemini browser flow plus successful headless verification
can commit a session to SQLite. CDP credentials remain in Chrome's encrypted
local Cookies SQLite, while the app stores private profile/provenance metadata.
No JSON account list is used. Failed/cancelled verification inserts no account;
failed reconnects preserve the existing record/client. Duplicate identity
requires the existing card's Reconnect action instead of creating another row.
Busy accounts cannot reconnect/unlink, and a login reservation cannot be leased
for generation. Failed new profiles are cleaned up; unlink deletes the stored
profile and stays removed after restart. Deletion failures report failure and
keep the account. Cookies, private profile paths and login-flow identifiers are
absent from public account/health responses.

The exact newly requested prompt is used with Pitch Black/Pure White. The
manifest accepts only `outputsPerInput: 1`; requests for 3/variants fail validation.
One job commits at most one verified PNG/History row. Real Chromium against the
local DOM fixture verifies first-image-only extraction from three candidates,
full-size download, attachment-menu fallback, exact prompt/references, refusal,
quota and signed-out failures. Legacy RPC tests also cover stable URL ordering
and original-size requests without calling the paid provider. Existing
4500×5400/300-DPI lossless RGBA, <25 MB saving remains intact.

The launcher polls `/docs` then `/api/health` for up to 45 seconds and requires
HTTP 200 from both plus worker identity before opening the UI, including with
zero linked accounts. Tests cover cold connection failure → HTTP 503 → HTTP 200, non-200
responses (including /docs 200 with /health 503), deadline/crash detection and
a crash immediately after a healthy response. Complete `desktop/engine_boot.log`
display/visible console and venv-bound requirements refresh remain covered.

**Scope:** Google pages/provider responses are isolated local test doubles; CDP
transport, Chromium, persistent profiles, SQLite, libvips files, ZIP, SSE and the
desktop browser UI are real. No live Google login or Gemini generation was performed. Executable launcher checks use official
PowerShell 7.6.8 on Linux; native Windows cmd/PowerShell 5.1 startup still requires
a target-machine smoke test. The single Python skip and legacy browser skip are
explicit opt-ins, not hidden failures. Chromium's missing system libraries were
installed before the final passing browser run.

## Previous Donepixel + automatic History archive baseline

Checks recorded for that earlier update:

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
  skipped** (1.0min), against the built static desktop UI served by FastAPI. The then-current
  seven presets/custom hex and dynamic ingestion/removal/Clear for 1/5/20/101 pass. Deep integration verifies real SSE
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
this is not a Windows startup smoke test. The launcher now polls
`http://127.0.0.1:8000/docs` followed by `/api/health` for HTTP 200 (the current
integration restores the required 45-second deadline and worker-identity gate,
with early abort on the engine-exit marker). The UI is not launched before those
checks pass; native Windows startup still needs its target-machine smoke test.
A crashed engine prints the exact contents of `desktop\engine_boot.log`.
That launcher-only follow-up did not change the frontend or image pipeline;
its prior validation results are retained above. The Donepixel update now
re-runs those launcher/image regressions as part of the current 108-test passing suite.

## Scope and limits

Validation ran on Linux with Node 20 / Python 3.13. The previous core browser batch test
intentionally disabled cutout to exercise 55 real print/save/ZIP operations;
actual neural cutout is covered separately by the real U2Net smoke and live
proxy request.

Provider-success tests use clearly isolated deterministic fixtures. **No live
authenticated Gemini generation was performed**, no real Google account cookies were used,
and no paid rendering service was called. BiRefNet's selectable model/cache
wiring is implemented, but its large model weights were not exercised here.
The Windows app window, target-machine dependencies and real authenticated
provider still require a target-machine smoke test.

The legacy 8K API exports 8192 pixels on the longest edge. Smaller provider images are
explicitly identified as Lanczos-upscaled, not native 8K generation. Neural
matting results still need artwork-specific visual review; an absolute
all-images/fine-detail/zero-halo guarantee is not claimed.

See `desktop/README.md` for reproducible commands and runtime requirements.
