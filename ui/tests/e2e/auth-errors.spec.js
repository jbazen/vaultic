import { test, expect } from "@playwright/test";
import { mockAllAPIs } from "./helpers.js";

const LOCKED = "Too many failed attempts. Try again in 15 minutes.";

function fakeJwt(tag, exp) {
  const payload = btoa(JSON.stringify({ sub: "testuser", exp, tag }));
  return `e30.${payload}.sig`;
}

async function submitLogin(page) {
  await page.goto("/");
  await page.getByPlaceholder(/username/i).fill("testuser");
  await page.getByPlaceholder(/password/i).fill("testpassword");
  await page.getByRole("button", { name: /sign in/i }).click();
}

test.describe("Login error messages (issue #157)", () => {
  test("a locked-out login shows the server's message", async ({ page }) => {
    await page.route("**/api/auth/login", r =>
      r.fulfill({ status: 429, json: { detail: LOCKED } }));
    await submitLogin(page);
    await expect(page.getByText(LOCKED)).toBeVisible();
    await expect(page.getByText("Invalid credentials")).toHaveCount(0);
  });

  test("an nginx 429 page without JSON still says to wait", async ({ page }) => {
    await page.route("**/api/auth/login", r =>
      r.fulfill({ status: 429, contentType: "text/html", body: "<html>429</html>" }));
    await submitLogin(page);
    await expect(page.getByText("Too many attempts. Try again in 15 minutes.")).toBeVisible();
  });

  test("a wrong password still says Invalid credentials", async ({ page }) => {
    await page.route("**/api/auth/login", r =>
      r.fulfill({ status: 401, json: { detail: "Invalid credentials" } }));
    await submitLogin(page);
    await expect(page.getByText("Invalid credentials")).toBeVisible();
  });

  test("a locked-out 2FA step shows the server's message", async ({ page }) => {
    await page.route("**/api/auth/login", r =>
      r.fulfill({ json: { requires_2fa: true, pending_token: "pending" } }));
    await page.route("**/api/auth/verify-2fa", r =>
      r.fulfill({ status: 429, json: { detail: "Too many attempts. Try again in 15 minutes." } }));
    await submitLogin(page);
    await page.locator("input[maxlength='6']").fill("123456");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Too many attempts. Try again in 15 minutes.")).toBeVisible();
  });
});

test.describe("Token refresh failures (issue #168)", () => {
  const expired = fakeJwt("expired", 1000000000);

  async function openWithExpiredSession(page, refreshStatus) {
    const sentWithExpired = [];
    let refreshCalls = 0;
    await mockAllAPIs(page);
    await page.route("**/api/auth/refresh", r => {
      refreshCalls += 1;
      r.fulfill({ status: refreshStatus, json: { detail: "nope" } });
    });
    page.on("request", req => {
      if (req.headers()["authorization"] === `Bearer ${expired}`) sentWithExpired.push(req.url());
    });
    await page.addInitScript(token => {
      if (!sessionStorage.getItem("seeded")) {
        localStorage.setItem("vaultic_token", token);
        localStorage.setItem("vaultic_refresh_token", "refresh-raw");
        sessionStorage.setItem("seeded", "1");
      }
    }, expired);
    await page.goto("/");
    await expect.poll(() => refreshCalls).toBeGreaterThan(0);
    return { sentWithExpired, refreshCalls: () => refreshCalls };
  }

  for (const status of [429, 503]) {
    test(`a ${status} from refresh keeps the device signed in`, async ({ page }) => {
      const { sentWithExpired } = await openWithExpiredSession(page, status);
      await page.waitForTimeout(500);
      await expect(page.getByPlaceholder(/username/i)).toHaveCount(0);
      expect(await page.evaluate(() => localStorage.getItem("vaultic_refresh_token")))
        .toBe("refresh-raw");
      // The expired token is never sent, so no 401 can sign the device out.
      expect(sentWithExpired).toEqual([]);
    });
  }

  test("a later successful refresh carries on with the new token", async ({ page }) => {
    const fresh = fakeJwt("fresh", 4102444800);
    let fail = true;
    // Settings mounts Plaid Link; stub its CDN script (see settings-password.spec.js).
    await page.route("https://cdn.plaid.com/**", r =>
      r.fulfill({ contentType: "application/javascript", body: "" }));
    await mockAllAPIs(page);
    await page.route("**/api/auth/refresh", r => {
      if (fail) {
        r.fulfill({ status: 429, json: { detail: "slow down" } });
      } else {
        r.fulfill({ json: { token: fresh, refresh_token: "refresh-2" } });
      }
    });
    await page.addInitScript(token => {
      if (!sessionStorage.getItem("seeded")) {
        localStorage.setItem("vaultic_token", token);
        localStorage.setItem("vaultic_refresh_token", "refresh-raw");
        sessionStorage.setItem("seeded", "1");
      }
    }, expired);
    await page.goto("/");
    await page.waitForResponse(r => r.url().includes("/api/auth/refresh") && r.status() === 429);
    await expect(page.getByPlaceholder(/username/i)).toHaveCount(0);
    fail = false;
    const authed = page.waitForRequest(req => req.headers()["authorization"] === `Bearer ${fresh}`);
    await page.getByRole("link", { name: /settings/i }).click();
    await authed;
    expect(await page.evaluate(() => localStorage.getItem("vaultic_refresh_token"))).toBe("refresh-2");
    await expect(page.getByPlaceholder(/username/i)).toHaveCount(0);
  });

  test("a 401 from refresh still signs out", async ({ page }) => {
    await openWithExpiredSession(page, 401);
    await expect(page.getByPlaceholder(/username/i)).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("vaultic_refresh_token"))).toBeNull();
  });
});
