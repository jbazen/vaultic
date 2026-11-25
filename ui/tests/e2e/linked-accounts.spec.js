import { test, expect } from "@playwright/test";
import { mockAllAPIs, loginOnly } from "./helpers.js";

// #181: each LPL account is linked to the Parker (Investor360) account it moved
// from; the Dashboard shows the Parker-style summary for the LPL accounts.

const LPL_LINKED = {
  id: 30, name: "SAM - Retirement", mask: "9001", type: "investment", subtype: "ira",
  institution_name: "LPL Financial", source: "plaid", plaid_item_id: "item-lpl-2", current: 2000,
  link_id: 7, link_active: 1, linked_from_id: 21, linked_from_name: "Old Roth IRA",
  linked_from_mask: "0002", linked_until: "2026-11-03",
};

const SUMMARY = {
  groups: [{
    institution: "LPL Financial", account_ids: [30], total_value: 2000,
    categories: [{ asset_name: "Large-Cap Growth", market_value: 1500 },
                 { asset_name: "Cash and Equivalents", market_value: 500 }],
    asset_classes: [{ asset_name: "Domestic Equity", market_value: 1500 }],
    top_holdings: [{ symbol: "FZAFX", description: "Fidelity Advisor Series I", value_dollars: 1500 }],
    balance_history: [
      { balance_date: "2026-10-31", market_value: 1900, net_investment: 1500 },
      { balance_date: "2026-11-03", market_value: 1950, net_investment: 1500 },
      { balance_date: "2026-11-25", market_value: 2000, net_investment: null },
    ],
    predecessor_institution: "Parker Financial (Commonwealth/NFS)",
    predecessor_as_of: "2026-11-03", parker_only: true,
  }],
};

async function setup(page, { accounts = [LPL_LINKED], summary = SUMMARY } = {}) {
  await mockAllAPIs(page);
  await page.route("**/api/accounts", r => r.fulfill({ json: accounts }));
  await page.route("**/api/linked-accounts/summary", r => r.fulfill({ json: summary }));
  await page.route("**/api/linked-accounts/allocation/30", r => r.fulfill({ json: {
    categories: SUMMARY.groups[0].categories, asset_classes: SUMMARY.groups[0].asset_classes } }));
  await page.route("**/api/investor360/performance*", r => r.fulfill({ json: [
    { time_period: "YTD", display_name: "Year to Date", portfolio_return: 8.5,
      benchmark_sp500: 10.1, benchmark_bond: 2.0, benchmark_tbill: 4.0 } ] }));
  await page.route("**/api/investor360/activity-summary*", r => r.fulfill({ json: {} }));
}

test.describe("LPL accounts linked to their Parker history (#181)", () => {
  test("Dashboard shows the LPL portfolio summary with Parker's history", async ({ page }) => {
    await setup(page);
    await loginOnly(page);
    const section = page.getByTestId("linked-portfolio");
    await expect(section.getByText("LPL Financial — Portfolio Summary")).toBeVisible();
    await expect(section.getByText("Large-Cap Growth")).toBeVisible();
    await expect(section.getByText("Fidelity Advisor Series I")).toBeVisible();
    await expect(section.getByText(/through Nov 3, 2026, then LPL Financial/)).toBeVisible();
    await expect(section.getByText("Year to Date")).toBeVisible();
  });

  test("no summary without links", async ({ page }) => {
    await setup(page, { summary: { groups: [] } });
    await loginOnly(page);
    await expect(page.getByText("SAM - Retirement (...9001)")).toBeVisible();
    await expect(page.getByTestId("linked-portfolio")).toHaveCount(0);
  });

  test("linked card shows allocation and Parker tabs, and Unlink undoes the link", async ({ page }) => {
    await setup(page);
    // After the Unlink, the server reports the link undone and no summary
    let linked = true;
    await page.route("**/api/accounts", r =>
      r.fulfill({ json: [{ ...LPL_LINKED, link_active: linked ? 1 : 0 }] }));
    await page.route("**/api/linked-accounts/summary", r =>
      r.fulfill({ json: linked ? SUMMARY : { groups: [] } }));
    const patches = [];
    await page.route("**/api/linked-accounts/7", async r => {
      patches.push(r.request().postDataJSON());
      linked = false;
      await r.fulfill({ json: { ok: true, active: false } });
    });
    await loginOnly(page);
    await page.getByText("SAM - Retirement (...9001)").click();
    await expect(page.getByText(/comes from Old Roth IRA \(\.\.\.0002\)/)).toBeVisible();

    await page.getByRole("button", { name: "Allocation", exact: true }).click();
    await expect(page.getByText("Asset Class")).toBeVisible();
    await expect(page.locator("#accounts").getByText("Domestic Equity")).toBeVisible();

    await page.getByRole("button", { name: "Parker", exact: true }).click();
    await expect(page.getByText("From Parker, as of Nov 3, 2026.")).toBeVisible();

    await page.getByRole("button", { name: /Unlink history of Old Roth IRA/ }).click();
    await expect.poll(() => patches).toEqual([{ active: false }]);
    // The card and the Dashboard summary both follow the change without a reload
    await expect(page.getByRole("button", { name: /Relink history of Old Roth IRA/ })).toBeVisible();
    await expect(page.getByTestId("linked-portfolio")).toHaveCount(0);
  });

  test("a failed Unlink shows its message", async ({ page }) => {
    await setup(page);
    await page.route("**/api/linked-accounts/7", r =>
      r.fulfill({ status: 500, json: { detail: "Internal server error" } }));
    await loginOnly(page);
    await page.getByText("SAM - Retirement (...9001)").click();
    await page.getByRole("button", { name: /Unlink history of Old Roth IRA/ }).click();
    await expect(page.getByRole("alert")).toContainText("Internal server error");
  });

  test("an unlinked card has no extra tabs and offers Relink", async ({ page }) => {
    await setup(page, { accounts: [{ ...LPL_LINKED, link_active: 0 }] });
    await loginOnly(page);
    await page.getByText("SAM - Retirement (...9001)").click();
    await expect(page.getByRole("button", { name: /Relink history of Old Roth IRA/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Allocation", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Parker", exact: true })).toHaveCount(0);
  });
});
