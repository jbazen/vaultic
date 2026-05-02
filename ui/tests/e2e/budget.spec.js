/**
 * E2E tests for the Budget page CreateTransactionModal.
 *
 * Regression for issue #44: when the FAB inside ItemDetailModal opens
 * CreateTransactionModal with a pre-filled item, the Save button must
 * become enabled as soon as the user fills amount + merchant — the
 * previous validation required splits[0].amount to match the main
 * amount, but the per-row input is hidden in single-row mode so the
 * split amount could only be set by a stale auto-sync that fired once.
 */
import { test, expect } from "@playwright/test";
import { loginMocked } from "./helpers.js";

const CURRENT_MONTH = new Date().toISOString().slice(0, 7);

const MOCK_BUDGET = {
  month: CURRENT_MONTH,
  groups: [{
    id: 1, name: "Food", type: "expense", display_order: 0,
    total_planned: 500, total_spent: 0, is_archived: false,
    items: [{ id: 10, name: "Groceries", planned: 500, spent: 0, remaining: 500, is_archived: false }],
  }],
  summary: {
    total_income_planned: 0, total_income_received: 0,
    total_expense_planned: 500, total_expense_spent: 0,
    remaining_to_budget: -500,
  },
};

const MOCK_ITEM_DETAIL = {
  id: 10, name: "Groceries", planned: 500, spent: 0, remaining: 500,
  history: [{ month: CURRENT_MONTH, spent: 0, planned: 500 }],
  transactions: [],
};

test.describe("Budget — CreateTransactionModal save button", () => {
  test.beforeEach(async ({ page }) => {
    await loginMocked(page);
    await page.route(`**/api/budget/${CURRENT_MONTH}`, r =>
      r.fulfill({ json: MOCK_BUDGET }));
    await page.route("**/api/budget/items/10/detail*", r =>
      r.fulfill({ json: MOCK_ITEM_DETAIL }));
    await page.route("**/api/budget/unassigned/**", r =>
      r.fulfill({ json: [] }));
    await page.route("**/api/budget/pending-review/**", r =>
      r.fulfill({ json: [] }));
  });

  test("FAB-opened modal enables save with item pre-selected (issue #44)", async ({ page }) => {
    let postBody = null;
    await page.route("**/api/budget/manual-transaction", r => {
      postBody = r.request().postDataJSON();
      r.fulfill({ json: { ok: true, transaction_id: "manual_test_1" } });
    });

    // Navigate via the SPA nav link — page.goto() would reload and lose the
    // mocked auth token (test JWT can't be decoded by isAuthed()).
    // The Budget nav group is collapsed by default; expand it first.
    await page.locator(".nav-group-header", { hasText: "Budget" }).click();
    await page.getByRole("link", { name: /monthly budget/i }).click();
    await expect(page).toHaveURL(/\/budget$/);
    await expect(page.getByText("Groceries", { exact: true })).toBeVisible({ timeout: 10000 });
    // Clicking the item name span calls stopPropagation (it's the rename
    // affordance). Click the planned-amount cell instead — it bubbles to the
    // row's onClick which opens ItemDetailModal.
    // The Planned cell (col 3) has stopPropagation for inline edit. Click the
    // Remaining cell (col 4) which bubbles up to the row's onOpenItem handler.
    await page.locator(".budget-item-row")
      .filter({ hasText: "Groceries" })
      .first()
      .getByText("$500.00")
      .nth(1)
      .click();

    // FAB inside ItemDetailModal opens CreateTransactionModal pre-filled with item 10
    await page.getByRole("button", { name: /add transaction/i }).click();

    // Fill amount + merchant. Type the amount character-by-character so the
    // regression specifically exercises multi-keystroke updates that broke
    // the old auto-sync useEffect.
    const amountField = page.getByPlaceholder("0.00");
    await amountField.fill("");
    await amountField.pressSequentially("50.00", { delay: 30 });
    await page.getByPlaceholder(/merchant/i).fill("Trader Joe's");

    // Save button must be enabled (the bug kept it disabled)
    const saveBtn = page.getByRole("button", { name: /add expense/i });
    await expect(saveBtn).toBeEnabled();

    await saveBtn.click();
    await expect.poll(() => postBody).not.toBeNull();
    expect(postBody.amount).toBe(50);
    expect(postBody.item_id).toBe(10);
    expect(postBody.merchant_name).toBe("Trader Joe's");
    expect(postBody.is_income).toBe(false);
  });
});
