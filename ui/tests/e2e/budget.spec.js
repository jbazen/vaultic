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

// Regression for issue #74: an expense item with $0 planned but real spending
// used to render "—" in the default Remaining column, hiding the activity.
const MOCK_BUDGET_UNPLANNED_SPEND = {
  month: CURRENT_MONTH,
  groups: [{
    id: 2, name: "Sinking Funds", type: "expense", display_order: 0,
    total_planned: 0, total_spent: 75, is_archived: false,
    items: [
      { id: 20, name: "Car Repair", planned: 0, spent: 75, remaining: -75, is_archived: false },
      { id: 21, name: "Homeschool", planned: 0, spent: 0, remaining: 0, is_archived: false },
    ],
  }],
  summary: {
    total_income_planned: 0, total_income_received: 0,
    total_expense_planned: 0, total_expense_spent: 75,
    remaining_to_budget: 0,
  },
};

// Regression for issue #75: notes on a transaction sitting in the New/Pending/
// Tracked queues, and carrying over once the transaction is assigned.
const MOCK_BUDGET_NOTES = {
  month: CURRENT_MONTH,
  groups: [{
    id: 3, name: "Food", type: "expense", display_order: 0,
    total_planned: 300, total_spent: 0, is_archived: false,
    items: [{ id: 30, name: "Groceries", planned: 300, spent: 0, remaining: 300, is_archived: false }],
  }],
  summary: {
    total_income_planned: 0, total_income_received: 0,
    total_expense_planned: 300, total_expense_spent: 0,
    remaining_to_budget: -300,
  },
};

const UNASSIGNED_TXN_NO_NOTE = {
  transaction_id: "txn_notes_1", date: "2026-03-10", name: "Trader Joe's",
  merchant_name: "Trader Joe's", amount: 42.50, category: "Groceries",
  account_name: "Chase Checking", account_mask: "1234", notes: null,
};

test.describe("Budget — transaction notes (issue #75)", () => {
  test.beforeEach(async ({ page }) => {
    await loginMocked(page);
    await page.route(`**/api/budget/${CURRENT_MONTH}`, r =>
      r.fulfill({ json: MOCK_BUDGET_NOTES }));
    await page.route("**/api/budget/pending-review/**", r => r.fulfill({ json: [] }));
    await page.route("**/api/budget/deleted/**", r => r.fulfill({ json: [] }));

    await page.locator(".nav-group-header", { hasText: "Budget" }).click();
    await page.getByRole("link", { name: /monthly budget/i }).click();
    await expect(page).toHaveURL(/\/budget$/);
  });

  test("add a note on an unassigned transaction, then see it carry over once tracked", async ({ page }) => {
    let notesPatchBody = null;
    let currentNote = null;

    // Unassigned list reflects whatever note was last saved, so the UI can
    // be asserted against after the row reloads post-save.
    await page.route("**/api/budget/unassigned/**", r =>
      r.fulfill({ json: [{ ...UNASSIGNED_TXN_NO_NOTE, notes: currentNote }] }));
    await page.route("**/api/budget/assigned/**", r =>
      r.fulfill({ json: currentNote ? [{
        transaction_id: "txn_notes_1", date: "2026-03-10", name: "Trader Joe's",
        merchant_name: "Trader Joe's", amount: 42.50, category: "Groceries",
        item_id: 30, item_name: "Groceries", group_name: "Food",
        account_name: "Chase Checking", account_mask: "1234", notes: currentNote,
      }] : [] }));
    await page.route("**/api/budget/transactions/txn_notes_1/notes", r => {
      notesPatchBody = r.request().postDataJSON();
      currentNote = notesPatchBody.notes;
      r.fulfill({ json: { ok: true, transaction_id: "txn_notes_1", notes: currentNote } });
    });
    await page.route("**/api/budget/assign", r => {
      r.fulfill({ json: { ok: true } });
    });

    // Open the Transactions panel → New tab (unassigned queue)
    await page.getByRole("button", { name: "Transactions", exact: true }).click();
    await page.getByRole("button", { name: /^New/ }).click();
    await expect(page.getByText("Trader Joe's")).toBeVisible();

    // No note yet — shows the "+ note" affordance
    await page.getByRole("button", { name: "+ note" }).click();
    await page.locator("textarea[placeholder='Add a note…']").fill("Double-check this charge");
    await page.getByRole("button", { name: "Save", exact: true }).click();

    await expect.poll(() => notesPatchBody).not.toBeNull();
    expect(notesPatchBody.notes).toBe("Double-check this charge");

    // Row now shows the saved note instead of the "+ note" affordance
    await expect(page.getByText("📝 Double-check this charge")).toBeVisible();

    // Assign it to the budget item — carries the note along with it
    await page.locator("select.budget-item-select").selectOption("30");

    // Switch to Tracked — the same note is still there, no copy step required
    await page.getByRole("button", { name: /^Tracked/ }).click();
    await expect(page.getByText("📝 Double-check this charge")).toBeVisible();
  });
});

test.describe("Budget — unplanned spending visibility (issue #74)", () => {
  test.beforeEach(async ({ page }) => {
    await loginMocked(page);
    await page.route(`**/api/budget/${CURRENT_MONTH}`, r =>
      r.fulfill({ json: MOCK_BUDGET_UNPLANNED_SPEND }));
    await page.route("**/api/budget/unassigned/**", r => r.fulfill({ json: [] }));
    await page.route("**/api/budget/pending-review/**", r => r.fulfill({ json: [] }));
  });

  test("$0-planned item with spending shows the amount, not a dash", async ({ page }) => {
    await page.locator(".nav-group-header", { hasText: "Budget" }).click();
    await page.getByRole("link", { name: /monthly budget/i }).click();
    await expect(page).toHaveURL(/\/budget$/);

    // Car Repair: planned 0, spent 75 → shows the overspend in the Remaining column
    const carRepair = page.locator(".budget-item-row").filter({ hasText: "Car Repair" });
    await expect(carRepair).toBeVisible({ timeout: 10000 });
    await expect(carRepair.getByText("-$75.00")).toBeVisible();

    // Homeschool: planned 0, spent 0 → genuinely empty, still a dash
    const homeschool = page.locator(".budget-item-row").filter({ hasText: "Homeschool" });
    await expect(homeschool.getByText("—")).toBeVisible();
  });
});
