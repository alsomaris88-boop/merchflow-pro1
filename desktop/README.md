# MerchFlow Pro — Local Desktop Engine (Unified Single-Port Build)

**Production architecture**: ONE process, ONE port. Python/FastAPI serves BOTH
the pre-built static dashboard (`out/`) and the complete `/api/*` surface
(SQLite-backed) on **127.0.0.1:8000**. End users never install Node.js, never
run `npm`, never wait on a dev server.

```
chrome/msedge --app=http://127.0.0.1:8000
        │
        ▼
 FastAPI worker (uvicorn :8000)
   ├─ /  → StaticFiles(out/)  → full MerchFlow dashboard
   ├─ /api/health · /api/accounts · /api/trends · /api/trademark
   │   /api/license · /api/listings · /api/activity   (SQLite)
   ├─ /api/bridge/generate|process|elite/*  (aliases of engine routes)
   ├─ /accounts/login/*                     (one-time login window)
   ├─ /generate/stream  ·  /process  ·  /elite/*
   └─ GeminiClient (headless batchexecute) + libvips pipeline
```

## Building the static UI bundle (once, dev machine only)

```powershell
# Windows PowerShell or any shell
$env:DESKTOP_EXPORT="1"; npx next build   # emits out/  (pages only; /api ignored)
```

Copy/commit the `out/` folder with the desktop package. Users then only need
Python 3.11+ — `run_windows.bat` handles venv + deps + launch.

# Legacy: two-process dev mode still works (`next dev` on :3000 proxies `127.0.0.1:8000`).

---

# Headless Donepixel Build — generation architecture

Real production engine behind the web dashboard: **zero browser windows**,
pure async HTTPS against Google's internal `batchexecute` RPC using pooled
session cookies — exactly how commercial tools like Donepixel operate.
Ten accounts cost **<40 MB RAM** instead of 4 GB+ of Chrome windows.

## Architecture

```
Browser (UI :3000)
   │  fetch /api/bridge/*               (Next.js routes)
   ▼
Next.js API ── proxy/SSE ──▶ FastAPI worker  http://127.0.0.1:8000
   /api/bridge/accounts  ↔ /accounts/cookies · paste __Secure-1PSID(/TS)
   /api/bridge/generate  ↔ /generate/stream  · SSE pooled Imagen-3 batch
   /api/bridge/process   ↔ /process          · libvips print pipeline
                                    │
        GeminiClient (gemini_client.py) — per account, silent:
          1. init()      GET gemini.google.com/app → extract "SNlM0e" token
          2. upload      POST content-push.googleapis.com/upload (resumable)
          3. batchexecute POST /_/BardChatUi/data/batchexecute?rpcids=maZAc
          4. download    lh3.googleusercontent.com URL → raw bytes
          5. rotate      best-effort __Secure-1PSIDTS refresh
```

If the worker is offline, every module falls back to the in-browser simulated
pipeline — the UI never blocks.

## Quick start (Windows)

1. `run_windows.bat` — venv + deps, engine (:8000), UI (:3000), app window.
2. **Account Pool → Connect cookies** on each card: Option A gives the
   60-second guided extract (F12 → Application → Cookies), Option B is the
   paste form. The engine verifies `SNlM0e` in ~2s and the card flips to
   **Ready · Authenticated Headless**.
3. **AI Design Generator**: style + niches → *Generate batch*. SSE streams
   `dispatch → progress → image(base64) → done` into the console; PNGs +
   Productor CSV + Excel manifest land in `…\MerchFlow\Output\`.
4. **Prepare Designs Lab** shows *Run on local engine (libvips)*.

## Environment flags

| Var                        | Default                  | Purpose                                   |
|---------------------------|--------------------------|-------------------------------------------|
| `MERCHFLOW_OUT`           | `~/MerchFlow/Output`     | Where PNG/CSV/XLSX are written            |
| `MERCHFLOW_MOCK_ENGINE`   | off                      | `1` = placeholder renders, zero cookies   |
| `MERCHFLOW_JITTER_MIN/MAX`| 65/85                    | Per-account human-cadence seconds         |
| `MERCHFLOW_WORKER_URL`    | `http://127.0.0.1:8000`  | Next.js → worker override                 |

## Files

```
desktop/
├─ worker/app.py            FastAPI endpoints (+SSE, login start/status/cancel)
├─ worker/browser.py        ONE-TIME login window spawner + 1.5s capture sensor
├─ worker/gemini_client.py  SNlM0e extraction, upload, batchexecute RPC, rotate
├─ worker/gemini_pool.py    session pool, register_session, jitter, failover
├─ worker/image_lab.py      keep-holes BFS, defringe, despeckle, halftone, vips
├─ config/gemini_accounts.json   (cookies persisted locally on disk)
└─ requirements.txt
```

Login endpoints (bridged through `/api/bridge/login/*`):

| Method | Worker route | Purpose |
|--------|--------------|---------|
| POST | `/accounts/login/start` | spawn isolated window + start sensor |
| GET | `/accounts/login/status/{id}` | poll state (`pending/authenticated/closed/failed`) |
| POST | `/accounts/login/cancel/{id}` | user dismissed the modal |
