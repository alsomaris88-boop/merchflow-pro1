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

The launcher polls `http://127.0.0.1:8000/api/health` for up to **45 seconds**,
allowing libvips/rembg/ONNX imports to finish. Only **HTTP 200** plus this worker's
`ok`/`unified` markers permits opening Chrome/Edge/the browser. A signed-in Gemini
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

## Account Manager — real browser sessions only

A fresh installation has **zero accounts**, with this exact empty state:

> No accounts connected. Click [+ Add Gemini Account] to link your Google account.

There are no seeded accounts, JSON/default profile lists, PostgreSQL fallbacks,
pretend avatars, fabricated account counters or sample account activity. The old tracked
`desktop/config/gemini_accounts.json` is removed and is never read/imported.

**Add Gemini Account → choose Chrome/Brave → Open Google sign-in** starts a real
isolated browser on Gemini, which redirects to Google's login with the proper
return URL. Passwords/2FA are entered directly into Google. A pending/closed/
cancelled/failed flow is not an account and never inserts a database row.
The sensor must observe the real Gemini app, extract its session, and verify it
headlessly before committing the account. Cancelling during verification blocks
that commit; a failed reconnect leaves an existing valid session intact.

The single credential source is **`~/MerchFlow/merchflow.db`** (`MERCHFLOW_DB`
overrides it). The private `accounts` table stores real session cookies,
browser-login provenance, verification time and actual counters. Credentials do
not go into JSON, frontend storage, account/health responses or logs. Protect the
local database and its backups; it is local storage, not a claim of encrypted
SQLite. Database filenames and legacy credential JSON are ignored by Git.

Upgrade migration removes pre-refactor **unverified metadata-only account rows**;
users of the old JSON store must connect again through the browser. Existing
jobs, listings, designs and output PNGs are not deleted. Repeat sign-in for the
same Google identity updates its existing row. Disconnect durably deletes the
stored session and closes its HTTP client; it is not a temporary UI hide.

Both desktop and Next `/api/accounts` read the worker's SQLite-backed list.
Manual POST/PATCH account creation and pasted-cookie intake return HTTP 405.
Configured scheduling windows are local budgets, not promised provider quotas
or evidence of a paid subscription. Real provider access/rate limits still apply.

## The four production paths

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
PNG/History entry—never 3N variants. Only the first actual generated image asset
is downloaded if Gemini returns multiple candidate URLs. Original-resolution
image downloads are requested rather than forcing an 8192px asset down to 2048.
The stable response parser deduplicates renditions and excludes uploaded references.

`gemini_pool.DONEPIXEL_PROMPT` contains the exact requested 8K multimodal prompt,
with only `{Selected_Color_Hex}` substituted. Every call sends one original
uploaded reference to the real image provider. Available authenticated Pro
sessions get exclusive, least-recently-used leases: one render per account,
across accounts in parallel. The default ceiling is eight concurrent provider
calls, configurable with `MERCHFLOW_P2P_PARALLEL` (1–32). Auth-required,
cooldown, exhausted and unverified/non-Pro sessions are excluded. Flash
model-degradation failures are not accepted as Pro successes. Existing fusion
scheduling keeps its ordered account strategy and shares the exclusive leases.
There is no promised render throughput or provider quota bypass. The prompt is
the exact requested faithful-artwork/no-variations text, including cleanup of
fabric wrinkles, with only the selected Black/White hex substituted.

**Uploads are bounded, not one enormous base64 JSON:** create a manifest, upload
original files independently as binary (two at a time), then start the durable
batch. Inputs are spooled in `output/.p2p-inputs/`; they are removed after their
jobs/batch finish. Keep the browser open during upload. **Once queued, generation
is server-owned** and continues through module navigation, reloads and SSE
disconnections. The last batch can be reopened from Pixel to Pixel. An abandoned
pre-generation upload can be discarded explicitly.

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
| `MERCHFLOW_P2P_PARALLEL` | `8` | Parallel P2P provider calls, capped to eligible account count (1–32) |
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
optional ZIP only, navigation/reload recovery, and real Listing Hub file
association, including regenerating the same retained files on another colour
with fresh job identities. Python provider fixtures exercise genuine 1/5/20/101 disk archives
and exact-pixel lossless RGBA roundtrip above 4 MB. These are not a claim of
successful live Gemini rendering. Linux validation does not replace an actual
Windows/native-package and authenticated-provider smoke test.
