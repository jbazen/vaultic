import { test, expect } from "@playwright/test";
import { mockAllAPIs, loginOnly } from "./helpers.js";

// Two VAPID public keys (65-byte uncompressed P-256 points, base64url) that
// differ only in their last byte, like before and after a rotation (#59).
function vapidKey(lastByte) {
  const bytes = new Uint8Array(65).fill(7);
  bytes[0] = 4;
  bytes[64] = lastByte;
  return Buffer.from(bytes).toString("base64url");
}
const OLD_KEY = vapidKey(1);
const NEW_KEY = vapidKey(2);
const OLD_ENDPOINT = "https://push.example.test/old";
const NEW_ENDPOINT = "https://push.example.test/new";

/**
 * Replace the browser's service worker and PushManager with a fake that holds
 * one subscription made with `browserKey` (or none). Like real browsers,
 * subscribe() rejects a different key while a subscription exists.
 * `window.__push` records what the page did.
 */
async function fakePush(page, { browserKey, permission = "granted" }) {
  await page.addInitScript(({ browserKey, permission, oldEndpoint, newEndpoint }) => {
    const fromB64 = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));
    const state = { sub: null, subscribeCalls: 0, unsubscribed: [] };
    function makeSub(keyBytes, endpoint) {
      const sub = {
        endpoint,
        options: { applicationServerKey: keyBytes.slice().buffer },
        toJSON: () => ({ endpoint, keys: { p256dh: "p256dh-test", auth: "auth-test" } }),
        unsubscribe: async () => {
          if (state.sub === sub) state.sub = null;
          state.unsubscribed.push(endpoint);
          return true;
        },
      };
      return sub;
    }
    if (browserKey) state.sub = makeSub(fromB64(browserKey), oldEndpoint);
    const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
    const pushManager = {
      getSubscription: async () => state.sub,
      subscribe: async ({ applicationServerKey }) => {
        state.subscribeCalls += 1;
        const key = new Uint8Array(applicationServerKey);
        if (state.sub) {
          if (same(new Uint8Array(state.sub.options.applicationServerKey), key)) return state.sub;
          throw new DOMException("A subscription with a different applicationServerKey already exists.", "InvalidStateError");
        }
        state.sub = makeSub(key, newEndpoint);
        return state.sub;
      },
    };
    const reg = { pushManager };
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        ready: Promise.resolve(reg),
        getRegistration: async () => reg,
        register: async () => reg,
        addEventListener: () => {},
      },
    });
    window.PushManager = window.PushManager || function PushManager() {};
    Object.defineProperty(Notification, "permission", { configurable: true, get: () => permission });
    window.__push = {
      state,
      currentKey: () => (state.sub ? btoa(String.fromCharCode(...new Uint8Array(state.sub.options.applicationServerKey))) : null),
    };
  }, { browserKey, permission, oldEndpoint: OLD_ENDPOINT, newEndpoint: NEW_ENDPOINT });
}

/** Mock the push endpoints; returns the bodies the page POSTed. */
async function mockPushAPI(page, serverKey) {
  const calls = { subscribe: [], unsubscribe: [] };
  await page.route("**/api/push/vapid-public-key", r => r.fulfill({ json: { publicKey: serverKey } }));
  await page.route("**/api/push/subscribe", r => {
    calls.subscribe.push(r.request().postDataJSON());
    r.fulfill({ json: { status: "subscribed" } });
  });
  await page.route("**/api/push/unsubscribe", r => {
    calls.unsubscribe.push(r.request().postDataJSON());
    r.fulfill({ json: { status: "unsubscribed" } });
  });
  return calls;
}

const b64 = key => Buffer.from(key, "base64url").toString("base64");

test.describe("Push survives a VAPID key rotation (issue #59)", () => {
  // Settings mounts Plaid Link; its real CDN script throws a cross-origin
  // "Script error." in the test browser that replaces the page.
  test.beforeEach(async ({ page }) => {
    await page.route("https://cdn.plaid.com/**", r =>
      r.fulfill({ contentType: "application/javascript", body: "" }));
  });

  test("sign-in replaces a subscription made with the old key", async ({ page }) => {
    await fakePush(page, { browserKey: OLD_KEY });
    await mockAllAPIs(page);
    const calls = await mockPushAPI(page, NEW_KEY);
    await loginOnly(page);

    await expect.poll(() => calls.subscribe.length).toBe(1);
    expect(calls.unsubscribe).toEqual([{ endpoint: OLD_ENDPOINT }]);
    expect(calls.subscribe[0]).toEqual({ endpoint: NEW_ENDPOINT, p256dh: "p256dh-test", auth: "auth-test" });
    expect(await page.evaluate(() => window.__push.currentKey())).toBe(b64(NEW_KEY));

    await page.getByRole("link", { name: /settings/i }).click();
    await expect(page.getByText("Enabled on this device")).toBeVisible();
  });

  test("sign-in leaves a subscription with the current key alone", async ({ page }) => {
    await fakePush(page, { browserKey: NEW_KEY });
    await mockAllAPIs(page);
    const calls = await mockPushAPI(page, NEW_KEY);
    await loginOnly(page);

    await page.getByRole("link", { name: /settings/i }).click();
    await expect(page.getByText("Enabled on this device")).toBeVisible();
    expect(calls).toEqual({ subscribe: [], unsubscribe: [] });
    expect(await page.evaluate(() => window.__push.state.subscribeCalls)).toBe(0);
  });

  test("without permission the old subscription shows as off and Enable replaces it", async ({ page }) => {
    await fakePush(page, { browserKey: OLD_KEY, permission: "default" });
    await mockAllAPIs(page);
    const calls = await mockPushAPI(page, NEW_KEY);
    await loginOnly(page);

    await page.getByRole("link", { name: /settings/i }).click();
    await expect(page.getByText("Disabled on this device")).toBeVisible();
    expect(calls.subscribe).toEqual([]);

    await page.getByRole("button", { name: "Enable Notifications" }).click();
    await expect(page.getByText("Notifications enabled on this device.")).toBeVisible();
    expect(calls.unsubscribe).toEqual([{ endpoint: OLD_ENDPOINT }]);
    expect(calls.subscribe.map(b => b.endpoint)).toEqual([NEW_ENDPOINT]);
    expect(await page.evaluate(() => window.__push.currentKey())).toBe(b64(NEW_KEY));
  });

  test("no subscription: sign-in does not subscribe", async ({ page }) => {
    await fakePush(page, { browserKey: null });
    await mockAllAPIs(page);
    const calls = await mockPushAPI(page, NEW_KEY);
    await loginOnly(page);

    await page.getByRole("link", { name: /settings/i }).click();
    await expect(page.getByText("Disabled on this device")).toBeVisible();
    expect(calls).toEqual({ subscribe: [], unsubscribe: [] });
  });
});
