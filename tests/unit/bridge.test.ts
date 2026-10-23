import assert from "node:assert/strict";
import test from "node:test";
import { readSSE } from "../../src/lib/bridge";
import { downloadDesignsZip, isSavedDesign, responseJson, sendToPrepare, takePrepareHandoff } from "../../src/lib/designs-store";

function stream(parts: Uint8Array[]) {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) { for (const part of parts) controller.enqueue(part); controller.close(); } }));
}

test("SSE handles arbitrary UTF-8 chunk boundaries, CRLF, comments and an unterminated tail", async () => {
  const data = new TextEncoder().encode(': heartbeat\r\n\r\ndata: {"type":"log","msg":"café ⚡"}\r\n\r\ndata: {"type":"done"}');
  const events: Record<string, unknown>[] = [];
  await readSSE(stream(Array.from(data, (byte) => new Uint8Array([byte]))), (event) => { events.push(event); });
  assert.deepEqual(events, [{ type: "log", msg: "café ⚡" }, { type: "done" }]);
});

test("SSE does not swallow a failed file-verification handler", async () => {
  const body = stream([new TextEncoder().encode('data: {"type":"image"}\n\n')]);
  await assert.rejects(readSSE(body, () => { throw new Error("unverified PNG"); }), /unverified PNG/);
});

test("SSE rejects malformed event data rather than faking success", async () => {
  await assert.rejects(readSSE(stream([new TextEncoder().encode("data: broken\n\n")]), () => {}));
});

test("isSavedDesign rejects browser-only images and missing disk confirmation", () => {
  const valid = { id: "one", saved: true, path: "/output/one.png", fileUrl: "/api/bridge/designs/one", w: 4500, h: 5400, bytes: 1000, dpi: 300 };
  assert.ok(isSavedDesign(valid));
  assert.ok(!isSavedDesign({ ...valid, saved: false }));
  assert.ok(!isSavedDesign({ ...valid, path: undefined }));
  assert.ok(!isSavedDesign({ ...valid, bytes: 0 }));
  assert.ok(!isSavedDesign({ ...valid, dpi: 96 }));
  assert.ok(!isSavedDesign({ url: "blob:preview" }));
});

test("HTTP errors surface the worker's actual reason", async () => {
  await assert.rejects(responseJson(Response.json({ ok: false, reason: "Disk full" }, { status: 500 })), /Disk full/);
  await assert.rejects(responseJson(Response.json({ detail: "Connect an account" }, { status: 409 })), /Connect an account/);
});

function replaceGlobal(name: string, value: unknown): () => void {
  const original = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  return () => { if (original) Object.defineProperty(globalThis, name, original); else Reflect.deleteProperty(globalThis, name); };
}

test("History handoff survives navigation before Prepare mounts", () => {
  const entries = new Map<string, string>();
  const restoreStorage = replaceGlobal("sessionStorage", { setItem: (key: string, value: string) => entries.set(key, value), getItem: (key: string) => entries.get(key) ?? null, removeItem: (key: string) => entries.delete(key) });
  const restoreWindow = replaceGlobal("window", new EventTarget());
  try {
    sendToPrepare(["one", "two", "three"]);
    assert.deepEqual(takePrepareHandoff(), ["one", "two", "three"]);
    assert.deepEqual(takePrepareHandoff(), []);
  } finally { restoreStorage(); restoreWindow(); }
});

test("ZIP download hands an HTTP stream to the browser, not an archive-sized Blob", async () => {
  let clicked = false;
  const anchor = { href: "", download: "", click: () => { clicked = true; }, remove: () => {} };
  const requests: { url: string; body: string }[] = [];
  const restoreDocument = replaceGlobal("document", { createElement: () => anchor, body: { appendChild: () => {} } });
  const restoreFetch = replaceGlobal("fetch", async (url: string, options: RequestInit) => {
    requests.push({ url, body: String(options.body) });
    return Response.json({ ok: true, url: "/api/bridge/export?ticket=real-ticket" });
  });
  try {
    await downloadDesignsZip(Array.from({ length: 55 }, (_, i) => `id-${i}`));
    assert.equal(requests.length, 1);
    assert.equal(JSON.parse(requests[0].body).ids.length, 55);
    assert.equal(anchor.href, "/api/bridge/export?ticket=real-ticket");
    assert.ok(clicked);
  } finally { restoreDocument(); restoreFetch(); }
});
