/** Shared mock API setup for E2E tests */

export async function mockAllAPIs(page) {
  await page.route("/api/auth/login", r =>
    r.fulfill({ json: { token: "test-jwt-token" } }));
  await page.route("/api/auth/me", r =>
    r.fulfill({ json: { username: "testuser", two_fa_enabled: 0 } }));
  await page.route("/api/net-worth/latest", r =>
    r.fulfill({ json: { total: 250000, liquid: 50000, invested: 150000, real_estate: 100000, vehicles: 20000, liabilities: 70000, crypto: 0, other_assets: 0, snapped_at: "2026-03-14" } }));
  await page.route("/api/net-worth/history*", r =>
    r.fulfill({ json: [] }));
  await page.route("/api/accounts", r =>
    r.fulfill({ json: [{ id: 1, name: "Chase Checking", display_name: null, mask: "1234", type: "depository", subtype: "checking", institution_name: "Chase", current: 5000, available: 4900 }] }));
  await page.route("/api/manual", r =>
    r.fulfill({ json: [{ id: 1, name: "Primary Home", category: "home_value", value: 450000, entered_at: "2026-03-01" }] }));
  await page.route("/api/accounts/transactions/recent*", r =>
    r.fulfill({ json: [] }));
  await page.route("/api/plaid/items", r =>
    r.fulfill({ json: [] }));
  await page.route("/api/accounts/portfolio-performance*", r =>
    r.fulfill({ json: [] }));
  await page.route("/api/market-rates*", r =>
    r.fulfill({ json: { rates: [] } }));
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
