/**
 * E2E tests for the Financial Calendar on the Dashboard.
 *
 * These tests verify the calendar section renders correctly and that the
 * create-event modal opens, without requiring a real backend. All API calls
 * are intercepted by the shared mock in helpers.js.
 */
import { test, expect } from "@playwright/test";
import { loginMocked } from "./helpers.js";

test.describe("Financial Calendar", () => {
  test.beforeEach(async ({ page }) => {
    await loginMocked(page);
  });

  test("calendar section is visible on the dashboard", async ({ page }) => {
    // The section heading should appear after the calendar mounts
    await expect(page.getByText("Financial Calendar")).toBeVisible({ timeout: 8000 });
  });

  test("react-big-calendar month view renders", async ({ page }) => {
    // RBC renders a toolbar with navigation buttons — confirms the library mounted
    await expect(page.locator(".rbc-toolbar")).toBeVisible({ timeout: 8000 });
  });

  test("month/week/day view buttons are present", async ({ page }) => {
    await page.locator(".rbc-toolbar").waitFor({ timeout: 8000 });
    // RBC renders view-switch buttons inside the toolbar; use exact role to
    // avoid "Day" matching "Today" and "Month" matching month-label text
    const toolbar = page.locator(".rbc-toolbar");
    await expect(toolbar.getByRole("button", { name: "Month", exact: true })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "Week", exact: true })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "Day", exact: true })).toBeVisible();
  });

  test("add event button opens the create modal", async ({ page }) => {
    // Wait for calendar to mount
    await page.locator(".rbc-toolbar").waitFor({ timeout: 8000 });

    // Click the "+ Add Event" button in the CalendarSection header
    await page.getByRole("button", { name: /add event/i }).click();

    // EventFormModal should appear with "New Event" title
    await expect(page.getByText("New Event")).toBeVisible({ timeout: 4000 });
  });

  test("create modal has required fields", async ({ page }) => {
    await page.locator(".rbc-toolbar").waitFor({ timeout: 8000 });
    await page.getByRole("button", { name: /add event/i }).click();
    await page.getByText("New Event").waitFor({ timeout: 4000 });

    // Title input, event type dropdown, and cancel button should be present
    await expect(page.getByPlaceholder("Event title")).toBeVisible();
    await expect(page.getByRole("button", { name: /cancel/i })).toBeVisible();
  });

  test("cancel button closes the create modal", async ({ page }) => {
    await page.locator(".rbc-toolbar").waitFor({ timeout: 8000 });
    await page.getByRole("button", { name: /add event/i }).click();
    await page.getByText("New Event").waitFor({ timeout: 4000 });

    await page.getByRole("button", { name: /cancel/i }).click();

    // Modal should disappear
    await expect(page.getByText("New Event")).not.toBeVisible({ timeout: 4000 });
  });

  test("switching to week view works", async ({ page }) => {
    await page.locator(".rbc-toolbar").waitFor({ timeout: 8000 });
    await page.locator(".rbc-toolbar").getByRole("button", { name: "Week", exact: true }).click();

    // Week view renders a time grid with hourly labels
    await expect(page.locator(".rbc-time-view")).toBeVisible({ timeout: 4000 });
  });

  test("switching to day view works", async ({ page }) => {
    await page.locator(".rbc-toolbar").waitFor({ timeout: 8000 });
    // Use exact match to avoid hitting "Today" button which also contains "Day"
    await page.locator(".rbc-toolbar").getByRole("button", { name: "Day", exact: true }).click();

    // Day view also renders the time grid
    await expect(page.locator(".rbc-time-view")).toBeVisible({ timeout: 4000 });
  });
});
