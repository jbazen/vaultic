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

  test("an nginx 429 page without JSON says to wait a minute", async ({ page }) => {
    await page.route("**/api/auth/login", r =>
      r.fulfill({ status: 429, contentType: "text/html", body: "<html>429</html>" }));
    await submitLogin(page);
    await expect(page.getByText("Too many attempts. Wait a minute and try again.")).toBeVisible();
  });

  test("an unreachable server gets a plain message", async ({ page }) => {
    await page.route("**/api/auth/login", r => r.abort());
    await submitLogin(page);
    await expect(page.getByText("Can't reach the server. Check your connection and try again."))
      .toBeVisible();
  });

  test("an unreachable server at the 2FA step gets a plain message", async ({ page }) => {
    await page.route("**/api/auth/login", r =>
      r.fulfill({ json: { requires_2fa: true, pending_token: "pending" } }));
    await page.route("**/api/auth/verify-2fa", r => r.abort());
    await submitLogin(page);
    await page.locator("input[maxlength='6']").fill("123456");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Can't reach the server. Check your connection and try again."))
      .toBeVisible();
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

  // reply: a status code, or a function (route) => fulfil/abort it.
  async function openWithExpiredSession(page, reply) {
    const sentWithExpired = [];
    let refreshCalls = 0;
    await mockAllAPIs(page);
    await page.route("**/api/auth/refresh", async r => {
      refreshCalls += 1;
      if (typeof reply === "function") await reply(r);
      else await r.fulfill({ status: reply, json: { detail: "nope" } });
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

  const transient = {
    "a 429": 429,
    "a 503": 503,
    "a network error": r => r.abort(),
    "a 200 without tokens": r => r.fulfill({ json: {} }),
  };
  for (const [name, reply] of Object.entries(transient)) {
    test(`${name} from refresh keeps the device signed in`, async ({ page }) => {
      const { sentWithExpired } = await openWithExpiredSession(page, reply);
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

  test("a 401 for a token another tab already rotated keeps that tab's new tokens", async ({ page }) => {
    const otherTab = fakeJwt("other-tab", 4102444800);
    await openWithExpiredSession(page, async r => {
      // The other tab stores its new pair while this tab's request is in flight.
      await page.evaluate(token => {
        localStorage.setItem("vaultic_token", token);
        localStorage.setItem("vaultic_refresh_token", "refresh-other-tab");
      }, otherTab);
      await r.fulfill({ status: 401, json: { detail: "revoked" } });
    });
    await page.waitForTimeout(500);
    await expect(page.getByPlaceholder(/username/i)).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem("vaultic_refresh_token")))
      .toBe("refresh-other-tab");
  });
});

test.describe("Sign out (issue #168 review)", () => {
  test("sends the newest refresh token when a refresh is due", async ({ page }) => {
    const longLived = fakeJwt("long", 4102444800);
    let logoutBody = null;
    await mockAllAPIs(page);
    await page.route("**/api/auth/refresh", r =>
      r.fulfill({ json: { token: longLived, refresh_token: "refresh-2" } }));
    await page.route("**/api/auth/logout", r => {
      logoutBody = r.request().postDataJSON();
      r.fulfill({ json: { status: "logged_out" } });
    });
    await page.addInitScript(token => {
      if (!sessionStorage.getItem("seeded")) {
        localStorage.setItem("vaultic_token", token);
        localStorage.setItem("vaultic_refresh_token", "refresh-1");
        sessionStorage.setItem("seeded", "1");
      }
    }, longLived);
    await page.goto("/");
    await page.waitForSelector("text=Net Worth", { timeout: 8000 });
    // The access token is now within 2 minutes of expiry, so a refresh is due.
    const nearExpiry = fakeJwt("near", Math.floor(Date.now() / 1000) + 60);
    await page.evaluate(token => localStorage.setItem("vaultic_token", token), nearExpiry);

    await page.getByRole("button", { name: "Sign out" }).click();

    await expect(page.getByPlaceholder(/username/i)).toBeVisible();
    await expect.poll(() => logoutBody).toEqual({ refresh_token: "refresh-2" });
  });
});
