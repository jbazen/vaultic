/** Shared mock API setup for E2E tests */

export async function mockAllAPIs(page) {
  // ── Catch-all: intercept any /api/ request that isn't explicitly mocked ──
  // Returns an empty JSON response to prevent ECONNREFUSED crashes when
  // navigating to pages whose specific API endpoints aren't mocked below.
  await page.route("**/api/**", r => {
    r.fulfill({ json: {} });
  });

  // ── Auth ──
  await page.route("**/api/auth/login", r =>
    r.fulfill({ json: { token: "test-jwt-token" } }));
  await page.route("**/api/auth/me", r =>
    r.fulfill({ json: { username: "testuser", two_fa_enabled: 0 } }));
  await page.route("**/api/auth/users", r =>
    r.fulfill({ json: [{ username: "testuser" }] }));
  await page.route("**/api/auth/security-log*", r =>
    r.fulfill({ json: { lines: [] } }));

  // ── Net worth ──
  await page.route("**/api/net-worth/latest", r =>
    r.fulfill({ json: { total: 250000, liquid: 50000, invested: 150000, real_estate: 100000, vehicles: 20000, liabilities: 70000, crypto: 0, other_assets: 0, snapped_at: "2026-03-14" } }));
  await page.route("**/api/net-worth/history*", r =>
    r.fulfill({ json: [] }));

  // ── Accounts (sub-routes first, then base) ──
  await page.route("**/api/accounts/transactions/recent*", r =>
    r.fulfill({ json: [] }));
  await page.route("**/api/accounts/portfolio/performance*", r =>
    r.fulfill({ json: [] }));
  await page.route("**/api/accounts", r =>
    r.fulfill({ json: [{ id: 1, name: "Chase Checking", display_name: null, mask: "1234", type: "depository", subtype: "checking", institution_name: "Chase", current: 5000, available: 4900 }] }));

  // ── Other data endpoints ──
  await page.route("**/api/manual", r =>
    r.fulfill({ json: [{ id: 1, name: "Primary Home", category: "home_value", value: 450000, entered_at: "2026-03-01" }] }));
  await page.route("**/api/plaid/items", r =>
    r.fulfill({ json: [] }));
  await page.route("**/api/market/rates*", r =>
    r.fulfill({ json: { rates: [] } }));

  // ── Sage ──
  await page.route("**/api/sage/chat", r =>
    r.fulfill({ json: { response: "Hello! I'm Sage, your financial assistant.", history: [] } }));
  await page.route("**/api/sage/speak", r =>
    r.fulfill({ status: 200, contentType: "audio/mpeg", body: Buffer.from([]) }));
}

export async function loginMocked(page) {
  await mockAllAPIs(page);
  await page.goto("/");
  await page.getByPlaceholder(/username/i).fill("testuser");
  await page.getByPlaceholder(/password/i).fill("testpassword");
  await page.getByRole("button", { name: /sign in|login|log in/i }).click();
  // Wait for dashboard to appear
  await page.waitForURL(/\//);
  await page.waitForSelector("text=Net Worth", { timeout: 8000 });
}
