# Pipeline validation — 2026-10-26

## Current integration: native-first archives, Prepare, and CDP

Worker identity: `2.5.8-native-first-halftone`.

- Donepixel stores the provider's original render as a lossless RGBA PNG at its
  decoded native dimensions. Generation does not upscale. History records the
  native width/height and the real saved byte count; original-only extraction
  and the no-false-cooldown behavior remain in place.
- History→Prepare sends `designId`, name, and settings only. The worker resolves
  the SQLite-backed disk record; the browser displays the small preview and
  never fetches/posts the full-resolution PNG. Local file uploads keep their
  binary upload path.
- With AI remains ON by default and uses real U2Net/BiRefNet. Without AI is an
  explicit opt-in using median-border/OpenCV exterior flood fill. Cutout,
  despeckle, and the 1–2px alpha choke run at native/source resolution before
  any print resize. If no foreground remains, the exact client error is:
  `Subject matches the border color. Try With AI mode or lower color tolerance.`
- Despeckle counts connected components in OpenCV. Above 50,000 foreground
  components it skips per-component deletion, applies one 3×3 morphological
  close, preserves the texture, and returns zero removed pixels. This avoids a
  large Python contour-object list on halftone/grain masks.
- Only after native processing and foreground validation may the worker resize
  and pad to the 4500×5400, 300-DPI print canvas, with a 180px top collar margin.
  The matching Elite T-shirt product canvas and mockup collar preset also use
  180px. The final PNG is streamed through libvips to disk with lossless RGBA
  and compression level 6; History stores the output's actual dimensions/bytes.
  Lanczos is resampling, not AI super-resolution.
- The P2P input queue has no fixed item-count cap; tests exercise 101 inputs.
  Rendering concurrency remains distinct from queue size: the worker pool is
  configurable at 1–32 (default 10), per-account slots at 1–32 (default 2), and
  same-account launches are staggered by 2.5 seconds.
- Chrome applies the requested background-timer, occluded-window, renderer, and
  native Win-occlusion flags to both launch modes. Manual sign-in stays visible
  at the normal desktop position; no off-screen `--window-position` flag is
  added. Headless-only resource flags remain separate. Worker cleanup targets
  only the owned Chrome PID tree; no system-wide Chrome kill is used.
- The isolated Playwright provider is test-only, labeled, and confined to a
  temporary output/SQLite directory. It makes no Gemini calls and is not a
  production account or a claim of live provider success.

Validation results:

- **182 Python tests passed, 15 skipped** (272.21s; one Starlette/httpx
  deprecation warning). `compileall` and `git diff --check` passed.
- **27 TypeScript unit tests passed.** ESLint, typecheck, normal production
  build, and static desktop build passed.
- **20 Chromium E2E tests passed** (3.5m), using the isolated local fixture
  provider. Coverage includes pending/success/failure/retry, native-size disk
  archives, History→Prepare without original-image fetch, a 55-file real
  libvips/disk/SQLite/ZIP run, and 101-file dynamic ingestion. No live Gemini,
  paid rendering, or native-Windows production smoke was run.
- `npm ci` reports 7 dependency advisories (4 moderate, 2 high, 1 critical); no
  dependency updates were made as part of this image-pipeline change.

Performance boundary: a synthetic 1500×1500 mask with 562,500 isolated pixels
measured connected-component counting at 0.037s and the morphological close at
0.001s in this sandbox. An alternative `findContours` call measured 0.54s and
process RSS rose from 45.3 to 274.9 MiB for that same synthetic case. These are
fixture measurements only—not a production RAM ceiling, image-processing SLA,
or speedup claim. No `<0.2s`, `<15MB`, or 0%-RAM guarantee is made.

## Retained baseline: 10× dynamic Donepixel workers and concurrent Gemini tabs

Historical notes below describe the pre-native-first baseline. Where an older
setting conflicts with the current section above, the current native-first
settings take precedence.

Worker identity: `2.5.3-multitab-dynamic-batch`, built atop published
`b2e236145d3b7b495e7e2fab1071439087cd8ab8` (`2.5.2` silent-paste/original-only
contracts).

- `MAX_PARALLEL` defaults to 10 and remains bounded 1–32 from
  `MERCHFLOW_P2P_PARALLEL`. Each P2P batch sizes its executor as
  `min(MAX_PARALLEL, max(1, ready_pro_count() * SLOTS_PER_ACCOUNT), max(1, queued_jobs))`.
  Thus a five-item batch starts five, a ten-item batch can start ten, and a
  one-item batch never launches extras. There is no fixed item-count limit.
- `SLOTS_PER_ACCOUNT` defaults to 10 (bounded/configurable 1–32 by
  `MERCHFLOW_GEMINI_SLOTS_PER_ACCOUNT`). The pool tracks lease counts, balances
  tabs across accounts, and wakes queued workers when a tab lease is released.
  Account reconnect/removal is still refused while **any** lease is active.
  Parallel P2P batches do not inherit the legacy 65–85-second per-account jitter
  between worker waves; the separate legacy sequential scheduler keeps its jitter.
- Each lease reserves an individual tab and one local rolling-window quota unit;
  `window_usage + in_flight_leases` cannot overspend the five-hour local cap.
  Genuine Flash/degradation cooldowns cannot be shortened by late generic errors;
  sibling success cannot clear quota/auth state, and a concurrent quota signal
  cannot erase a verified sign-out.
- Raw CDP no longer locks an account client across a whole render. Each job gets
  its own Chrome tab, CDP WebSocket, temporary reference files and thread-local
  diagnostics. Only the final full-size download is serialized per account
  because Chrome's default download directory is browser-context-global; that
  prevents cross-job image swaps while reference upload, navigation and
  multimodal generation across other tabs remain concurrent. The legacy HTTPS
  client also pipelines independent RPCs; token/cookie/build/request-ID updates
  alone use its short state lock.
- Chromium regression tests run three prompt-distinct jobs concurrently in one
  signed-in fixture profile and verify each original image's actual pixels. Real
  end-to-end P2P tests send five and ten designs through **one** Pro-account
  fixture and measure five/ten simultaneous provider calls, respectively. Pool
  tests cover a 3+3 account burst, no parallel-mode start jitter, rolling-window reservations,
  cooldown persistence across success/fault, and auth-state persistence across
  an interleaved quota signal. An HTTP fixture verifies eight concurrent RPCs on
  one client with unique request IDs and matching responses.

Final automated results:

- **171 Python tests passed, 1 opt-in smoke skipped** (523.98s). This includes
  the new CDP/HTTP/pool/P2P concurrency regressions and all prior upload, archive,
  account, failure-policy and Windows-launcher cases.
- **26 TypeScript tests passed.**
- **17 Chromium desktop E2E tests passed, 1 opt-in legacy 55-file Prepare smoke
  skipped** (1.7m). The current static UI/runtime validates live cards, Retry,
  full-resolution Download, saved History/Prepare, ZIP/Listing, reload, colours,
  and dynamic input through 101 references.
- ESLint, TypeScript, Python compileall, whitespace, normal Next production build
  and desktop static export passed; API route sources were restored afterward.
  A fixture health probe reported `2.5.3-multitab-dynamic-batch`, ten batch
  workers, and ten per-account slots; both account-list endpoints returned zero
  real accounts. One non-blocking Starlette/httpx TestClient deprecation warning
  remains from the existing test dependency.
- Google page/provider responses are local fixtures. No live authenticated Gemini,
  paid render or native-Windows concurrency smoke was performed; run a target
  machine smoke before depending on simultaneous real provider traffic. Provider
  concurrency/rate limits are not overridden.

## Published `b2e2361`: silent clipboard, exact prompts and original-only extraction

Based on published `963f530`, with the user's explicit **safe paste → drop
fallback** choice (not unconditional duplicate dispatch). Worker identity:
`2.5.2-clipboard-originals-no-dom-cooldown`.

- Production attachment code no longer has file-input setters, upload-menu/Images
  clicks, OS choosers, or native drag-dispatch fallbacks. Each reference becomes a
  raw-byte `File`/`DataTransfer` and a real Chromium `ClipboardEvent('paste')` in
  the focused composer. Only an unhandled, non-busy paste with no new thumbnail
  may use one in-memory `DragEvent('drop')`. Both-handler/uncanceled/delayed cases
  avoid duplicate delivery. Actual MIME is sniffed without recompression; file
  bytes, niche IMAGE 1/style IMAGE 2 order, and one-result semantics are preserved.
- Gate 1 verifies the exact new reference count after each file; existing draft
  attachments, duplicate/rejected events and fabricated/hidden/history/avatar
  thumbnails never become success. Gate 2 requires the whole reference count.
  Temporary DataTransfer/File state is released. Generation cannot reattach to
  a visible private-profile browser.
- Byte-identical mandated Fusion and Donepixel 8K prompt literals are hash-locked.
  Only the selected Black/White hex changes the Donepixel template. The complete
  normalized prompt must survive native insertion or its one repair attempt;
  a matching prefix is no longer enough to send a truncated directive.
- Only the first actual original is extracted: full-size browser Download or an
  original-only request (`=s0`, query preserved, explicit original metadata).
  Failed original retrieval cannot return the UI preview or canvas fallback.
  Known preview-sized export bytes fail rather than masquerading as full-size.
  A physical 6554 × 8192 fixture original is returned byte-for-byte, without
  client resizing. No provider-native 8K guarantee is inferred from the prompt.
- Local DOM/clipboard/gate/CDP/prompt/timeout/extraction errors, explicit refusals
  and empty results never fault/cooldown a healthy account or silently reroute
  an uncertain paid render. Both generation paths release leases and preserve
  exact errors. Generic service-outage/“try tomorrow” text alone is not quota
  evidence. Real authentication/limit/degradation/window-cap guards remain.
- The approved live cards, exact failed-card errors/Retry, saved-ID Download/
  Prepare/History, verified SQLite accounts, lossless print pipeline and Windows
  launcher are retained; no fake production account or fabricated success is introduced.

Final automated results:

- **161 Python tests passed, 1 opt-in neural-model smoke skipped** (467.05s).
  Includes **42 CDP tests**: real Chromium paste/drop delivery and French controls
  left untouched, uncanceled/delayed handlers, MIME/byte identity, rejected/double/
  stale attachments, strict gates, exact prompt hashes/truncation repair, original
  failure/preview rejection, a physical byte-identical 6554 × 8192 original, and
  visible-browser reuse rejection. **18 account-policy tests** cover both Fusion
  and Donepixel, no local-error cooldown/usage/reroute, empty/refused responses,
  and preserved genuine quota/auth guards. Ambiguous service-outage text is not
  classified as quota. All earlier account/launcher/image/1–100+ regressions pass.
- **26 TypeScript unit tests passed.**
- **17 Chromium desktop E2E tests passed, 1 opt-in legacy 55-file Prepare smoke
  skipped** (1.6m), using the actual static export + FastAPI. Typed automation
  failures remain exact red failed cards; Retry, real Download/Prepare/History,
  one-output archive identity, SSE/ZIP/Listing, reload, Black/White, empty verified
  account lists and 1/5/20/101 ingestion remain verified.
- ESLint, TypeScript, compileall and whitespace checks pass. Both normal Next
  production build and static desktop export pass; API sources are restored.
- Fusion, Donepixel and legacy recreation prompt literals are unchanged from
  `963f530`; the mandatory two current 8K literals are SHA-256 locked in tests.
  Generator/live-card UI, output-store encoding/transactions, neural pipeline,
  persistent batch/Retry, account DB/browser admission and launcher are untouched.
- `/api/accounts` and `/api/bridge/accounts` remain genuinely empty after native
  integration; the test provider is never exposed as a connected account.
  The temporary fixture service is stopped and its temporary data removed.

All Google page and provider responses are explicit local test fixtures; Chromium/CDP, private
Chrome SQLite profiles, native libvips, PNG files/History/SQLite/SSE and browser
handoffs are real. No live Google/Gemini paid render or native Windows smoke was
performed. Synthetic clipboard acceptance depends on the current provider DOM;
if rejected, the engine fails visibly, not via a fake thumbnail or hidden UI fallback.

## Published baseline `963f530`: multilingual upload, Gate 1 and live results

Built on published `6e46af3` without rewriting approved CDP/Fusion/Prepare work.
Worker identity: `2.5.1-multilingual-upload-live-retry`.

- English/French upload-menu vocabulary plus direct SVG-plus, Material-attachment
  and test-ID controls. Composer-scoped direct input errors can fall back to the
  real intercepted file chooser or native drag/drop. French session/Send/Stop/
  full-size download controls are covered too.
- Gate 1 accepts loaded HTTP thumbnails, not only blob/data URLs, and counts
  distinct leaf images instead of their shared container. No document-wide
  fallback, avatars/history, invisible/broken previews, empty/text-only cards,
  pre-existing decorations or same-source decoration re-renders can satisfy it.
  Nested text-only wrappers cannot hide sibling previews in the real input-area.
  Visible upload progress still blocks; hidden spinners do not. Partial uploads
  fail rather than appending duplicate IMAGE 1/2 references.
- Generate renders pending skeleton cards **before the manifest POST returns**,
  with the exact `Generating 8K artwork...` label. Success cards use real saved
  thumbnails and provide Download + Send to Prepare Lab. Failed cards expose
  prominent red, full exact reasons and real Retry; no zero-image/partial failure
  is announced as Batch complete.
- Failed originals are retained for explicit per-existing-job retry across
  reload/recovery. Optional bounded binary reselection repairs old/missing
  sources. Successful jobs are not regenerated; concurrent/late duplicate retry
  requests do not create another PNG/History entry. Failed replacement uploads
  preserve the previous verified original. Recovery never silently replays an
  uncertain paid render. Successful-job source cleanup leaves real archives intact.

Final automated results:

- **124 Python tests passed, 1 opt-in neural-model smoke skipped** (383.62s).
  Includes all **23 CDP tests**, six French-only/icon/test-ID/drag-drop/direct-CDP-
  error upload modes, HTTP thumbnails, negative/busy/nested-wrapper gates,
  unchanged Fusion words/Unicode and IMAGE 1/2 bytes/order; all account/launcher/
  image regressions; and six additional real disk/SQLite retry/error tests.
- **26 TypeScript tests passed**, including exact-error retry propagation,
  bounded unchanged binary reselection, no new retry job identity and defensive
  failed/partial/zero-image/missing-archive completion labels.
- **17 Chromium desktop browser tests passed, 1 opt-in legacy 55-file Prepare
  case skipped** (1.6m), against the actual fresh static export + FastAPI. New
  cases hold the initial POST/retry response to verify immediate pending-only
  cards, real saved thumbnails, exact in-viewport red error + Retry, failed-card
  recovery after reload, per-failed-job/legacy-file-reselection retry without
  duplicate History, byte-identical actual PNG Download and saved-ID Prepare.
  Existing fresh-empty accounts, Black/White, 1/5/20/101 ingestion, repeat-colour,
  SSE/History, ZIP/Listing and navigation/reload tests also pass.
- Both **normal Next production build** and **static desktop export** pass;
  API source restoration, ESLint, TypeScript, compileall and whitespace pass.
- Mandatory Fusion directive and exact Donepixel fidelity prompt audited
  unchanged; the Generator component, neural image pipeline, output-store
  transaction/encoding and Windows launcher remain untouched.
- Real `/api/accounts` and `/api/bridge/accounts` lists remain empty after native
  fixture-backed integration; no test provider is exposed as a connected account.
  The temporary fixture service is stopped and its temporary data removed.

Test provider/DOM responses are **explicit isolated local fixtures**, not live Google. Real
Chromium/CDP, Chrome SQLite profiles, libvips, disk/SQLite History, SSE, browser
Download and Prepare/Listing handoff are exercised. No live authenticated Gemini
or native Windows smoke is claimed. Optional neural-model and legacy 55-file
Prepare smokes remain separate opt-ins. The exact fidelity prompt and the
mandatory Fusion literal/generator are unchanged. Black/White, one saved output
per successful input, 1–100+, lossless RGBA/300 DPI/<25 MB and Windows 45-second
HTTP-200/boot-log/venv guards remain covered.

**Deployment:** rebuild/replace the generated `out/` with
`npm ci && npm run build:desktop` before restarting the Windows package;
`git pull` alone cannot update an older git-ignored UI bundle. Preserve genuine
profiles, SQLite and output. See `desktop/README.md`.

## Published baseline `6e46af3`: account / single-output / startup CDP integration

The user-approved integration preserves upstream commits through `fba9f91`:
raw-CDP Chrome profiles, the byte-identical mandatory Fusion directive and
niche-first/style-second uploads, and the one-click Send to Prepare handoff.
The earlier verified `c8408a4` was not force-pushed over those changes.

Checks recorded for that published baseline:

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
re-runs those launcher/image regressions as part of the published CDP baseline's 108-test passing suite; the current
fix reruns them too (results above).

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
