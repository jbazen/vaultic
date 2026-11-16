import { defineConfig, devices } from "@playwright/test";

// The tests run against the production bundle (`vite build`, served by
// `vite preview`), not the dev server, so they cover what actually ships (#147).
// Preview also has no dependency optimiser or HMR, so it can't reload a page in
// the middle of a test, the likely cause of the #162 logout flake. The API is
// mocked in every test (tests/e2e/helpers.js), so preview needs no /api proxy.
const PORT = 4173;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30000,
  expect: { timeout: 5000 },
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    // Kept only for failed tests, which CI uploads as an artifact (#162)
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    // Always build and serve fresh, so a run never tests a stale bundle
    reuseExistingServer: false,
    timeout: 120000,
  },
});
