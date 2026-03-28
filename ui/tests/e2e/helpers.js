/** Shared mock API setup for E2E tests */

export async function mockAllAPIs(page) {
  // Auth endpoints
  await page.route("**/api/auth/login", r =>
    r.fulfill({ json: { token: "test-jwt-token" } }));
  await page.route("**/api/auth/me", r =>
    r.fulfill({ json: { username: "testuser", two_fa_enabled: 0 } }));

  // Net worth
  await page.route("**/api/net-worth/latest", r =>
    r.fulfill({ json: { total: 250000, liquid: 50000, invested: 150000, real_estate: 100000, vehicles: 20000, liabilities: 70000, crypto: 0, other_assets: 0, snapped_at: "2026-03-14" } }));
  await page.route("**/api/net-worth/history*", r =>
    r.fulfill({ json: [] }));

  // Account sub-routes (must be registered BEFORE the base /api/accounts route)
  await page.route("**/api/accounts/transactions/recent*", r =>
    r.fulfill({ json: [] }));
  await page.route("**/api/accounts/portfolio/performance*", r =>
    r.fulfill({ json: [] }));

  // Base accounts route — registered after sub-routes so it doesn't shadow them
  await page.route("**/api/accounts", r =>
    r.fulfill({ json: [{ id: 1, name: "Chase Checking", display_name: null, mask: "1234", type: "depository", subtype: "checking", institution_name: "Chase", current: 5000, available: 4900 }] }));

  // Manual entries, Plaid items, market rates
  await page.route("**/api/manual", r =>
    r.fulfill({ json: [{ id: 1, name: "Primary Home", category: "home_value", value: 450000, entered_at: "2026-03-01" }] }));
  await page.route("**/api/plaid/items", r =>
    r.fulfill({ json: [] }));
  await page.route("**/api/market/rates*", r =>
    r.fulfill({ json: { rates: [] } }));

  // Sage endpoints — chat returns a text response, speak returns an empty audio blob
  await page.route("**/api/sage/chat", r =>
    r.fulfill({ json: { response: "Hello! I'm Sage, your financial assistant.", history: [] } }));
  await page.route("**/api/sage/speak", r =>
    r.fulfill({ status: 200, contentType: "audio/mpeg", body: Buffer.from([]) }));

  // Transactions page (navigated to from dashboard tests)
  await page.route("**/api/accounts/transactions*", r => {
    // Don't intercept "recent" sub-route (already handled above)
    if (r.request().url().includes("/recent")) return r.fallback();
    r.fulfill({ json: [] });
  });
  await page.route("**/api/budget/unassigned/*", r =>
    r.fulfill({ json: [] }));
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
