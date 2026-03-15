import { test, expect } from "@playwright/test";
import { mockAllAPIs } from "./helpers.js";

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
    await expect(page.getByPlaceholder(/username/i)).toBeVisible();
  });
});
