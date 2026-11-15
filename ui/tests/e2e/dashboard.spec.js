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

test.describe("Sync All (issue #79)", () => {
  test("posts one sync, no refresh message, no delayed second sync", async ({ page }) => {
    await page.clock.install();
    await loginMocked(page);

    const syncUrls = [];
    await page.route("**/api/plaid/sync*", r => {
      syncUrls.push(r.request().url());
      r.fulfill({ json: { status: "ok" } });
    });

    await page.getByRole("button", { name: /sync all/i }).click();
    await expect(page.getByRole("button", { name: /sync all/i })).toBeEnabled();
    await expect(page.getByText(/refresh requested/i)).toHaveCount(0);

    // Run past the old 12s re-poll, then give any delayed fetch real time to
    // reach the route handler before counting (fastForward alone returns first).
    await page.clock.fastForward(15000);
    await page.waitForTimeout(1000);

    expect(syncUrls).toHaveLength(1);
    expect(syncUrls[0]).not.toContain("refresh");
    await expect(page.getByText(/refresh requested/i)).toHaveCount(0);
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
      r.fulfill({ json: { totals: [{ iso_currency_code: "USD", unofficial_currency_code: null, total_value: 12000 }], holdings: [
        { name: "Vanguard Total Stock", ticker_symbol: "VTSAX", security_type: "mutual fund", quantity: 100, institution_price: 120, institution_value: 12000, cost_basis: 10000, gain_loss_dollars: 2000, gain_loss_pct: 20, pct_assets: 100, iso_currency_code: "USD", unofficial_currency_code: null },
      ] } }));
    await page.route("**/api/accounts", r =>
      r.fulfill({ json: [{ id: 2, name: "Brokerage 401k", display_name: null, mask: "9876", type: "investment", subtype: "401k", institution_name: "Vanguard", current: 12000 }] }));
    await loginOnly(page);

    await page.getByText("Brokerage 401k").click();
    await expect(page.getByRole("button", { name: "Holdings", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Transactions", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Performance", exact: true })).toBeVisible();
    await expect(page.getByText("Vanguard Total Stock")).toBeVisible();
    // An all-USD account looks the same as before #141: one plain Total row.
    const footer = page.locator("tfoot tr");
    await expect(footer).toHaveCount(1);
    await expect(footer.locator("td").first()).toHaveText("Total");
    await expect(footer).toContainText("$12,000.00");
  });

  test("holdings and transactions in other currencies are not shown as dollars (#141)", async ({ page }) => {
    await mockAllAPIs(page);
    await page.route("**/api/accounts/2/holdings", r =>
      r.fulfill({ json: {
        totals: [
          { iso_currency_code: "USD", unofficial_currency_code: null, total_value: 10000 },
          { iso_currency_code: "CAD", unofficial_currency_code: null, total_value: 1234.5 },
          { iso_currency_code: null, unofficial_currency_code: "BTC", total_value: 0.5 },
        ],
        holdings: [
          { name: "Vanguard Total Market ETF", ticker_symbol: "VTI", security_type: "etf", quantity: 40, institution_price: 250, institution_value: 10000, cost_basis: null, gain_loss_dollars: null, gain_loss_pct: null, pct_assets: null, iso_currency_code: "USD", unofficial_currency_code: null },
          { name: "Shopify", ticker_symbol: "SHOP", security_type: "equity", quantity: 10, institution_price: 123.45, institution_value: 1234.5, cost_basis: null, gain_loss_dollars: null, gain_loss_pct: null, pct_assets: null, iso_currency_code: "CAD", unofficial_currency_code: null },
          { name: "Bitcoin", ticker_symbol: "BTC", security_type: "cryptocurrency", quantity: 0.5, institution_price: 1, institution_value: 0.5, cost_basis: null, gain_loss_dollars: null, gain_loss_pct: null, pct_assets: null, iso_currency_code: null, unofficial_currency_code: "BTC" },
        ],
      } }));
    await page.route("**/api/accounts/2/investment-transactions*", r =>
      r.fulfill({ json: [
        { investment_transaction_id: "it-1", date: "2026-11-01", type: "buy", subtype: "buy", name: "BUY BTC", security_name: "Bitcoin", ticker: "BTC", quantity: 0.25, amount: 0.25, fees: 0.0001, iso_currency_code: null, unofficial_currency_code: "BTC" },
        { investment_transaction_id: "it-2", date: "2026-10-01", type: "buy", subtype: "buy", name: "BUY VTI", security_name: "Vanguard Total Market ETF", ticker: "VTI", quantity: 4, amount: 1000, fees: 0, iso_currency_code: "USD", unofficial_currency_code: null },
      ] }));
    await page.route("**/api/accounts", r =>
      r.fulfill({ json: [{ id: 2, name: "Brokerage 401k", display_name: null, mask: "9876", type: "investment", subtype: "brokerage", institution_name: "Vanguard", current: 10000 }] }));
    await loginOnly(page);

    await page.getByText("Brokerage 401k").click();
    const btcRow = page.getByRole("row").filter({ hasText: "Bitcoin" });
    await expect(btcRow).toContainText("0.5 BTC");
    await expect(btcRow).not.toContainText("$");
    const footer = page.locator("tfoot tr");
    await expect(page.getByRole("row").filter({ hasText: "Shopify" })).toContainText("CA$1,234.50");
    await expect(footer).toHaveCount(3);
    await expect(footer.nth(0)).toContainText("Total (USD)");
    await expect(footer.nth(0)).toContainText("$10,000.00");
    await expect(footer.nth(1)).toContainText("Total (CAD)");
    await expect(footer.nth(1)).toContainText("CA$1,234.50");
    await expect(footer.nth(2)).toContainText("Total (BTC)");
    await expect(footer.nth(2)).toContainText("0.5 BTC");
    await expect(page.getByText("$10,000.50")).toHaveCount(0);

    await page.getByRole("button", { name: "Transactions", exact: true }).click();
    const btcTxn = page.getByRole("row").filter({ hasText: "2026-11-01" });
    await expect(btcTxn).toContainText("0.25 BTC");
    await expect(btcTxn).toContainText("0.0001 BTC");
    await expect(btcTxn).not.toContainText("$");
    await expect(page.getByRole("row").filter({ hasText: "2026-10-01" })).toContainText("$1,000.00");
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
