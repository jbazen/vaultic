import { test, expect } from "@playwright/test";
import { loginMocked } from "./helpers.js";

test.describe("Sage Chat", () => {
  test.beforeEach(async ({ page }) => {
    await loginMocked(page);
  });

  test("floating Sage button is visible", async ({ page }) => {
    await expect(page.getByTitle("Chat with Sage")).toBeVisible();
  });

  test("clicking Sage button opens chat panel", async ({ page }) => {
    await page.getByTitle("Chat with Sage").click();
    await expect(page.getByText("I'm Sage")).toBeVisible();
  });

  test("Sage responds to typed message", async ({ page }) => {
    await page.route("**/api/sage/chat", r =>
      r.fulfill({ json: { response: "Your net worth looks strong!", history: [] } }));

    await page.getByTitle("Chat with Sage").click();
    await page.getByPlaceholder(/ask sage/i).fill("What is my net worth?");
    await page.locator("button").filter({ hasText: "➤" }).click();
    await expect(page.getByText("Your net worth looks strong!")).toBeVisible({ timeout: 10000 });
  });

  test("Sage panel persists when navigating between pages", async ({ page }) => {
    await page.getByTitle("Chat with Sage").click();
    await expect(page.getByText("I'm Sage")).toBeVisible();
    // Navigate to another page — expand the Finance group first
    await page.getByRole("button", { name: /finance/i }).click();
    await page.getByRole("link", { name: /transactions/i }).click();
    // Sage panel should still be open
    await expect(page.getByText("I'm Sage")).toBeVisible();
  });

  test("closing and reopening Sage panel shows the same session", async ({ page }) => {
    await page.route("**/api/sage/chat", r =>
      r.fulfill({ json: { response: "Hello! Your cash balance is $5,000.", history: [] } }));

    await page.getByTitle("Chat with Sage").click();
    await page.getByPlaceholder(/ask sage/i).fill("Hello");
    await page.locator("button").filter({ hasText: "➤" }).click();
    await expect(page.getByText("Hello! Your cash balance is $5,000.")).toBeVisible({ timeout: 10000 });

    // Close the panel
    await page.locator("button").filter({ hasText: "✕" }).click();
    await expect(page.getByTitle("Chat with Sage")).toBeVisible();

    // Reopen — session should be restored from sessionStorage
    await page.getByTitle("Chat with Sage").click();
    await expect(page.getByText("Hello! Your cash balance is $5,000.")).toBeVisible();
  });

  test("Hey Sage button toggles always-on mode", async ({ page }) => {
    await page.getByTitle("Chat with Sage").click();
    const heyBtn = page.getByTitle(/enable hey sage|always listening/i);
    await heyBtn.click();
    await expect(page.getByTitle(/disable hey sage/i)).toBeVisible();
  });
});
