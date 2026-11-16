import { test, expect } from "@playwright/test";
import { loginOnly, mockAllAPIs } from "./helpers.js";

// index.html shows uncaught errors on screen for mobile debugging, but only when
// the app isn't there: a stray error in a running app used to replace the whole
// page (#162).
test.describe("On-screen error display (issue #162)", () => {
  test("a failed Sync All shows its message and keeps the app", async ({ page }) => {
    await mockAllAPIs(page);
    await page.route("**/api/plaid/sync*", r =>
      r.fulfill({ status: 500, json: { detail: "Plaid is down" } }));
    await loginOnly(page);
    await page.getByRole("button", { name: /sync all/i }).click();
    await expect(page.getByRole("alert")).toHaveText("Plaid is down");
    await expect(page.getByRole("button", { name: /sync all/i })).toBeEnabled();
    await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible();
  });

  test("a stray error or rejection in the running app leaves it on screen", async ({ page }) => {
    await mockAllAPIs(page);
    await loginOnly(page);
    await page.evaluate(() => {
      setTimeout(() => { throw new Error("stray error"); });
      Promise.reject(new Error("stray rejection"));
    });
    // Past the handler's 250ms check
    await page.waitForTimeout(600);
    await expect(page.getByText("stray", { exact: false })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible();
  });

  test("a crash that takes the app down is shown on screen", async ({ page }) => {
    await mockAllAPIs(page);
    // A null account makes the Dashboard throw while rendering.
    await page.route("**/api/accounts", r => r.fulfill({ json: [null] }));
    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("testpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    await expect(page.getByText("Error", { exact: true })).toBeVisible();
    await expect(page.locator("pre")).toContainText("TypeError");
  });

  test("the error text is shown as text, not HTML", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => {
      document.getElementById("root").replaceChildren();
      setTimeout(() => { throw new Error("<b id=injected>bold</b>"); });
    });
    await expect(page.locator("pre")).toContainText("<b id=injected>bold</b>");
    await expect(page.locator("#injected")).toHaveCount(0);
  });
});
