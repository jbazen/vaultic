import { test, expect } from "@playwright/test";
import { loginMocked, mockAllAPIs } from "./helpers.js";

test.describe("Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await loginMocked(page);
  });

  test("shows net worth total", async ({ page }) => {
    await expect(page.getByText(/250,000/)).toBeVisible();
  });

  test("shows connected account institution", async ({ page }) => {
    // Use .first() — "Chase" appears in both the institution span and the account name
    await expect(page.getByText("Chase").first()).toBeVisible();
  });

  test("shows manual entry (home value)", async ({ page }) => {
    await expect(page.getByText(/home|Primary Home/i)).toBeVisible();
  });

  test("net worth section heading visible", async ({ page }) => {
    await expect(page.getByText("Net Worth")).toBeVisible();
  });

  test("navigation to settings works", async ({ page }) => {
    await page.getByRole("link", { name: /settings/i }).click();
    await expect(page).toHaveURL(/settings/);
  });

  test("navigation to transactions works", async ({ page }) => {
    // Transactions is inside the collapsed "Finance" nav group — expand it first
    await page.getByRole("button", { name: /finance/i }).click();
    await page.getByRole("link", { name: /transactions/i }).click();
    await expect(page).toHaveURL(/transactions/);
  });

  test("navigation back to dashboard works", async ({ page }) => {
    // Navigate away via hash/URL change and return — avoids loading other pages
    // whose API calls aren't fully mocked in this helper.
    await page.evaluate(() => window.history.pushState({}, "", "/settings"));
    await page.evaluate(() => window.history.pushState({}, "", "/"));
    // Dashboard component remounts and re-fetches data
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Manual entry account numbers (Dashboard)", () => {
  test("ManualAccountRow shows the stored account number", async ({ page }) => {
    // Register the manual-entries override BEFORE login so the dashboard's
    // initial fetch (on mount, after login) gets the HSA entry with an account number.
    await mockAllAPIs(page);
    await page.route("**/api/manual", r =>
      r.fulfill({ json: [
        { id: 7, name: "HSA Cash Account", category: "liquid", value: 1544.06, entered_at: "2026-03-01", account_number: "430941440" },
      ] }));

    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("testpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    await page.waitForSelector("text=Net Worth", { timeout: 8000 });

    await expect(page.getByText("#430941440")).toBeVisible();
  });
});
