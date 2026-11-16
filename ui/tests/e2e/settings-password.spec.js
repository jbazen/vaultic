import { test, expect } from "@playwright/test";
import { fakeJwt, loginOnly, mockAllAPIs } from "./helpers.js";

async function fillAndSubmit(page) {
  const field = label => page.locator(".form-group")
    .filter({ has: page.locator(".form-label", { hasText: new RegExp(`^${label}$`) }) })
    .locator("input");
  await field("Current password").fill("OldPass12");
  await field("New password").fill("NewPass12");
  await field("Confirm new password").fill("NewPass12");
  await page.getByRole("button", { name: "Update password" }).click();
}

async function storedTokens(page) {
  return page.evaluate(() => ({
    token: localStorage.getItem("vaultic_token"),
    refresh: localStorage.getItem("vaultic_refresh_token"),
  }));
}

test.describe("Change password (issue #132)", () => {
  test("web session keeps working with the replacement token", async ({ page }) => {
    const newToken = fakeJwt("web-after-change");
    let sentBody = null;
    await mockAllAPIs(page);
    await page.route("**/api/auth/change-password", r => {
      sentBody = r.request().postDataJSON();
      r.fulfill({ json: { ok: true, token: newToken } });
    });
    await loginOnly(page);
    await page.getByRole("link", { name: /settings/i }).click();

    await fillAndSubmit(page);

    await expect(page.getByText(/other devices have been signed out/i)).toBeVisible();
    expect(sentBody).toEqual({ current_password: "OldPass12", new_password: "NewPass12", remember_me: false });
    expect(await storedTokens(page)).toEqual({ token: newToken, refresh: null });
    // Still signed in: the next request carries the new token.
    const meRequest = page.waitForRequest("**/api/auth/me");
    await page.getByRole("link", { name: /dashboard/i }).first().click();
    await page.getByRole("link", { name: /settings/i }).click();
    expect((await meRequest).headers()["authorization"]).toBe(`Bearer ${newToken}`);
    await expect(page.getByPlaceholder(/username/i)).toHaveCount(0);
  });

  test("keep-me-signed-in session gets a new refresh token", async ({ page }) => {
    const newToken = fakeJwt("mobile-after-change");
    let sentBody = null;
    await mockAllAPIs(page);
    await page.route("**/api/auth/change-password", r => {
      sentBody = r.request().postDataJSON();
      r.fulfill({ json: { ok: true, token: newToken, refresh_token: "refresh-after-change" } });
    });
    await loginOnly(page);
    await page.evaluate(token => {
      localStorage.setItem("vaultic_token", token);
      localStorage.setItem("vaultic_refresh_token", "refresh-before-change");
    }, fakeJwt("mobile-before-change"));
    await page.getByRole("link", { name: /settings/i }).click();

    await fillAndSubmit(page);

    await expect(page.getByText(/other devices have been signed out/i)).toBeVisible();
    expect(sentBody.remember_me).toBe(true);
    expect(await storedTokens(page)).toEqual({ token: newToken, refresh: "refresh-after-change" });
  });

  test("wrong current password shows an error and keeps the session", async ({ page }) => {
    await mockAllAPIs(page);
    await page.route("**/api/auth/change-password", r =>
      r.fulfill({ status: 401, json: { detail: "Current password is incorrect" } }));
    await loginOnly(page);
    await page.getByRole("link", { name: /settings/i }).click();
    const before = await storedTokens(page);

    await fillAndSubmit(page);

    await expect(page.getByText("Current password is incorrect")).toBeVisible();
    expect(await storedTokens(page)).toEqual(before);
  });

  test("too many wrong guesses shows the lockout message and keeps the session", async ({ page }) => {
    // Issue #151: the server answers 429 once the login lockout is hit.
    await mockAllAPIs(page);
    await page.route("**/api/auth/change-password", r =>
      r.fulfill({ status: 429, json: { detail: "Too many failed attempts. Try again in 15 minutes." } }));
    await loginOnly(page);
    await page.getByRole("link", { name: /settings/i }).click();
    const before = await storedTokens(page);

    await fillAndSubmit(page);

    await expect(page.getByText("Too many failed attempts. Try again in 15 minutes.")).toBeVisible();
    expect(await storedTokens(page)).toEqual(before);
    await expect(page.getByPlaceholder(/username/i)).toHaveCount(0);
  });

  test("Security Log polling during the change does not sign the user out", async ({ page }) => {
    // The server kills the old token before it answers, so any request still
    // carrying it gets a real 401, while the change is still pending. Polls
    // sent during the change must wait, and a poll already in flight must wait
    // for the change and retry with the new token.
    const oldAuth = "Bearer test-jwt-token";
    const newToken = fakeJwt("after-poll-race");
    let changed = false;
    let markKilled;
    const killed = new Promise(resolve => { markKilled = resolve; });
    const logAuths = [];
    await mockAllAPIs(page);
    await page.route("**/api/auth/security-log*", async r => {
      const auth = r.request().headers()["authorization"];
      logAuths.push({ auth, afterChange: changed });
      if (!changed) await killed;               // in flight across the change
      if (auth === oldAuth) {
        return r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" },
                           json: { detail: "Not authenticated" } });
      }
      return r.fulfill({ json: { lines: [] } });
    });
    await page.route("**/api/auth/change-password", async r => {
      changed = true;
      await new Promise(resolve => setTimeout(resolve, 1000));
      markKilled();                             // 401s land before the response
      await new Promise(resolve => setTimeout(resolve, 2500)); // spans a 3 s poll
      await r.fulfill({ json: { ok: true, token: newToken } });
    });
    await loginOnly(page);
    await page.getByRole("link", { name: /settings/i }).click();
    await expect.poll(() => logAuths.length).toBeGreaterThan(0);

    await fillAndSubmit(page);

    await expect(page.getByText(/other devices have been signed out/i)).toBeVisible({ timeout: 8000 });
    await expect.poll(() => logAuths.some(l => l.auth === `Bearer ${newToken}`)).toBe(true);
    expect(logAuths.filter(l => l.afterChange && l.auth === oldAuth)).toEqual([]);
    await page.waitForTimeout(500);
    expect(await storedTokens(page)).toEqual({ token: newToken, refresh: null });
    await expect(page.getByPlaceholder(/username/i)).toHaveCount(0);
  });

  test("another tab signed out by the change signs back in when the new token lands", async ({ page, context }) => {
    const newToken = fakeJwt("cross-tab");
    let changed = false;
    let release;
    const respond = new Promise(resolve => { release = resolve; });
    await mockAllAPIs(page);
    await page.route("**/api/auth/change-password", async r => {
      changed = true;
      await respond;
      await r.fulfill({ json: { ok: true, token: newToken } });
    });
    await loginOnly(page);
    // A JWT-shaped token, so a freshly opened tab counts as signed in.
    await page.evaluate(token => localStorage.setItem("vaultic_token", token), fakeJwt("before"));
    await page.getByRole("link", { name: /settings/i }).click();

    // A second tab on Settings: its Security Log poll still carries the old token.
    const other = await context.newPage();
    await mockAllAPIs(other);
    await other.route("**/api/auth/security-log*", r => {
      const auth = r.request().headers()["authorization"];
      if (changed && auth !== `Bearer ${newToken}`) {
        return r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" },
                           json: { detail: "Not authenticated" } });
      }
      return r.fulfill({ json: { lines: [] } });
    });
    await other.goto("/settings");
    await expect(other.getByRole("heading", { name: "Settings" })).toBeVisible();

    await fillAndSubmit(page);
    // Its next poll hits the dead token before this tab has the new one.
    await expect(other.getByPlaceholder(/username/i)).toBeVisible({ timeout: 8000 });

    release();

    await expect(page.getByText(/other devices have been signed out/i)).toBeVisible();
    await expect(other.getByPlaceholder(/username/i)).toHaveCount(0);
    expect(await storedTokens(page)).toEqual({ token: newToken, refresh: null });
  });
});

test.describe("Disable 2FA errors (issue #151)", () => {
  async function tryDisable(page, status, detail) {
    await mockAllAPIs(page);
    await page.route("**/api/auth/me", r =>
      r.fulfill({ json: { username: "testuser", two_fa_enabled: 1 } }));
    await page.route("**/api/auth/2fa/disable", r => r.fulfill({ status, json: { detail } }));
    await loginOnly(page);
    await page.getByRole("link", { name: /settings/i }).click();
    const before = await storedTokens(page);
    page.once("dialog", d => d.accept("SomePass12"));
    await page.getByRole("button", { name: "Disable 2FA" }).click();
    return before;
  }

  test("too many wrong guesses shows the lockout message and keeps the session", async ({ page }) => {
    const before = await tryDisable(page, 429, "Too many failed attempts. Try again in 15 minutes.");
    await expect(page.getByText("Too many failed attempts. Try again in 15 minutes.")).toBeVisible();
    expect(await storedTokens(page)).toEqual(before);
    await expect(page.getByRole("button", { name: "Disable 2FA" })).toBeVisible();
  });

  test("wrong password shows the server's message", async ({ page }) => {
    await tryDisable(page, 401, "Password is incorrect");
    await expect(page.getByText("Password is incorrect")).toBeVisible();
    await expect(page.getByRole("button", { name: "Disable 2FA" })).toBeVisible();
  });
});
