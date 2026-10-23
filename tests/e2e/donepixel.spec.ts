import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
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
    expect(raw.readUInt32BE(16)).toBe(4500); expect(raw.readUInt32BE(20)).toBe(5400);
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
  await page.getByRole("button", { name: `List ${prefix}-1`, exact: true }).click();
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
  await expect(page.locator(`img[alt="${name}"]`)).toHaveCount(1);
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
  await page.getByRole("radio", { name: "Navy", exact: true }).click();
  const second = await generate();
  expect(first.background).toBe("#000000"); expect(second.background).toBe("#0A192F");
  expect(first.id).not.toBe(second.id);
  expect(first.items[0].id).not.toBe(second.items[0].id);
  expect(first.items[0].design.id).not.toBe(second.items[0].design.id);
  expect(first.completed).toBe(1); expect(second.completed).toBe(1);
  for (const batch of [first, second]) expect((await readFile(batch.items[0].design.path)).readUInt32BE(16)).toBe(4500);
  await page.getByRole("button", { name: "Open Designs History", exact: true }).click();
  await page.getByPlaceholder("filter by niche, style, account…").fill(name);
  await expect(page.locator(`img[alt="${name}"]`)).toHaveCount(2);
  expect(downloads).toBe(0);
});
