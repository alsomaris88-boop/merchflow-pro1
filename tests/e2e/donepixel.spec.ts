import { expect, test, type Page, type APIRequestContext } from "@playwright/test";
import { readFile, unlink } from "node:fs/promises";
import path from "node:path";
import JSZip from "jszip";

const fixture = path.join(process.cwd(), "tests/fixtures/concept.png");
test.skip(process.env.MERCHFLOW_E2E_P2P !== "1", "Requires the isolated Donepixel test harness; no live provider is claimed");

test("batch survives navigation; SSE populates History, ZIP stays optional, and selected designs reach Listing Hub", async ({ page, request }, info) => {
  test.setTimeout(90000);
  const prefix = `p2p-browser-${Date.now()}`;
  const buffer = await readFile(fixture);
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.goto("/");
  await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
  await page.locator("#p2p-files").setInputFiles(Array.from({ length: 3 }, (_, i) => ({ name: `${prefix}-${i}.png`, mimeType: "image/png", buffer })));
  await page.evaluate((prefix) => {
    const observed: { id: string; lag: number }[] = [];
    (window as typeof window & { p2pObserved: { id: string; lag: number }[] }).p2pObserved = observed;
    window.addEventListener("merchflow:designs-changed", (event) => {
      const design = (event as CustomEvent).detail;
      if (design?.saved && design.name.startsWith(prefix)) observed.push({ id: design.id, lag: Date.now() - design.createdAt });
    });
  }, prefix);
  const started = page.waitForResponse((response) => response.url().endsWith("/start"));
  await page.getByRole("button", { name: "3 designs in, 3 back on your colour. ▷ Generate", exact: true }).click();
  const start = await started;
  expect(start.status()).toBe(200);
  const batchId = (await start.json()).batch.id;
  await page.getByRole("button", { name: "Designs History", exact: true }).click();
  await page.getByPlaceholder("filter by niche, style, account…").fill(prefix);
  // Wait for actual SSE observations, not rendering to finish in an arbitrary
  // short deadline. A 10-second polling fallback cannot create these events.
  await page.waitForFunction(() => (window as typeof window & { p2pObserved: unknown[] }).p2pObserved.length === 3, null, { timeout: 60000 });
  const observed = await page.evaluate(() => (window as typeof window & { p2pObserved: { id: string; lag: number }[] }).p2pObserved);
  expect(new Set(observed.map((event) => event.id)).size).toBe(3);
  expect(Math.max(...observed.map((event) => event.lag))).toBeLessThan(3500);
  await expect(page.locator(`img[alt^="${prefix}"]`)).toHaveCount(3);
  expect(downloads).toBe(0);
  const batch = (await (await request.get(`/api/bridge/p2p/batches/${batchId}`)).json()).batch;
  expect(batch.completed).toBe(3);
  for (const item of batch.items) {
    expect(item.design.saved).toBe(true);
    const raw = await readFile(item.design.path);
    expect(raw.readUInt32BE(16)).toBe(item.design.nativeW);
    expect(raw.readUInt32BE(20)).toBe(item.design.nativeH);
    expect(item.design.w).toBe(item.design.nativeW);
    expect(item.design.h).toBe(item.design.nativeH);
    expect(item.design.upscaled).toBe(false);
    expect(raw[25]).toBe(6); // actual RGBA, never indexed/palette PNG
  }
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download ZIP", exact: true }).click();
  const download = await downloadEvent;
  const zipPath = info.outputPath("donepixel-optional.zip");
  await download.saveAs(zipPath);
  const zip = await JSZip.loadAsync(await readFile(zipPath));
  expect(Object.keys(zip.files)).toHaveLength(3);
  expect(downloads).toBe(1);
  await page.getByRole("button", { name: "Select all", exact: true }).click();
  await page.getByRole("button", { name: "Send to Listing Hub (3)", exact: true }).click();
  await expect(page.getByText("3 designs from History", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: `List ${prefix}-1.png`, exact: true }).click();
  await page.getByRole("button", { name: "Generate dual listing", exact: true }).click();
  const savedResponse = page.waitForResponse((response) => response.url().endsWith("/api/bridge/listings") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Save to library", exact: true }).click();
  const saved = await savedResponse;
  expect(saved.status()).toBe(200);
  const listing = (await saved.json()).item;
  expect(listing.design_id).toBe(batch.items[1].design.id);
  expect(listing.design_path).toBe(batch.items[1].design.path);
  await page.reload();
  await page.getByRole("button", { name: "Designs History", exact: true }).click();
  await page.getByPlaceholder("filter by niche, style, account…").fill(prefix);
  await expect(page.locator(`img[alt^="${prefix}"]`)).toHaveCount(3);
});

test("queued Pixel batch survives reload and restores its disk-backed History without downloading", async ({ page, request }) => {
  test.setTimeout(90000);
  const name = `p2p-reload-${Date.now()}`;
  let downloads = 0; page.on("download", () => downloads++);
  await page.goto("/");
  await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
  await page.locator("#p2p-files").setInputFiles({ name: `${name}.png`, mimeType: "image/png", buffer: await readFile(fixture) });
  const started = page.waitForResponse((response) => response.url().endsWith("/start"));
  await page.getByRole("button", { name: "1 designs in, 1 back on your colour. ▷ Generate", exact: true }).click();
  const id = (await (await started).json()).batch.id;
  await page.reload();
  await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
  await expect(page.getByTestId("p2p-batch-status")).toContainText(id.slice(0, 8));
  await expect.poll(async () => (await (await request.get(`/api/bridge/p2p/batches/${id}`)).json()).batch.status,
    { timeout: 40000, intervals: [750] }).toBe("completed");
  await page.getByRole("button", { name: "Open Designs History", exact: true }).click();
  await expect(page.locator(`img[alt="${name}.png"]`)).toHaveCount(1);
  expect(downloads).toBe(0);
});

test("retained files can be regenerated on another colour as a fresh archived batch", async ({ page, request }) => {
  test.setTimeout(120000);
  const name = `p2p-repeat-${Date.now()}`;
  let downloads = 0; page.on("download", () => downloads++);
  await page.goto("/");
  await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
  await page.locator("#p2p-files").setInputFiles({ name: `${name}.png`, mimeType: "image/png", buffer: await readFile(fixture) });
  const generate = async () => {
    const button = page.getByRole("button", { name: "1 designs in, 1 back on your colour. ▷ Generate", exact: true });
    await expect(button).toBeEnabled();
    const started = page.waitForResponse((response) => response.url().endsWith("/start"));
    await button.click();
    const response = await started;
    expect(response.status()).toBe(200);
    const id = (await response.json()).batch.id;
    await expect.poll(async () => (await (await request.get(`/api/bridge/p2p/batches/${id}`)).json()).batch.status,
      { timeout: 60000, intervals: [750] }).toBe("completed");
    await expect(button).toBeEnabled();
    return (await (await request.get(`/api/bridge/p2p/batches/${id}`)).json()).batch;
  };
  const first = await generate();
  await page.getByRole("radio", { name: "Pure White", exact: true }).click();
  const second = await generate();
  expect(first.background).toBe("#000000"); expect(second.background).toBe("#FFFFFF");
  expect(first.id).not.toBe(second.id);
  expect(first.items[0].id).not.toBe(second.items[0].id);
  expect(first.items[0].design.id).not.toBe(second.items[0].design.id);
  expect(first.completed).toBe(1); expect(second.completed).toBe(1);
  for (const batch of [first, second]) {
    const design = batch.items[0].design;
    const png = await readFile(design.path);
    expect(png.readUInt32BE(16)).toBe(design.nativeW);
    expect(png.readUInt32BE(20)).toBe(design.nativeH);
    expect(design.w).toBe(design.nativeW);
    expect(design.h).toBe(design.nativeH);
  }
  await page.getByRole("button", { name: "Open Designs History", exact: true }).click();
  await page.getByPlaceholder("filter by niche, style, account…").fill(name);
  await expect(page.locator(`img[alt="${name}.png"]`)).toHaveCount(2);
  expect(downloads).toBe(0);
  // Preserve the newer main branch's one-click handoff, using the saved
  // single-output record rather than a browser-only render or a fresh download.
  await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
  await page.getByRole("button", { name: `Send ${second.items[0].name} to Prepare Designs Lab`, exact: true }).click();
  await expect(page.getByText("Batch queue · 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: `Remove ${name}.png`, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare designs (1)", exact: true })).toBeEnabled();
  expect(downloads).toBe(0);
});


// New live-card tests use the isolated fixture provider, but real HTTP binary
// uploads, libvips PNGs, disk/SQLite/History, SSE and native browser downloads.
const gate1Reason = "GATE 1 FAILED: the reference thumbnail never rendered in the Gemini input box";
async function openPixel(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
}
async function startSelected(page: Page, count: number) {
  const started = page.waitForResponse((response) => response.url().endsWith("/start") && response.request().method() === "POST");
  await page.getByRole("button", { name: `${count} designs in, ${count} back on your colour. ▷ Generate`, exact: true }).click();
  const response = await started;
  expect(response.status()).toBe(200);
  return (await response.json()).batch;
}
async function terminalBatch(request: APIRequestContext, id: string, status: string) {
  await expect.poll(async () => (await (await request.get(`/api/bridge/p2p/batches/${id}`)).json()).batch.status,
    { timeout: 60000, intervals: [350] }).toBe(status);
  return (await (await request.get(`/api/bridge/p2p/batches/${id}`)).json()).batch;
}
async function failureReference(page: Page, zero = false) {
  const data = await page.evaluate((zero) => {
    const canvas = document.createElement("canvas"); canvas.width = 50; canvas.height = 60;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = zero ? "rgb(8,18,28)" : "rgb(7,17,27)"; ctx.fillRect(0, 0, 50, 60);
    ctx.fillStyle = `#${Math.floor(Math.random() * 16777216).toString(16).padStart(6, "0")}`; ctx.fillRect(1, 0, 1, 1);
    return canvas.toDataURL("image/png").split(",")[1];
  }, zero);
  return Buffer.from(data, "base64");
}

test("Generate shows pending cards BEFORE create responds, then real artwork, exact PNG Download and saved-ID Prepare", async ({ page, request }, info) => {
  test.setTimeout(120000);
  await openPixel(page);
  const prefix = `p2p-live-${Date.now()}`;
  const buffer = await readFile(fixture);
  await page.locator("#p2p-files").setInputFiles([0, 1].map((i) => ({ name: `${prefix}-${i}.png`, mimeType: "image/png", buffer })));
  let release!: () => void, arrived!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const requested = new Promise<void>((resolve) => { arrived = resolve; });
  await page.route("**/api/bridge/p2p/batches", async (route) => { arrived(); await held; await route.continue(); });
  const started = page.waitForResponse((response) => response.url().endsWith("/start"));
  try {
    await page.getByRole("button", { name: "2 designs in, 2 back on your colour. ▷ Generate", exact: true }).click();
    await requested;
    await expect(page.getByTestId("p2p-result-card")).toHaveCount(2);
    await expect(page.getByText("Generating native-resolution artwork...", { exact: true })).toHaveCount(2);
    await expect(page.getByTestId("p2p-generating").first().locator(".animate-pulse").first()).toBeVisible();
    await expect(page.getByTestId("p2p-generated-artwork")).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem("merchflow:current-p2p-batch"))).toBeNull();
    await expect(page.getByRole("link", { name: /^Download / })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath("pending-before-network-response.png") });
  } finally { release(); }
  const id = (await (await started).json()).batch.id;
  const done = await terminalBatch(request, id, "completed");
  await expect(page.getByTestId("p2p-generated-artwork")).toHaveCount(2, { timeout: 15000 });
  await expect(page.getByTestId("p2p-batch-status")).toContainText("Batch complete");
  for (const item of done.items) {
    const card = page.locator(`[data-job-id="${item.id}"]`);
    const image = card.getByTestId("p2p-generated-artwork");
    await expect(image).toHaveAttribute("src", item.design.previewUrl);
    await expect.poll(() => image.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  }
  const first = done.items[0], downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: `Download ${first.name}`, exact: true }).click();
  const download = await downloadPromise, savedPath = info.outputPath("real-print-master.png");
  await download.saveAs(savedPath);
  const raw = await readFile(savedPath);
  expect(raw.equals(await readFile(first.design.path))).toBe(true);
  expect(raw.readUInt32BE(16)).toBe(first.design.nativeW);
  expect(raw.readUInt32BE(20)).toBe(first.design.nativeH);
  expect(first.design.w).toBe(first.design.nativeW);
  expect(first.design.h).toBe(first.design.nativeH);
  const history = (await (await request.get("/api/bridge/designs")).json()).items.filter((d: { batchId: string }) => d.batchId === id);
  expect(history).toHaveLength(2);
  expect(new Set(history.map((d: { id: string }) => d.id))).toEqual(new Set(done.items.map((j: { design: { id: string } }) => j.design.id)));
  await page.screenshot({ path: info.outputPath("live-real-success.png") });
  await page.getByRole("button", { name: `Send ${first.name} to Prepare Designs Lab`, exact: true }).click();
  await expect(page.getByText("Batch queue · 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: `Remove ${first.design.name}`, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare designs (1)", exact: true })).toBeEnabled();
});

test("failed card shows exact red GATE 1 error, survives reload, and Retry only replaces that card without duplicate History", async ({ page, request }, info) => {
  test.setTimeout(120000);
  await openPixel(page);
  const prefix = `p2p-retry-${Date.now()}`;
  await page.locator("#p2p-files").setInputFiles([
    { name: `${prefix}-ok.png`, mimeType: "image/png", buffer: await readFile(fixture) },
    { name: `${prefix}-failed.png`, mimeType: "image/png", buffer: await failureReference(page) },
  ]);
  const batch = await startSelected(page, 2), first = await terminalBatch(request, batch.id, "partial");
  const failed = first.items.find((j: { state: string }) => j.state === "failed");
  const success = first.items.find((j: { state: string }) => j.state === "succeeded");
  expect(failed.reason).toBe(gate1Reason); expect(failed.retryable).toBe(true);
  await expect(page.getByTestId("p2p-failure-reason")).toHaveText(gate1Reason);
  await expect(page.locator(`[data-job-id="${failed.id}"]`)).toHaveClass(/border-red-/);
  await expect(page.getByTestId("p2p-failure-reason")).toBeInViewport();
  await expect(page.getByRole("button", { name: `Retry ${failed.name}`, exact: true })).toBeInViewport();
  await expect(page.getByTestId("p2p-batch-status")).not.toContainText("Batch complete");
  await page.screenshot({ path: info.outputPath("live-exact-gate1-failure.png") });
  await page.reload(); await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
  await expect(page.getByTestId("p2p-failure-reason")).toHaveText(gate1Reason);
  let release!: () => void, arrived!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; }), requested = new Promise<void>((resolve) => { arrived = resolve; });
  await page.route(`**/items/${failed.id}/retry`, async (route) => { arrived(); await held; await route.continue(); });
  const retried = page.waitForResponse((response) => response.url().endsWith(`/items/${failed.id}/retry`));
  try {
    await page.getByRole("button", { name: `Retry ${failed.name}`, exact: true }).click();
    await requested;
    await expect(page.getByTestId("p2p-generating")).toHaveCount(1);
    await expect(page.getByTestId("p2p-generated-artwork")).toHaveCount(1);
    await expect(page.getByTestId("p2p-result-card")).toHaveCount(2);
  } finally { release(); }
  const retryResponse = await retried;
  expect(retryResponse.status()).toBe(200); expect((await retryResponse.json()).batch.id).toBe(batch.id);
  const done = await terminalBatch(request, batch.id, "completed");
  await expect(page.getByTestId("p2p-generated-artwork")).toHaveCount(2, { timeout: 15000 });
  await expect(page.getByTestId("p2p-failure-reason")).toHaveCount(0);
  expect(done.items.find((j: { id: string }) => j.id === success.id).design.id).toBe(success.design.id);
  expect(done.items.find((j: { id: string }) => j.id === failed.id).id).toBe(failed.id);
  const duplicate = await request.post(`/api/bridge/p2p/batches/${batch.id}/items/${failed.id}/retry`);
  expect(duplicate.status()).toBe(200);
  const history = (await (await request.get("/api/bridge/designs")).json()).items.filter((d: { batchId: string }) => d.batchId === batch.id);
  expect(history).toHaveLength(2);
  expect(new Set(history.map((d: { id: string }) => d.id)).size).toBe(2);
});

test("zero-image engine error stays a failed red card, never Batch complete or a fake artwork", async ({ page, request }) => {
  test.setTimeout(90000);
  await openPixel(page);
  const name = `p2p-zero-${Date.now()}.png`;
  await page.locator("#p2p-files").setInputFiles({ name, mimeType: "image/png", buffer: await failureReference(page, true) });
  const batch = await startSelected(page, 1), failed = await terminalBatch(request, batch.id, "failed");
  expect(failed.completed).toBe(0); expect(failed.items[0].design).toBeNull();
  await expect(page.getByTestId("p2p-failure-reason")).toHaveText("Provider returned no image");
  await expect(page.getByTestId("p2p-generated-artwork")).toHaveCount(0);
  await expect(page.getByTestId("p2p-batch-status")).not.toContainText("Batch complete");
  await expect(page.getByRole("button", { name: `Retry ${name}`, exact: true })).toBeEnabled();
  const history = (await (await request.get("/api/bridge/designs")).json()).items.filter((d: { batchId: string }) => d.batchId === batch.id);
  expect(history).toHaveLength(0);
});

test("Retry of a legacy/missing original opens a real file chooser and reuploads that original into the same job", async ({ page, request }) => {
  test.setTimeout(120000);
  await openPixel(page);
  const buffer = await failureReference(page), name = `p2p-missing-original-${Date.now()}.png`;
  await page.locator("#p2p-files").setInputFiles({ name, mimeType: "image/png", buffer });
  const batch = await startSelected(page, 1), failed = await terminalBatch(request, batch.id, "failed"), job = failed.items[0];
  // Delete ONLY this explicit fixture's temporary source to reproduce old builds,
  // which deleted failed originals. Never touch source fixtures or real output.
  const outDir = (await (await request.get("/api/bridge")).json()).outDir;
  expect(outDir).toContain("merchflow-p2p-browser-");
  await unlink(path.join(outDir, ".p2p-inputs", batch.id, job.id));
  await page.reload(); await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
  await expect(page.getByText("Original upload unavailable. Retry will ask you to choose the original image again.", { exact: true })).toBeVisible();
  const chooserEvent = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: `Retry ${name}`, exact: true }).click();
  const chooser = await chooserEvent;
  const response = page.waitForResponse((res) => res.url().endsWith(`/items/${job.id}/retry`));
  await chooser.setFiles({ name, mimeType: "image/png", buffer });
  expect((await response).status()).toBe(200);
  const done = await terminalBatch(request, batch.id, "completed");
  expect(done.items[0].id).toBe(job.id);
  await expect(page.getByTestId("p2p-generated-artwork")).toHaveCount(1, { timeout: 15000 });
  const history = (await (await request.get("/api/bridge/designs")).json()).items.filter((d: { batchId: string }) => d.batchId === batch.id);
  expect(history).toHaveLength(1);
});
