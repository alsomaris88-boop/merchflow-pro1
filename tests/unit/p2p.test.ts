import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server";
import { COLOUR_SWATCHES, generationLabel, normaliseHex, submitPixelBatch, type PixelBatch } from "../../src/lib/p2p-client";
import { clearListingHandoff, listingHandoff, sendToListing } from "../../src/lib/designs-store";
import { proxyWorker } from "../../src/lib/worker-proxy";

function replaceGlobal(name: string, value: unknown): () => void {
  const old = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  return () => { if (old) Object.defineProperty(globalThis, name, old); else Reflect.deleteProperty(globalThis, name); };
}
function storage() {
  const data = new Map<string, string>();
  return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => data.set(key, value), removeItem: (key: string) => data.delete(key) };
}

test("Donepixel swatches use exact requested hexes and dynamic labels do not cap N", () => {
  assert.deepEqual(COLOUR_SWATCHES.map((s) => s.hex), ["#000000", "#FFFFFF"]);
  for (const n of [1, 5, 20, 50, 101, 217]) assert.equal(generationLabel(n), `${n} designs in, ${n} back on your colour. ▷ Generate`);
  assert.equal(normaliseHex("#aBcDeF"), "#ABCDEF");
  assert.throws(() => normaliseHex("#ABC"));
});

for (const n of [1, 5, 20, 101]) {
  test(`${n} inputs upload independently as unchanged binary with bounded concurrency`, async () => {
    const restoreStorage = replaceGlobal("localStorage", storage());
    let active = 0, peak = 0, uploads = 0, starts = 0;
    const positions = new Map<string, number>();
    const inputs = Array.from({ length: n }, (_, i) => ({ id: `job-${i}`, file: new File([new Uint8Array([0, 255, 128, i % 256])], `${i}.png`, { type: "image/png" }), url: `blob:${i}` }));
    const batch: PixelBatch = { id: "batch-id", status: "uploading", total: n, completed: 0, failed: 0, background: "#000000", items: [] };
    const restoreFetch = replaceGlobal("fetch", async (url: string, init: RequestInit) => {
      if (url.endsWith("/start")) { starts++; assert.equal(uploads, n); return Response.json({ ok: true, batch: { ...batch, status: "running" } }); }
      if (url.includes("/items/")) {
        assert.ok(init.body instanceof File);
        active++; peak = Math.max(peak, active);
        await new Promise((resolve) => setTimeout(resolve, 1));
        const index = positions.get(decodeURIComponent(url.split("/items/")[1]));
        assert.ok(index !== undefined, "binary upload must match its generated manifest ID");
        assert.deepEqual(new Uint8Array(await (init.body as File).arrayBuffer()), new Uint8Array([0, 255, 128, index % 256]));
        active--; uploads++;
        return Response.json({ ok: true });
      }
      const manifest = JSON.parse(String(init.body));
      assert.equal(manifest.items.length, n);
      assert.equal(manifest.background, "#000000");
      assert.equal(manifest.outputsPerInput, 1);
      manifest.items.forEach((item: { id: string; name: string }, index: number) => {
        assert.notEqual(item.id, inputs[index].id);
        assert.equal(item.name, inputs[index].file.name);
        positions.set(item.id, index);
      });
      return Response.json({ ok: true, batch });
    });
    try {
      const result = await submitPixelBatch(inputs, "#000000", () => {}, () => {});
      assert.equal(result.total, n); assert.equal(uploads, n); assert.equal(starts, 1);
      assert.ok(peak <= 2); assert.ok(peak >= Math.min(2, n));
    } finally { restoreFetch(); restoreStorage(); }
  });
}

test("Listing Hub handoff preserves all selected disk record IDs across navigation", () => {
  const restoreWindow = replaceGlobal("window", new EventTarget());
  const restoreStorage = replaceGlobal("sessionStorage", storage());
  try {
    sendToListing(["a", "b", "c"]);
    assert.deepEqual(listingHandoff(), ["a", "b", "c"]);
    assert.deepEqual(listingHandoff(), ["a", "b", "c"]);
    clearListingHandoff(); assert.deepEqual(listingHandoff(), []);
  } finally { restoreWindow(); restoreStorage(); }
});

test("Next proxy streams opaque upload bytes without corrupting binary data", async () => {
  const bytes = new Uint8Array([0, 255, 128, 65, 0, 254]);
  const restoreFetch = replaceGlobal("fetch", async (_url: string, options: RequestInit) => {
    assert.equal(new Headers(options.headers).get("content-type"), "application/octet-stream");
    const request = new Request("http://test/upload", { method: "POST", body: options.body, duplex: "half" } as RequestInit);
    assert.deepEqual(new Uint8Array(await request.arrayBuffer()), bytes);
    return Response.json({ ok: true });
  });
  try {
    const req = new NextRequest("http://test/api/bridge/p2p/batches/a/items/b", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: bytes });
    assert.equal((await proxyWorker(req, "/api/bridge/p2p/batches/a/items/b")).status, 200);
  } finally { restoreFetch(); }
});

test("SSE proxy forwards Last-Event-ID for durable reconnect/replay", async () => {
  const restoreFetch = replaceGlobal("fetch", async (_url: string, options: RequestInit) => {
    assert.equal(new Headers(options.headers).get("last-event-id"), "42");
    return new Response('data: {"type":"sync"}\n\n', { headers: { "Content-Type": "text/event-stream" } });
  });
  try {
    const req = new NextRequest("http://test/api/bridge/events", { headers: { "Last-Event-ID": "42" } });
    const response = await proxyWorker(req, "/api/bridge/events");
    assert.equal(response.headers.get("X-Accel-Buffering"), "no");
    assert.equal(response.headers.get("content-type"), "text/event-stream");
  } finally { restoreFetch(); }
});

test("regenerating retained files on another colour creates fresh job identities", async () => {
  const restoreStorage = replaceGlobal("localStorage", storage());
  const seen = new Set<string>();
  const colours: string[] = [];
  let uploads = 0;
  const restoreFetch = replaceGlobal("fetch", async (url: string, init: RequestInit) => {
    if (url.includes("/items/")) { uploads++; assert.ok(seen.has(decodeURIComponent(url.split("/items/")[1]))); return Response.json({ ok: true }); }
    if (url.endsWith("/start")) return Response.json({ batch: { id: `run-${colours.length}`, status: "running" } });
    const manifest = JSON.parse(String(init.body));
    colours.push(manifest.background);
    for (const item of manifest.items) { assert.ok(!seen.has(item.id)); seen.add(item.id); }
    return Response.json({ batch: { id: `run-${colours.length}`, status: "uploading" } });
  });
  const inputs = [{ id: "retained-selection", file: new File(["source"], "same.png"), url: "blob:same" }];
  try {
    await submitPixelBatch(inputs, "#000000", () => {}, () => {});
    await submitPixelBatch(inputs, "#FFFFFF", () => {}, () => {});
    assert.deepEqual(colours, ["#000000", "#FFFFFF"]);
    assert.equal(uploads, 2); assert.equal(seen.size, 2);
    assert.equal(inputs[0].id, "retained-selection");
  } finally { restoreFetch(); restoreStorage(); }
});
