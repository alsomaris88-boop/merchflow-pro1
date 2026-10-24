import { expect, test, type Page } from "@playwright/test";

const empty = "No accounts connected. Click [+ Add Gemini Account] to link your Google account.";

/** Failure/pending responses only: these tests never fake a connected account. */
async function isolatedAccountUI(page: Page, rows: unknown[] = []) {
  await page.route("**/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/bridge") return route.fulfill({ json: { connected: true, profiles: [] } });
    if (pathname === "/api/accounts") return route.fulfill({ json: { source: "sqlite", items: rows } });
    if (pathname === "/api/bridge/events") return route.fulfill({ contentType: "text/event-stream", body: 'data: {"type":"sync"}\n\n' });
    if (pathname === "/api/bridge/login/start") return route.fulfill({ json: { ok: true, state: "pending", flowId: "test-pending-flow" } });
    if (pathname.startsWith("/api/bridge/login/flow/")) return route.fulfill({ json: { state: route.request().method() === "POST" ? "closed" : "pending" } });
    return route.fulfill({ json: { items: [], jobCount: 0, listingCount: 0 } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Account Pool", exact: true }).click();
}

test("account manager begins empty and pending/cancelled browser login never adds a ghost account", async ({ page }) => {
  await isolatedAccountUI(page);
  await expect(page.getByTestId("accounts-empty")).toContainText(empty);
  await expect(page.getByTestId("connected-account")).toHaveCount(0);
  await page.getByRole("button", { name: "Add Gemini Account", exact: true }).first().click();
  await page.getByRole("button", { name: "Open Google sign-in", exact: true }).click();
  await expect(page.getByText("Complete Google sign-in in the browser window", { exact: true })).toBeVisible();
  await expect(page.getByTestId("connected-account")).toHaveCount(0);
  const cancelled = page.waitForRequest((request) => request.url().includes("/login/flow/") && request.method() === "POST");
  await page.getByRole("button", { name: "Cancel sign-in", exact: true }).click();
  await cancelled;
  await expect(page.getByTestId("accounts-empty")).toContainText(empty);
  await page.reload();
  await page.getByRole("button", { name: "Account Pool", exact: true }).click();
  await expect(page.getByTestId("connected-account")).toHaveCount(0);
});

test("unverified legacy metadata does not become an account or a pretend avatar", async ({ page }) => {
  await isolatedAccountUI(page, [{ id: 1, label: "Unverified metadata", email: "unverified@example.test", status: "ready", dailyUsed: 200 }]);
  await expect(page.getByTestId("accounts-empty")).toContainText(empty);
  await expect(page.getByTestId("connected-account")).toHaveCount(0);
  await expect(page.getByText("unverified@example.test", { exact: true })).toHaveCount(0);
});

test("browser-launch failure stays visible without persisting an account", async ({ page }) => {
  await isolatedAccountUI(page);
  await page.route("**/api/bridge/login/start", (route) => route.fulfill({ status: 400, json: { ok: false, reason: "Could not find a Google Chrome installation on this computer." } }));
  await page.getByRole("button", { name: "Add Gemini Account", exact: true }).first().click();
  await page.getByRole("button", { name: "Open Google sign-in", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Connect your Google account" }).getByRole("alert")).toContainText("Could not find a Google Chrome installation");
  await expect(page.getByTestId("connected-account")).toHaveCount(0);
});
