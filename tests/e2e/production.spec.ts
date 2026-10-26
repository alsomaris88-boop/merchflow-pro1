import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import JSZip from "jszip";

const fixture = path.join(process.cwd(), "tests/fixtures/concept.png");

async function mockNoProvider(page: Page) {
  // UI wiring tests only: no successful design/provider response is fabricated.
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/bridge") return route.fulfill({ json: { connected: true, mock: false, outDir: "output/", profiles: [] } });
    if (url.pathname === "/api/bridge/events") return route.fulfill({ contentType: "text/event-stream", body: 'retry: 60000\ndata: {"type":"sync"}\n\n' });
    if (["/api/bridge/generate", "/api/bridge/pixel-to-pixel", "/api/bridge/p2p/batches"].includes(url.pathname)) {
      return route.fulfill({ status: 409, json: { detail: "No authenticated image-provider account. Connect one in Account Pool." } });
    }
    return route.fulfill({ json: { items: [], source: "test fixture", jobCount: 0, listingCount: 0 } });
  });
}

test("55 real print PNGs are queued sequentially, streamed in one ZIP, and handed off from durable History", async ({ page, request }, info) => {
  test.skip(process.env.MERCHFLOW_E2E_REAL_WORKER !== "1", "Requires a running real FastAPI worker and same-origin Next proxy");
  test.setTimeout(360000);
  const prefix = `batch-${Date.now()}`;
  const buffer = await readFile(fixture);
  let active = 0, peak = 0, calls = 0;
  page.on("request", (req) => { if (new URL(req.url()).pathname === "/api/bridge/process") { active++; peak = Math.max(peak, active); calls++; } });
  page.on("requestfinished", (req) => { if (new URL(req.url()).pathname === "/api/bridge/process") active--; });
  page.on("requestfailed", (req) => { if (new URL(req.url()).pathname === "/api/bridge/process") active--; });
  await page.goto("/");
  await page.getByRole("button", { name: "Prepare Designs Lab", exact: true }).click();
  // This exercises actual resize/save/ZIP endpoints without needing model
  // inference 55 times. Real U2Net matting has a separate opt-in smoke test.
  await page.getByRole("button", { name: "Remove background", exact: true }).click();
  await page.locator("#lab-files").setInputFiles([
    ...Array.from({ length: 55 }, () => ({ name: `${prefix}.png`, mimeType: "image/png", buffer })),
    { name: "broken.png", mimeType: "image/png", buffer: Buffer.from("invalid-image-fixture") },
  ]);
  await expect(page.getByText("Batch queue · 56", { exact: true })).toBeVisible();
  const run = page.getByRole("button", { name: "Prepare designs (56)", exact: true });
  await expect(run).toBeEnabled();
  await run.click();
  const all = page.getByRole("button", { name: "Download all (55)", exact: true });
  await expect(all).toBeEnabled({ timeout: 300000 });
  await expect(page.getByText("1 failed", { exact: true })).toBeVisible();
  expect(calls).toBe(56);
  expect(peak).toBe(1);
  const downloadEvent = page.waitForEvent("download");
  await all.click();
  const download = await downloadEvent;
  const zipPath = info.outputPath("real-55-print-files.zip");
  await download.saveAs(zipPath);
  const zip = await JSZip.loadAsync(await readFile(zipPath));
  const names = Object.keys(zip.files);
  expect(names).toHaveLength(55);
  expect(new Set(names).size).toBe(55);
  for (const name of names) {
    expect(name).toMatch(/4500x5400_300dpi(?:_\d+)?\.png$/);
    const png = await zip.file(name)!.async("nodebuffer");
    expect(png.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    expect(png.readUInt32BE(16)).toBe(4500);
    expect(png.readUInt32BE(20)).toBe(5400);
    const physical = png.indexOf(Buffer.from("pHYs"));
    expect(physical).toBeGreaterThan(0);
    expect(png.readUInt32BE(physical + 4)).toBe(11811); // 300 DPI in pixels/metre
    expect(png.readUInt32BE(physical + 8)).toBe(11811);
  }
  const response = await request.get("/api/bridge/designs");
  const history = (await response.json()).items.filter((d: { name: string }) => d.name === prefix);
  expect(history).toHaveLength(55);
  expect((await readFile(history[0].path)).readUInt32BE(16)).toBe(4500);
  await page.getByRole("button", { name: "Designs History", exact: true }).click();
  await page.getByPlaceholder("filter by niche, style, account…").fill(prefix);
  await expect(page.getByRole("button", { name: "Send to Prepare (all)", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Send to Prepare (all)", exact: true }).click();
  await expect(page.getByText("Batch queue · 55", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Designs History", exact: true }).click();
  await expect(page.getByText(`${prefix}`, { exact: true }).first()).toBeVisible();
});

test("History designs reach Prepare by ID without a full-resolution browser fetch or Base64 payload", async ({ page, request }) => {
  test.skip(process.env.MERCHFLOW_E2E_REAL_WORKER !== "1", "Requires a running real FastAPI worker");
  test.setTimeout(90000);
  const name = `history-direct-${Date.now()}`;
  const buffer = await readFile(fixture);
  const savedResponse = await request.post("/api/bridge/designs", {
    data: { image: `data:image/png;base64,${buffer.toString("base64")}`, name },
  });
  expect(savedResponse.status()).toBe(200);
  const original = (await savedResponse.json()).design as { id: string; fileUrl: string };
  const originalPath = new URL(original.fileUrl, "http://fixture.invalid").pathname;
  let fetchedOriginal = false;
  let processPayload: Record<string, unknown> | undefined;
  page.on("request", (req) => {
    const url = new URL(req.url());
    if (url.pathname === originalPath && !url.searchParams.has("preview")) fetchedOriginal = true;
    if (url.pathname === "/api/bridge/process" && req.method() === "POST") processPayload = req.postDataJSON() as Record<string, unknown>;
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Designs History", exact: true }).click();
  await page.getByPlaceholder("filter by niche, style, account…").fill(name);
  await expect(page.getByRole("button", { name: "Send to Prepare (all)", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Send to Prepare (all)", exact: true }).click();
  await expect(page.getByText("Batch queue · 1", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Remove background", exact: true }).click();
  await page.getByRole("button", { name: "Prepare designs (1)", exact: true }).click();
  await expect(page.getByRole("button", { name: "Download all (1)", exact: true })).toBeEnabled({ timeout: 60000 });
  expect(processPayload).toMatchObject({ designId: original.id, name, settings: expect.any(Object) });
  expect(processPayload).not.toHaveProperty("image");
  expect(JSON.stringify(processPayload).length).toBeLessThan(3000);
  expect(fetchedOriginal).toBe(false);
});

test("Prepare Lab exposes the three safe defaults, mode/precision choices, and opt-in scaling", async ({ page }) => {
  await mockNoProvider(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Prepare Designs Lab", exact: true }).click();

  const remove = page.getByRole("button", { name: "Remove background", exact: true });
  const upscale = page.getByRole("button", { name: "Upscale", exact: true });
  const resize = page.getByRole("button", { name: "Resize for printing", exact: true });
  await expect(remove).toHaveAttribute("aria-pressed", "true");
  await expect(upscale).toHaveAttribute("aria-pressed", "false");
  await expect(resize).toHaveAttribute("aria-pressed", "true");
  const neural = page.getByRole("combobox", { name: "Neural matting model" });
  const choke = page.getByRole("combobox", { name: "Alpha choke" });
  await expect(neural).toHaveValue("u2net");
  await expect(choke).toHaveValue("1");
  await neural.selectOption("birefnet-general");
  await choke.selectOption("2");

  await page.getByRole("button", { name: "Without AI · Fast ⚡", exact: true }).click();
  await expect(page.getByRole("button", { name: "Keep holes", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Everywhere", exact: true }).click();
  await expect(page.getByRole("button", { name: "Everywhere", exact: true })).toHaveAttribute("aria-pressed", "true");

  await upscale.click();
  const fourX = page.getByRole("button", { name: "4×", exact: true });
  await expect(fourX).toBeVisible();
  await fourX.click();
  await expect(fourX).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/final 18000 × 21600 px/)).toBeVisible();
});

test("provider authentication errors stay failed; there is no simulated generator output", async ({ page }) => {
  await mockNoProvider(page);
  await page.goto("/");
  await page.getByRole("button", { name: "AI Design Generator", exact: true }).click();
  await page.locator("#dz-style").setInputFiles(fixture);
  await page.locator("#dz-niche").setInputFiles(fixture);
  const run = page.getByRole("button", { name: "Generate batch (1)", exact: true });
  await expect(run).toBeEnabled(); await run.click();
  await expect(page.getByRole("alert").filter({ hasText: "No authenticated image-provider account" })).toContainText("No authenticated image-provider account");
  await expect(page.getByText("Saved designs · 1", { exact: true })).toHaveCount(0);
  await expect(page.getByText("failed", { exact: true })).toBeVisible();
});

for (const [label, hex] of [["Pitch Black", "#000000"], ["Pure White", "#FFFFFF"]]) {
  test(`Pixel to Pixel sends the exact ${label} colour and never fakes an authenticated render`, async ({ page }) => {
    await mockNoProvider(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
    await expect(page.getByRole("radio", { name: "Pitch Black", exact: true })).toHaveAttribute("aria-checked", "true");
    await page.locator("#p2p-files").setInputFiles(fixture);
    const option = page.getByRole("radio", { name: label, exact: true });
    await option.click(); await expect(option).toHaveAttribute("aria-checked", "true");
    const payload = page.waitForRequest((req) => new URL(req.url()).pathname === "/api/bridge/p2p/batches");
    const run = page.getByRole("button", { name: "1 designs in, 1 back on your colour. ▷ Generate", exact: true });
    await expect(run).toBeEnabled(); await run.click();
    expect((await payload).postDataJSON().background).toBe(hex);
    await expect(page.getByRole("alert").filter({ hasText: "No authenticated image-provider account" })).toContainText("No authenticated image-provider account");
    await expect(page.getByText("Saved to disk + Designs History", { exact: true })).toHaveCount(0);
  });
}

for (const n of [1, 5, 20, 101]) {
  test(`dynamic ingestion retains all ${n} images, updates the action, and supports remove/clear`, async ({ page }) => {
    await mockNoProvider(page); await page.goto("/");
    await page.getByRole("button", { name: "Pixel to Pixel", exact: true }).click();
    const buffer = await readFile(fixture);
    await page.locator("#p2p-files").setInputFiles(Array.from({ length: n }, (_, i) => ({ name: `input-${i}.png`, mimeType: "image/png", buffer })));
    await expect(page.getByTestId("p2p-upload-count")).toHaveText(`${n} designs uploaded`);
    await expect(page.getByRole("button", { name: `${n} designs in, ${n} back on your colour. ▷ Generate`, exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Remove input-0.png", exact: true }).click();
    await expect(page.getByTestId("p2p-upload-count")).toHaveText(`${n - 1} designs uploaded`);
    if (n > 1) await page.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(page.getByTestId("p2p-upload-count")).toHaveText("0 designs uploaded");
  });
}
