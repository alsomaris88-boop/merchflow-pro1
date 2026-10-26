# MerchFlow Pro — local desktop engine

FastAPI serves the exported dashboard and its APIs on one local port. No Node.js
process is required on the end user's Windows machine. The development profile
can instead use Next.js, whose `/api/bridge/*` routes stream to the same worker.

## Build and run

On a development machine (Node 20.9+ and Python 3.11+):

```sh
npm ci
npm run build                  # normal Next.js server profile
npm run build:desktop          # static Windows UI bundle in out/
```

`build:desktop` temporarily excludes Next API routes because FastAPI supplies
them in the desktop package. It restores those sources even on a build failure.
Ship the generated `out/` folder with the Python desktop package. Run
`run_windows.bat` from the repository root on Windows; it installs the worker
dependencies and opens `http://127.0.0.1:8000` in an app window.

### Updating an existing Windows install (silent clipboard + live results)

This release changes the Python worker only. If the Windows package already
contains the live-card UI from `963f530`, replace/pull the updated worker source,
stop its old engine/app window, run `run_windows.bat`, and reload; no UI rebuild
is required for this concurrency change. `/api/health` identifies the worker as
**`2.5.3-multitab-dynamic-batch`** and reports the parallel/tab limits.

`out/` is generated and git-ignored. When shipping a fresh complete package or
upgrading an older UI, rebuild and replace it on the development machine with
`npm ci && npm run build:desktop`; pulling UI source alone cannot update an
older packaged bundle. Copy the fresh `out/` to the Windows package, stop its
old engine/app window, run `run_windows.bat`, and reload. The end user still does
not need a running Node process. Keep genuine verified Chrome profiles, SQLite
credentials, and existing output intact; never replace their private state.

### Windows startup upgrades and crash logs

`run_windows.bat` refreshes `desktop/requirements.txt` on **every normal launch**,
including when `desktop/.venv_worker` already exists. Both pip and uvicorn use
that venv's exact `Scripts/python.exe`, not executables resolved from PATH.
`rembg[cpu]` in the requirements installs rembg and its ONNX Runtime dependency.
A pip upgrade failure does not skip the requirements install; a requirements
install failure stops startup and displays its error.

Boot diagnostics, dependency-install output, and combined engine stdout/stderr
are recorded in **`desktop/engine_boot.log`**. The engine runs in a visible
console that stays open after it exits and prints the complete log. On startup
failure, the original launcher console also prints the log and pauses instead
of disappearing. A fresh exit-status marker lets the readiness check stop
promptly after a crash; stale status is cleared before each launch.

The launcher polls `http://127.0.0.1:8000/docs` then `/api/health` for up to
**45 seconds**, allowing libvips/rembg/ONNX imports to finish. Only **HTTP 200
from both** plus this worker's `ok`/`unified` markers permits opening
Chrome/Edge/the browser. A signed-in Gemini
account is not required for engine readiness: the empty Account Manager must
also open normally. Connection errors, HTTP 201/202/204/503, malformed JSON,
startup crashes and deadline expiry do not launch the UI. The exit marker is
checked again after a healthy response to catch a child that just crashed.

If port 8000 is already in use, stop the old engine first and rerun the launcher. You do
not need to delete a working venv just to install newly added dependencies.
Runtime log/status files are ignored by Git.

### Two-process development

```sh
python -m pip install -r desktop/requirements.txt
python -m uvicorn desktop.worker.app:app --host 127.0.0.1 --port 8000
# In another terminal:
npm run dev
```

For a remote/proxied preview, bind the processes to `0.0.0.0`. Browser code always
uses same-origin URLs; `MERCHFLOW_WORKER_URL` is a **server-side** proxy setting.

## Account Manager — verified isolated Chrome profiles over CDP

The Account Manager starts with **zero accounts**; nothing is seeded. Clicking
**+ Add Gemini Account** launches a real, visible Chrome window:

```
chrome --user-data-dir="desktop/profiles/account_<id>" --app="https://gemini.google.com/app"
       --remote-debugging-port=<9500+id>
```

You sign in manually (password, 2FA and passkeys all work — it is a genuine
browser). A CDP sensor polls every 1.5 s; once the tab is on
`gemini.google.com/app`, the prompt box is present, no *Sign in* button is
shown and the session is live, the visible browser closes and the same profile is independently verified headlessly before the account is written to SQLite
(`accounts.engine = 'cdp'`, `accounts.profile_dir`) with a verified timestamp and browser-login provenance.

Generation opens or reuses **the same profile headlessly**
(`--headless=new`, DevTools port `9222 + id − 1`; each profile is its own
Chrome process, so parallel accounts need separate ports). Credentials are
never requested again and no cookie values are exported into application JSON or frontend JavaScript.
`desktop/worker/gemini_cdp.py` drives Gemini's normal chat input:

1. Read each real reference on the server, sniff its actual image MIME without
   converting/recompressing the pixels, Base64 it in memory, reconstruct a
   `File`/`DataTransfer`, focus `rich-textarea [contenteditable="true"]` and dispatch
   `ClipboardEvent('paste')` through `Runtime.evaluate`. No OS clipboard read/write,
   upload menu, Images pill, file-input setter, native file chooser or visible
   generation window is used.
2. **Gate 1** verifies exactly the expected new, loaded reference thumbnails in
   the composer **after each reference**, keeping niche IMAGE 1 before style
   IMAGE 2. Existing draft attachments, duplicates, avatars/history, hidden/broken
   previews and empty cards cannot satisfy it. If paste is unhandled and no
   upload/thumbnail is in progress, the **same staged File** may be dispatched
   once with in-memory `DragEvent('drop')`. An acknowledged/busy/partially accepted
   paste is never resent via drop. This intentionally avoids dispatching both
   deliveries unconditionally and uploading every reference twice. Staged page
   data is released after each attempt.
3. Inject the exact prompt with `Input.insertText`; verify its **entire** normalized
   text, not only its first 48 characters. A native insertion truncation gets one
   full replacement attempt; a remaining mismatch fails before sending. The
   source Fusion/Donepixel literals remain byte-identical; browser paragraph
   whitespace is normalized only for comparison.
4. Submit with the composer-scoped Send/Envoyer control (Enter fallback).
   **Gate 2** requires the complete expected number of real user-bubble reference
   images, excluding avatars, rather than accepting one image for a two-image fusion.
5. Extract the **first generated asset at original resolution** using its full-size
   Download control with browser-level download behavior (no OS Save As dialog),
   or an in-page original-only fetch. `=s…`/`=w…`/`=h…` URLs are requested at `=s0`,
   preserving query parameters; explicit original metadata URLs are supported.
   A failed original never falls back to the known preview URL or a canvas copy.
   A supposed full-size export returning only known preview-sized pixels fails
   visibly. The CDP extractor never resizes the original bytes or invents 8K pixels.
6. Return **exactly one** original; response candidate sets are not saved. Actual
   native width/height are recorded. A local 8K-source regression verifies a
   6554 × 8192 original byte-for-byte; this is not proof Google will generate
   native 8K just because a prompt requests it.

**No false account cooldowns for local glitches.** `GeminiAutomationError`
classifies composer/clipboard/gate/prompt/CDP/timeout/original-extraction errors;
explicit refusals and empty results are also non-quota failures. Both Fusion and
Donepixel release the lease without cooldown, false success-usage increments,
auth invalidation or silently trying another paid account. The remote request may
already have rendered: only explicit Retry may reissue it. Actual verified
sign-out, explicit provider limit/Flash-degradation signals, and existing window
caps still enforce their real account guards. No quota bypass is implemented.

Generation stays headless and will not reuse a visible Chrome process for the
private profile; close that window first. Initial Google sign-in remains the
real, visible, browser-verified setup flow described above. Disconnecting an idle
account closes its headless client and deletes its SQLite row/profile. Failures
stay visible, never synthetic successes. `desktop/profiles/` holds private live
sessions and is git-ignored; no legacy JSON account file is read or written.
Gemini may change its DOM or reject synthetic events: dispatch alone is **not**
accepted as upload success, no fake thumbnail is inserted, and UI upload fallbacks
are forbidden. Such failures leave the account healthy and show their exact error.
The per-gate wait defaults to 45 seconds (`MERCHFLOW_CDP_UPLOAD_TIMEOUT`). English/
French session, Send, Stop and full-size download labels remain supported.
Tests use real Chromium against `tests/support/fake_gemini.py` (an explicit local
DOM fixture, never Google). Live authenticated Gemini and native Windows still
require target-machine smoke tests.

**Account admission and upgrade safeguards**

- Fresh databases and pending/failed/cancelled login flows contain **zero** accounts.
  The exact empty message is:
  `No accounts connected. Click [+ Add Gemini Account] to link your Google account.`
- SQLite is the only account authority. Chrome keeps its credentials in the
  profile's local encrypted Cookies SQLite; the app stores the private profile
  reference and verified browser-flow provenance, not browser-side tokens.
  Existing browser-verified legacy SQLite cookie sessions remain compatible;
  there is no JSON/PostgreSQL/default-profile fallback.
- Metadata insertion, status spoofing and pasted-cookie HTTP intake return 405.
  Public account/health responses exclude credential fields, private profile
  paths and login-flow identifiers. A successfully opened window is still
  **pending**, never a synthetic successful login.
- Cancelling during the headless probe cannot commit an account. A failed
  reconnect preserves the previous record/client. Reconnect is serialized
  against generation; repeated identity uses the existing card's Reconnect
  action rather than creating duplicate accounts. New cancelled/failed profile
  folders are cleaned up; existing reconnect profiles stay intact.
- Old unverified metadata must be linked once again; its Chrome profile files
  are not deleted by the schema migration. Precisely fingerprinted factory demo
  activity is removed without deleting later real jobs/listings/design files.
- Protect local SQLite/profile files with OS permissions; do not publish the
  local worker port. Sign-in does not establish paid Pro entitlement or guarantee
  native 8K provider output. Expired sessions require Reconnect.

## The four production paths

Module 01 preserves the mandatory `FUSION_DIRECTIVE` verbatim and uploads the
niche subject as **IMAGE 1**, then the style reference as **IMAGE 2**. Runtime
operator settings are appended; they never rewrite the directive. Donepixel
uses its own exact single-reference fidelity prompt, not the fusion directive.

### Universal cutout

`desktop/worker/image_lab.py` uses **rembg / ONNX**, with U2Net or
`birefnet-general`. It does not sample/key a border colour and never silently
falls back to a colour heuristic. Matting handles arbitrary-colour and textured
backdrops, preserves existing alpha holes, and decontaminates partial-edge RGB.
A real `cv2.erode` applies a **1–2 pixel alpha choke at final export resolution**,
not multiple erosions on a tiny source image. Micro-dot cleanup is optional and
off by default to protect disconnected fine details.

The first use downloads the chosen model's official weights. BiRefNet requires
more memory than U2Net. Missing dependencies, unavailable weights and empty
foreground masks fail the job visibly. Semantic matting is not infallible:
review fine artwork against light and dark backgrounds and try the other model
when the subject is difficult. There is no claim that every conceivable artwork
will be segmented perfectly.

### Batch Prepare Lab and streaming ZIP

The canonical component is `src/components/modules/prepare-lab.tsx`; the old
component path is a compatibility re-export. The multi-file input and drop zone
keep **all** dropped files, including 50+ images. One complete pipeline finishes
before the next starts. A bad file fails individually without dropping the
remaining batch. Successful outputs are retained when retrying failed files.

Each master is a genuine **4500×5400 PNG with embedded 300 DPI pHYs metadata**.
Optional distress, garment and product raster variants also go through verified
PNG saving and durable History. Maximum compression changes the actual encoder
level; the UI does not invent savings or memory-use percentages.

**Download all (N)** obtains a short-lived download ticket and streams one ZIP64
archive from disk to the browser download manager. The worker copies in 256 KB
chunks, stores already-compressed PNGs without recompression, and disambiguates
duplicate filenames. Neither browser nor Next.js holds an archive-sized Blob.
The PNGs in the ZIP are the exact bytes on disk, including DPI and alpha.

### Strict 1:1 Donepixel Pixel to Pixel (dynamic batches)

The new `src/components/modules/pixel-to-pixel.tsx` accepts **1, 5, 20, 50, 100+
images**, with no fixed batch-count cap. The counter and Generate label reflect
exactly N files. Each thumbnail has Remove; Clear all releases object URLs.
The prominent two-way background control is **Pitch Black `#000000` (default)**
or **Pure White `#FFFFFF`**. These replace the earlier multicolour/custom picker.

The manifest explicitly sends `outputsPerInput: 1`; a request for 3 or a variants
parameter is rejected. Each original has one job and at most one verified master
PNG/History entry—never 3N variants. Each archived result retains the upstream
**Send to Prepare Designs Lab** action, which queues that real disk-backed
History record without regenerating or downloading it. Only the first actual
generated image asset
is downloaded if Gemini returns multiple candidate URLs. Original-resolution
image downloads are requested rather than forcing an 8192px asset down to 2048.
The stable response parser deduplicates renditions and excludes uploaded references.

`gemini_pool.DONEPIXEL_PROMPT` contains the exact requested 8K multimodal prompt,
with only `{Selected_Color_Hex}` substituted. Every call sends one original
uploaded reference to the real image provider. Eligible authenticated Pro
sessions get atomic per-tab leases and share the batch across both accounts and
independent headless tabs within each account.
The dynamic worker count is `min(MERCHFLOW_P2P_PARALLEL, ready Pro accounts ×
SLOTS_PER_ACCOUNT, queued jobs)`: default maximum **10 designs at once**, never
more than the queued batch size. `MERCHFLOW_P2P_PARALLEL` is configurable from
1–32 (default 10); `MERCHFLOW_GEMINI_SLOTS_PER_ACCOUNT` defaults to 10 and is
configurable from 1–32. The pool reserves an individual tab slot **and** its
rolling-window quota before dispatch, so 10 concurrent requests cannot spend the
same final local Pro-window allowance. Unverified, non-Pro, auth-required and
quota/cooldown accounts are excluded. A genuine quota/degradation signal blocks
new leases, cannot be shortened by another tab's generic network error, and
survives sibling success; verified auth-required state cannot be erased by a
sibling quota signal.

Each raw-CDP render owns its own Chrome tab, CDP WebSocket, input files,
full-size image destination and thread-local diagnostics; sharing one profile
never serializes the actual generation. Chrome's normal browser-context download
folder is global, so only the final full-size *download/extraction* stage uses a
per-account lock to prevent two tabs from saving/returning each other's PNG.
Gemini's HTTP client similarly snapshots token/request IDs under a small state
lock while the upload and image RPC network requests remain independent.
The legacy sequential scheduler retains its randomized account jitter; dynamic
parallel P2P bursts bypass that 65–85-second per-account delay, staying bounded
by tab slots, the local window cap, and genuine provider auth/quota signals. Existing fusion calls still use the pool. Flash
model-degradation failures remain real quota signals; no provider quota bypass or
throughput guarantee is claimed. Up to ten concurrent 8K renders can still
increase local memory/CPU/network use and may expose provider-side rate limits;
operators can lower either slot setting if needed. The prompt is
the exact requested faithful-artwork/no-variations text, including cleanup of
fabric wrinkles, with only the selected Black/White hex substituted.

**Uploads are bounded, not one enormous base64 JSON:** create a manifest, upload
original files independently as binary (two at a time), then start the durable
batch. Inputs are spooled in `output/.p2p-inputs/`. Successful-job originals
are removed; **failed originals remain local for explicit Retry**, including
across UI reloads and worker restarts. Keep the browser open during upload. **Once queued, generation
is server-owned** and continues through module navigation, reloads and SSE
disconnections. The last batch can be reopened from Pixel to Pixel. An abandoned
pre-generation upload can be discarded explicitly.

**Generate immediately displays live cards below the form**, even while the
manifest POST is still waiting: pulsing skeleton + `Generating 8K artwork...`.
These are pending UI only, not invented designs or saved records. Once a real
PNG + History transaction commits, the card shows its actual disk thumbnail,
**Download** (the existing master) and **Send to Prepare Lab** (its saved ID).
Nothing auto-downloads. Failures show a prominent red badge with the **full exact
engine reason** and **Retry**, never a successful batch-complete announcement.
Mixed/zero-image failures remain partial/failed, and other successful cards stay
archived and usable.

Retry requeues **only that existing failed job**, with its stored background;
it does not create a replacement job or regenerate already successful items.
Duplicate/in-progress/late successful retry requests are idempotent. Retry obeys
the real-account/libvips/mock-block guards and waits for the active batch to
settle. If an older build removed the original (or the local source was removed),
Retry opens a real file chooser to reupload that original as bounded, validated
binary into the same job. Failed replacement uploads preserve any prior verified
source. Interrupted retries are marked failed on recovery, never silently replayed.
Retained failed references live at `output/.p2p-inputs/<batch>/<job>` until a
successful retry or deliberate local removal; these are private originals, not
extra generated outputs. Removing one does not remove an archived design, but
requires reselecting the original to retry.

As each real provider image arrives, a bounded **native libvips streaming** graph
aspect-fits it onto **4500 × 5400**, using the selected background for padding,
without cropping. Native dimensions/upscaling are disclosed. The prompt requests
8K clarity, but provider-native 8K is not guaranteed and the new final print
canvas is 4500 × 5400—not an advertised native 8K export. No matting, palette
reduction or recolouring substitute is applied to the provider artwork.

The master is a **lossless RGBA PNG, 300 DPI**, written directly to disk as
`output/p2p_<filename>_<UTC timestamp>_<collision suffix>.png`. Compression does
not quantize colours or remove alpha. Per the clarified requirement, **4 MB is
not a rejection threshold**: files below **25,000,000 bytes** are accepted.
A larger lossless file fails visibly rather than degrading pixels silently.
The generator's creative changes/resampling are distinct from lossless encoding;
there is no promise that a generative provider reproduces source pixels exactly.

The PNG and thumbnail must verify before a SQLite transaction inserts `designs`,
marks the persistent batch job successful and writes its durable SSE outbox
entry. The browser **does not initiate a download** on completion. History gets
per-image live updates even when Pixel is unmounted; users can inspect and
multi-select, stream an optional ZIP, or **Send to Listing Hub**. Listing handoff
persists real record IDs across navigation and saving a listing stores its
verified design ID/path in SQLite. Partial failures leave successful files
archived. Worker-restart recovery marks uncertain unfinished work failed without
silently reissuing paid renders; existing archives remain intact.

The older `/api/bridge/pixel-to-pixel` 8192px-longest-edge recreation API remains
available with Transparent, Black, White and Keep Original backgrounds. It is
not used by the new Donepixel print-batch UI; those legacy paths remain covered
by backend regressions.

### Genuine saving and Designs History

The default output directory is **`<repository>/output/`**, independent of the
worker's current working directory. `MERCHFLOW_OUT` overrides it.

`output_store.py` atomically writes each PNG, flushes it, verifies its format,
dimensions and DPI, and then registers it in `output/.history.sqlite3`.
Collision-resistant filenames prevent same-name/same-second overwrites. A
failed write or registry operation produces no success event or fake History
entry. Generation emits file IDs/URLs only **after** this operation completes.
History therefore survives reloads, navigation, browser closure and worker
restarts. Small PNG thumbnails avoid decoding dozens of 8K masters in the grid.

History is now disk-backed rather than IndexedDB-only. Existing browser cache
contents are not deleted, but browser-only legacy records are not represented
as disk-verified files. Reimport an exported legacy PNG to register it in the
new pipeline. Clearing/deleting History entries deliberately leaves user PNGs
on disk untouched.

Generation requires a real authenticated account in **Account Pool**. Provider
errors, missing sessions, empty streams and failed writes stay failed. The
browser does not fabricate a design, account, duration, or successful queue item.
`MERCHFLOW_MOCK_ENGINE=1` is explicitly blocked for production generation.
Provider quality, subscription costs and rate limits are not guaranteed by this
application. Live provider generation is separate from the local raster tests.

## API additions

| Method | Same-origin route | Purpose |
|---|---|---|
| GET / DELETE | `/api/accounts` | Real SQLite sessions; `?id=N` durably disconnects |
| POST | `/api/bridge/login/start` | Open a real isolated Google/Gemini sign-in window |
| GET / POST | `/api/bridge/login/flow/{id}` | Read sign-in state / cancel without creating an account |
| POST | `/api/bridge/process` | Sequential caller → cutout/print/save, returning verified record/report |
| POST | `/api/bridge/generate` | Provider style fusion, SSE events |
| POST | `/api/bridge/pixel-to-pixel` | Legacy 8192px recreation API, SSE events |
| POST | `/api/bridge/p2p/batches` | Create 1:1 manifest: Black/White, `outputsPerInput: 1` |
| POST | `/api/bridge/p2p/batches/{id}/items/{job}` | Stream one original binary upload (40 MB max) |
| POST | `/api/bridge/p2p/batches/{id}/items/{job}/retry` | Retry only an existing failed job; empty POST reuses its source, optional octet-stream reselects a missing original |
| POST | `/api/bridge/p2p/batches/{id}/start` | Idempotently enqueue server-owned parallel generation |
| GET | `/api/bridge/p2p/batches/{id}` | Persistent batch/job states and verified design records |
| POST | `/api/bridge/p2p/batches/{id}/discard` | Discard an unfinished pre-generation upload |
| GET | `/api/bridge/events` | Durable live archive/job SSE, replay via Last-Event-ID |
| GET / POST | `/api/bridge/listings` | SQLite listings linked to verified design IDs/paths |
| GET / POST / DELETE | `/api/bridge/designs` | Durable History, verified browser-derived raster save, remove entries |
| GET | `/api/bridge/designs/{id}` | Actual PNG; `?preview=1` for its thumbnail |
| POST | `/api/bridge/export` | Validate IDs and issue short-lived ZIP ticket |
| GET | `/api/bridge/export?ticket=...` | Stream ZIP64 archive |

Downloads resolve record IDs, never arbitrary client-supplied paths. These APIs
are intended for the local desktop/private development environment, not an
unauthenticated public multi-user service.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `MERCHFLOW_OUT` | `<repository>/output` | PNGs, previews and durable History |
| `MERCHFLOW_DB` | `~/MerchFlow/merchflow.db` | Existing account/job/listing database |
| `MERCHFLOW_WORKER_URL` | `http://127.0.0.1:8000` | Next.js → worker proxy |
| `MERCHFLOW_CDP_UPLOAD_TIMEOUT` | `45` | Seconds per gate to verify each silent paste/drop reference thumbnail |
| `MERCHFLOW_P2P_PARALLEL` | `10` | Per-batch concurrent provider calls, dynamically capped to jobs and Pro accounts × per-account slots (1–32) |
| `MERCHFLOW_GEMINI_SLOTS_PER_ACCOUNT` | `10` | Independent concurrent headless tabs/RPC slots per eligible Gemini account (1–32) |
| `MERCHFLOW_REMBG_MODEL` | `u2net` | Default model for direct pipeline callers |
| `U2NET_HOME` | rembg default | Model weights cache directory |
| `OMP_NUM_THREADS` | runtime default | ONNX CPU thread tuning |
| `MERCHFLOW_MOCK_ENGINE` | off | Must be off for production generation |

Native libvips is mandatory for Donepixel's streamed print export. The
`pyvips[binary]` requirement supplies it on supported platforms; an unavailable
runtime blocks Donepixel visibly. Other image tools retain their identified
Pillow fallback. Neural background removal
still requires rembg, ONNX Runtime and OpenCV. SVG vector export requires vtracer.

## Validation

```sh
python -m pip install -r desktop/requirements-dev.txt
python -m compileall -q desktop/worker
python -m pytest desktop/tests -q
npm run test:unit
npm run lint
npm run typecheck
npm run build
npm run build:desktop
```

Opt-in **real U2Net** smoke (downloads official weights and uses a public-domain
NASA astronaut image on orange, navy and textured backgrounds):

```sh
MERCHFLOW_TEST_REMBG=1 OMP_NUM_THREADS=2 python -m pytest desktop/tests/test_rembg_smoke.py -q
```

Browser tests (with Next + worker running):

```sh
npx playwright install --with-deps chromium
MERCHFLOW_E2E_REAL_WORKER=1 npm run test:e2e
```

The real-worker browser case queues **55 valid files plus one invalid file**,
checks only one processing request is in flight, downloads one ZIP, verifies all
55 PNG dimensions/DPI and unique filenames, and checks History/handoff/reload.
Other browser cases check Pitch Black/Pure White, default Black, dynamic
1/5/20/101 ingestion, the exact empty Account Manager, pending/cancelled/failed
browser sign-in, and genuine authentication failure without fake success.

Donepixel browser integration has an explicit **test-only provider harness**:

```sh
# Build the actual static desktop UI before starting the test harness.
npm run build:desktop
# Terminal 1: fixture provider, actual libvips/SQLite/SSE/files, temporary data
VIPS_CONCURRENCY=2 python tests/support/donepixel_worker.py
# Terminal 2: validate the shipped static UI + FastAPI API routes
MERCHFLOW_E2E_BASE_URL=http://127.0.0.1:8123 MERCHFLOW_E2E_P2P=1 npm run test:e2e

# Alternatively, validate Next proxy wiring with a separate dev server:
# MERCHFLOW_WORKER_URL=http://127.0.0.1:8123 npm run dev -- --hostname 0.0.0.0
# MERCHFLOW_E2E_P2P=1 npm run test:e2e
```

The harness is never imported by production. It uses no paid account/cookies,
and must be stopped after validation. Tests verify live History arrival,
optional ZIP only, navigation/reload recovery, immediate pre-POST pending cards,
full exact-error badges, no false zero-image completion, retry/reselection with
no duplicate saved outputs, byte-identical optional PNG Download, saved-ID
Prepare handoff, and real Listing Hub file association, including regenerating the same retained files on another colour
with fresh job identities. Clipboard regressions forbid menu/Images/file-input/chooser commands, preserve
original JPEG MIME/bytes even under a .png filename, verify sequential IMAGE 1/2,
reject rejected/double/stale pastes, repair truncated prompts, and reject preview
fallbacks when original requests fail. Account-policy tests cover both generation
paths with no false local-error cooldowns while retaining real auth/quota guards.
Python provider fixtures exercise genuine 1/5/20/101 disk archives
and exact-pixel lossless RGBA roundtrip above 4 MB. These are not a claim of
successful live Gemini rendering. Linux validation does not replace an actual
Windows/native-package and authenticated-provider smoke test.
