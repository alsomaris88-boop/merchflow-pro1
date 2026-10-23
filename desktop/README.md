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

The launcher checks the actual worker health API before opening the UI. If port
8000 is already in use, stop the old engine first and rerun the launcher. You do
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

### Pixel to Pixel

`src/components/modules/pixel-to-pixel.tsx` calls the connected image provider to
**recreate** each uploaded concept. It is not the old 900px HSL palette-transfer
preview. Background choices are:

- **Transparent:** neural alpha matting and a 1px export-space choke.
- **Solid Black:** matte the foreground, then composite onto exact RGB 0,0,0.
- **Solid White:** matte the foreground, then composite onto exact RGB 255,255,255.
- **Keep Original:** ask the provider to preserve the reference backdrop and
  retain the recreated backdrop without segmentation. Generative results can
  differ from source pixels.

**8K means 8192px on the longest edge**, preserving the rendered aspect ratio,
with 300 DPI metadata. Provider-native dimensions and any Lanczos upscaling are
shown; a smaller model render is never advertised as native 8K generation.

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
| POST | `/api/bridge/process` | Sequential caller → cutout/print/save, returning verified record/report |
| POST | `/api/bridge/generate` | Provider style fusion, SSE events |
| POST | `/api/bridge/pixel-to-pixel` | Provider concept recreation, SSE events |
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
| `MERCHFLOW_REMBG_MODEL` | `u2net` | Default model for direct pipeline callers |
| `U2NET_HOME` | rembg default | Model weights cache directory |
| `OMP_NUM_THREADS` | runtime default | ONNX CPU thread tuning |
| `MERCHFLOW_MOCK_ENGINE` | off | Must be off for production generation |

libvips is optional; the pipeline accurately identifies its Pillow Lanczos
fallback when the native runtime is not installed. Neural background removal
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
Other browser cases stub **authentication failure only**, confirming the four
background values reach the recreation endpoint and failed generation does not
fall back to a fabricated image. Deterministic Python provider fixtures test
SSE/save ordering without using paid services or account cookies; they are not a
claim of live-provider validation. Tests run on Linux; the actual Windows app
window and live authenticated provider must also be smoke-tested on the target
machine.
