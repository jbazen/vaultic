/**
 * E2E tests for the Insperity 401K detail view on the Accounts page.
 *
 * Verifies the InsperityAccountCard renders with all 5 tabs:
 *   Holdings, Transactions, Performance, Contributions, Allocation.
 * All Insperity API endpoints are mocked with representative data.
 */
import { test, expect } from "@playwright/test";
import { mockAllAPIs, loginMocked } from "./helpers.js";

// ── Mock data matching the shapes returned by /api/insperity/* endpoints ────

const INSPERITY_ENTRY = {
  id: 99, name: "INSPERITY 401K PLAN", category: "invested",
  value: 1398.69, entered_at: "2026-04-08", account_number: "105001401K",
  exclude_from_net_worth: 0, holdings: [], notes: null,
};

const MOCK_HOLDINGS = [
  { fund: "Vanguard Target 2060", balance: 1200.00, shares: 80.0, price: 15.00, ratio_pct: 85.8 },
  { fund: "Fidelity Bond Index", balance: 198.69, shares: 20.0, price: 9.93, ratio_pct: 14.2 },
];

const MOCK_PRICES = [
  { fund: "Vanguard Target 2060", price: 15.00, prior_price: 14.80, change_pct: 1.35, asset_class: "Target Date" },
  { fund: "Fidelity Bond Index", price: 9.93, prior_price: 10.00, change_pct: -0.70, asset_class: "Fixed Income" },
];

const MOCK_PERFORMANCE = [
  { period: "1_month", cumulative: 1.43, annualized: 1.43 },
  { period: "3_month", cumulative: 3.20, annualized: 0.48 },
  { period: "ytd", cumulative: 5.10, annualized: 0.29 },
];

const MOCK_CONTRIBUTIONS = {
  snapped_at: "2026-04-08", pretax_pct: 0.0, roth_pct: 6.0,
  ytd_employee: 934.62, ytd_employer: 467.31,
  last_ee_amount: 311.54, last_ee_date: "03/30/2026",
  last_er_amount: 155.77, last_er_date: "03/30/2026",
};

const MOCK_ALLOCATIONS = [
  { fund: "Vanguard Target 2060", target_pct: 85.0, asset_class: "Target Date" },
  { fund: "Fidelity Bond Index", target_pct: 15.0, asset_class: "Fixed Income" },
];

const MOCK_TRANSACTIONS = [
  { txn_date: "03/30/2026", description: "Employee Roth Contrib", code: "09U", amount: 311.54, shares: 20.79, price: 14.99 },
  { txn_date: "03/30/2026", description: "Employer Match", code: "30U", amount: 155.77, shares: 10.40, price: 14.98 },
];

/** Register mocks for all Insperity detail endpoints used by InsperityAccountCard. */
async function mockInsperityAPIs(page) {
  await page.route("**/api/insperity/status", r =>
    r.fulfill({ json: { synced: true, last_sync: "2026-04-08T12:00:00", status: "success", total_balance: 1398.69, holdings_count: 2, duration_ms: 3, error: null } }));
  await page.route("**/api/insperity/holdings", r =>
    r.fulfill({ json: MOCK_HOLDINGS }));
  await page.route("**/api/insperity/prices", r =>
    r.fulfill({ json: MOCK_PRICES }));
  await page.route("**/api/insperity/performance", r =>
    r.fulfill({ json: MOCK_PERFORMANCE }));
  await page.route("**/api/insperity/contributions", r =>
    r.fulfill({ json: MOCK_CONTRIBUTIONS }));
  await page.route("**/api/insperity/allocations", r =>
    r.fulfill({ json: MOCK_ALLOCATIONS }));
  await page.route("**/api/insperity/transactions", r =>
    r.fulfill({ json: MOCK_TRANSACTIONS }));
}

test.describe("Insperity Account Detail", () => {
  test.beforeEach(async ({ page }) => {
    // Override manual entries to include Insperity
    await mockAllAPIs(page);
    await mockInsperityAPIs(page);
    await page.route("**/api/manual", r =>
      r.fulfill({ json: [INSPERITY_ENTRY] }));

    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("testpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    await page.waitForSelector("text=Net Worth", { timeout: 8000 });

    // Navigate to Accounts page
    await page.getByRole("button", { name: /finance/i }).click();
    await page.getByRole("link", { name: /accounts/i }).click();
  });

  test("renders Insperity card with balance", async ({ page }) => {
    await expect(page.getByText("INSPERITY 401K PLAN")).toBeVisible();
    await expect(page.getByText("$1,398.69")).toBeVisible();
  });

  test("expands to show holdings tab", async ({ page }) => {
    await page.getByText("INSPERITY 401K PLAN").click();
    await expect(page.getByText("Vanguard Target 2060")).toBeVisible();
    await expect(page.getByText("Fidelity Bond Index")).toBeVisible();
  });

  test("shows performance tab", async ({ page }) => {
    await page.getByText("INSPERITY 401K PLAN").click();
    await page.getByRole("button", { name: "Performance" }).click();
    await expect(page.getByText("1 Month")).toBeVisible();
    await expect(page.getByText("YTD")).toBeVisible();
  });

  test("shows contributions tab", async ({ page }) => {
    await page.getByText("INSPERITY 401K PLAN").click();
    await page.getByRole("button", { name: "Contributions" }).click();
    await expect(page.getByText("Roth Rate")).toBeVisible();
    await expect(page.getByText("YTD Employee")).toBeVisible();
  });

  test("shows allocation tab", async ({ page }) => {
    await page.getByText("INSPERITY 401K PLAN").click();
    await page.getByRole("button", { name: "Allocation" }).click();
    await expect(page.getByText("85.0%")).toBeVisible();
    await expect(page.getByText("Target Date")).toBeVisible();
  });

  test("shows transactions tab", async ({ page }) => {
    await page.getByText("INSPERITY 401K PLAN").click();
    await page.getByRole("button", { name: "Transactions" }).click();
    await expect(page.getByText("Employee Roth Contrib")).toBeVisible();
    await expect(page.getByText("Employer Match")).toBeVisible();
  });
});

test.describe("Plaid Reconnect", () => {
  test("stale institution shows Reconnect button and login-expired warning", async ({ page }) => {
    await mockAllAPIs(page);
    // A Chase account exists (from helpers), and its Plaid item is long-stale.
    // Date is far in the past so it's stale regardless of when CI runs.
    await page.route("**/api/plaid/items", r =>
      r.fulfill({ json: [{
        id: 1, item_id: "item-chase-stale", institution_name: "Chase",
        last_synced_at: "2020-01-01 08:00:06", created_at: "2019-12-01 05:13:31",
      }] }));

    await page.goto("/");
    await page.getByPlaceholder(/username/i).fill("testuser");
    await page.getByPlaceholder(/password/i).fill("testpassword");
    await page.getByRole("button", { name: /sign in|login|log in/i }).click();
    await page.waitForSelector("text=Net Worth", { timeout: 8000 });

    await page.getByRole("button", { name: /finance/i }).click();
    await page.getByRole("link", { name: /accounts/i }).click();

    await expect(page.getByRole("button", { name: /reconnect/i })).toBeVisible();
    await expect(page.getByText(/login expired, click Reconnect/i)).toBeVisible();
  });
});
