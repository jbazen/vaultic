import { test, expect } from "@playwright/test";
import { loginOnly, mockAllAPIs } from "./helpers.js";

test.describe("Authentication", () => {
  test("shows login page on initial load", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Vaultic")).toBeVisible();
    await expect(page.getByPlaceholder(/username/i)).toBeVisible();
  });

  test("login fails with wrong password", async ({ page }) => {
    await page.route("/api/auth/login", r =>
      r.fulfill({ status: 401, json: { detail: "Invalid credentials" } }));
    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("wrongpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    await expect(page.getByText(/invalid|error|incorrect/i)).toBeVisible();
  });

  test("login succeeds and shows dashboard", async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("testpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 8000 });
  });

  test("2FA step shows after login when required", async ({ page }) => {
    await page.route("/api/auth/login", r =>
      r.fulfill({ json: { requires_2fa: true, username: "testuser" } }));
    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("testpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    // 2FA code input — 6-digit input
    await expect(page.getByPlaceholder(/code|2fa|digit/i).or(page.locator("input[maxlength='6']"))).toBeVisible();
  });

  test("logout returns to login screen", async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("testpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    await page.waitForSelector("text=Net Worth", { timeout: 8000 });
    await page.getByRole("button", { name: /sign out|logout/i }).click();
    await expect(page.getByPlaceholder(/username/i)).toBeVisible({ timeout: 8000 });
    // The session is really gone, so a reload can't bring the Dashboard back (#162)
    await expect.poll(() => page.evaluate(() => [
      localStorage.getItem("vaultic_token"),
      localStorage.getItem("vaultic_refresh_token"),
    ])).toEqual([null, null]);
    await page.reload();
    await expect(page.getByPlaceholder(/username/i)).toBeVisible();
  });

  test("an error in a third-party script doesn't replace the app (#162)", async ({ page }) => {
    // The real Plaid Link script once threw while the Dashboard unmounted on
    // sign-out. A cross-origin error reaches the page only as "Script error.",
    // and the on-screen error handler used to wipe the whole page with it.
    await mockAllAPIs(page);
    await page.route("https://cdn.plaid.com/**", r =>
      r.fulfill({ contentType: "application/javascript", body: "throw new Error('plaid');" }));
    await page.addInitScript(() => {
      window.__errors = [];
      window.addEventListener("error", e => window.__errors.push(e.message));
    });
    await loginOnly(page);
    await expect.poll(() => page.evaluate(() => window.__errors)).toContain("Script error.");
    await expect(page.getByText("Net Worth").first()).toBeVisible();
    await page.getByRole("button", { name: /sign out|logout/i }).click();
    await expect(page.getByPlaceholder(/username/i)).toBeVisible();
  });
});
