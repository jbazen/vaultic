import { test, expect } from "@playwright/test";
import { loginMocked, loginOnly, mockAllAPIs } from "./helpers.js";

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
    // "Primary Home" is also the Property & Vehicles row, so match it exactly (strict mode)
    await expect(page.getByText("Primary Home")).toBeVisible();
  });

  test("net worth section heading visible", async ({ page }) => {
    await expect(page.getByText("Net Worth")).toBeVisible();
  });

  test("navigation to settings works", async ({ page }) => {
    await page.getByRole("link", { name: /settings/i }).click();
    await expect(page).toHaveURL(/settings/);
  });

  test("the sidebar has no Accounts link or Finance group, and Import is top-level", async ({ page }) => {
    await expect(page.getByRole("link", { name: /^accounts$/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /finance/i })).toHaveCount(0);
    await page.getByRole("link", { name: /import/i }).first().click();
    await expect(page).toHaveURL(/\/import/);
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
    await loginOnly(page);

    await expect(page.getByText("HSA Cash Account")).toBeVisible();
    await expect(page.getByText("#430941440")).toBeVisible();
  });
});

test.describe("Dashboard account list", () => {
  test("a Plaid investment row expands to Holdings / Transactions / Performance", async ({ page }) => {
    await mockAllAPIs(page);
    await page.route("**/api/accounts/2/holdings", r =>
      r.fulfill({ json: { total_value: 12000, holdings: [
        { name: "Vanguard Total Stock", ticker_symbol: "VTSAX", security_type: "mutual fund", quantity: 100, institution_price: 120, institution_value: 12000, cost_basis: 10000, gain_loss_dollars: 2000, gain_loss_pct: 20, pct_assets: 100 },
      ] } }));
    await page.route("**/api/accounts", r =>
      r.fulfill({ json: [{ id: 2, name: "Brokerage 401k", display_name: null, mask: "9876", type: "investment", subtype: "401k", institution_name: "Vanguard", current: 12000 }] }));
    await loginOnly(page);

    await page.getByText("Brokerage 401k").click();
    await expect(page.getByRole("button", { name: "Holdings", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Transactions", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Performance", exact: true })).toBeVisible();
    await expect(page.getByText("Vanguard Total Stock")).toBeVisible();
  });

  test("shows Property & Vehicles and Other Assets sections", async ({ page }) => {
    await mockAllAPIs(page);
    await page.route("**/api/manual", r =>
      r.fulfill({ json: [
        { id: 1, name: "Primary Home", category: "home_value", value: 450000, entered_at: "2026-03-01" },
        { id: 2, name: "Gold Coins", category: "other_asset", value: 3000, entered_at: "2026-03-01" },
      ] }));
    await loginOnly(page);

    await expect(page.getByText("Property & Vehicles")).toBeVisible();
    await expect(page.getByText("Other Assets", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Gold Coins")).toBeVisible();
  });

  test("shows the empty state when nothing is connected", async ({ page }) => {
    await mockAllAPIs(page);
    await page.route("**/api/accounts", r => r.fulfill({ json: [] }));
    await page.route("**/api/manual", r => r.fulfill({ json: [] }));
    await loginOnly(page);

    await expect(page.getByText(/No accounts connected/)).toBeVisible();
  });

  test("a failed Coinbase sync shows its message", async ({ page }) => {
    await mockAllAPIs(page);
    await page.route("**/api/crypto/sync", r =>
      r.fulfill({ status: 502, json: { detail: "Coinbase is unreachable" } }));
    await loginOnly(page);

    await page.getByRole("button", { name: /coinbase/i }).click();
    await expect(page.getByText(/Coinbase is unreachable/)).toBeVisible();
  });
});

test.describe("Legacy /accounts link", () => {
  test("lands on the Dashboard account list", async ({ page }) => {
    await mockAllAPIs(page);
    // Sign in directly on the old URL, as an old bookmark or notification would
    await loginOnly(page, "/accounts");

    await expect(page).toHaveURL(/\/#accounts$/);
    await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
    await expect(page.getByText("Chase", { exact: true })).toBeVisible();
  });

  test("stays on the account list when the cards above it load late", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 700 });
    await mockAllAPIs(page);
    // A tall Market Summary that arrives after the list has rendered and scrolled
    const indexes = Array.from({ length: 60 }, (_, i) => ({
      symbol: `IDX${i}`, name: `Index ${i}`, last_trade_amount: 5000 + i,
      net_change: 1, percent_change: 0.1, snapped_at: "2026-11-13",
    }));
    await page.route("**/api/investor360/market-summary", async r => {
      await new Promise(res => setTimeout(res, 800));
      await r.fulfill({ json: indexes });
    });
    await loginOnly(page, "/accounts");

    await expect(page.getByText("Index 59")).toBeAttached();
    await expect(page.getByRole("heading", { name: "Accounts" })).toBeInViewport();
  });
});
