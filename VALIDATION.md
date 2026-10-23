# Core pipeline validation — 2026-10-23

## Passed

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

## Scope and limits

Validation ran on Linux with Node 20 / Python 3.13. The browser's batch test
intentionally disables cutout to exercise 55 real print/save/ZIP operations;
actual neural cutout is covered separately by the real U2Net smoke and live
proxy request.

Provider-success tests use clearly isolated deterministic fixtures. **No live
authenticated Gemini generation was performed**, no account cookies were used,
and no paid rendering service was called. BiRefNet's selectable model/cache
wiring is implemented, but its large model weights were not exercised here.
The Windows app window, target-machine dependencies and real authenticated
provider still require a target-machine smoke test.

An 8K export is 8192 pixels on the longest edge. Smaller provider images are
explicitly identified as Lanczos-upscaled, not native 8K generation. Neural
matting results still need artwork-specific visual review; an absolute
all-images/fine-detail/zero-halo guarantee is not claimed.

See `desktop/README.md` for reproducible commands and runtime requirements.
