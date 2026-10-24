import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server";
import { isConnectedAccount, NO_ACCOUNTS_MESSAGE } from "../../src/lib/accounts-client";
import { GET, DELETE, POST, PATCH } from "../../src/app/api/accounts/route";
import { POST as pastedCookies } from "../../src/app/api/bridge/accounts/route";

const verified = { id: 7, email: "artist@example.test", authSource: "browser-login", verifiedAt: 1700000000, hasCookies: true };
function replaceFetch(value: typeof fetch) {
  const previous = globalThis.fetch;
  globalThis.fetch = value;
  return () => { globalThis.fetch = previous; };
}

test("empty account message is the exact requested copy", () => {
  assert.equal(NO_ACCOUNTS_MESSAGE, "No accounts connected. Click [+ Add Gemini Account] to link your Google account.");
});

test("account UI admits only browser-verified SQLite session metadata", () => {
  assert.ok(isConnectedAccount(verified));
  assert.ok(isConnectedAccount({ ...verified, engine: "cdp", hasCookies: false, hasSession: true }));
  assert.equal(isConnectedAccount({ ...verified, engine: "cdp", hasCookies: false, hasSession: false }), false);
  for (const value of [null, {}, { id: 1, email: "unverified@example.test", status: "ready" },
    { ...verified, hasCookies: false }, { ...verified, verifiedAt: 0 }, { ...verified, verifiedAt: NaN },
    { ...verified, authSource: "pending" }, { ...verified, id: -1 }, { ...verified, email: "" }]) {
    assert.equal(isConnectedAccount(value), false);
  }
  // A genuinely linked but expired session remains a real account needing reconnect.
  assert.ok(isConnectedAccount({ ...verified, status: "auth-required", authed: false }));
});

test("Next account GET preserves an empty SQLite list without seeds or PostgreSQL fallback", async () => {
  const restore = replaceFetch(async (url) => {
    assert.ok(String(url).endsWith("/api/accounts"));
    return Response.json({ source: "sqlite", items: [] });
  });
  try {
    const response = await GET(new NextRequest("http://app/api/accounts"));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { source: "sqlite", items: [] });
  } finally { restore(); }
});

test("an offline worker produces a real account error, never fabricated users", async () => {
  const restore = replaceFetch(async () => { throw new Error("offline"); });
  try {
    const response = await GET(new NextRequest("http://app/api/accounts"));
    assert.equal(response.status, 503);
    const data = await response.json();
    assert.equal(data.ok, false); assert.ok(!data.items || data.items.length === 0);
  } finally { restore(); }
});

test("manual account creation, status spoofing and pasted cookies are rejected without a worker mutation", async () => {
  const restore = replaceFetch(async () => { assert.fail("Manual intake must not contact the worker"); });
  try {
    for (const handler of [POST, PATCH, pastedCookies]) assert.equal(handler().status, 405);
  } finally { restore(); }
});

test("disconnect is a persistent worker DELETE, not a local-only UI filter", async () => {
  const restore = replaceFetch(async (url, init) => {
    assert.ok(String(url).endsWith("/api/accounts?id=7"));
    assert.equal(init?.method, "DELETE");
    return Response.json({ ok: true });
  });
  try {
    const response = await DELETE(new NextRequest("http://app/api/accounts?id=7", { method: "DELETE" }));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true });
  } finally { restore(); }
});
