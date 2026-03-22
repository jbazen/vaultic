/**
 * Vaultic Service Worker
 *
 * Handles two responsibilities:
 *   1. Web Push — receives encrypted push events from the server and shows
 *      OS-level notifications via self.registration.showNotification().
 *   2. Notification click — focuses or opens the Vaultic tab when the user
 *      taps a notification, navigating to the URL embedded in the payload.
 *
 * The service worker is registered in main.jsx at app startup. Push
 * subscriptions are managed in Settings → Notifications.
 */

self.addEventListener("install", () => {
  // Skip the "waiting" phase so a new SW version activates immediately
  // instead of waiting for all tabs to close.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Take control of any open clients (tabs) immediately on activation
  // so the new SW handles fetch/push events right away.
  event.waitUntil(self.clients.claim());
});

/**
 * Push event — fired when the server sends a Web Push message.
 *
 * The server encrypts a JSON payload with:
 *   { title: string, body: string, url: string }
 *
 * We decode it and show a system notification. The notification stays
 * visible until the user interacts with it (requireInteraction: true)
 * so it doesn't vanish silently on mobile.
 */
self.addEventListener("push", (event) => {
  let title = "Vaultic";
  let body  = "You have transactions to review.";
  let url   = "/review";  // opens the mobile review queue

  if (event.data) {
    try {
      const data = event.data.json();
      title = data.title || title;
      body  = data.body  || body;
      url   = data.url   || url;
    } catch {
      body = event.data.text() || body;
    }
  }

  const options = {
    body,
    icon:             "/favicon.png",
    badge:            "/favicon.png",
    requireInteraction: true,           // keep visible until user taps
    data:             { url },          // passed through to notificationclick
    vibrate:          [200, 100, 200],  // buzz pattern on mobile
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

/**
 * Notification click — called when the user taps the OS notification.
 *
 * Tries to focus an already-open Vaultic tab; if none exists, opens a new one.
 * The URL comes from the notification's data.url field set during push.
 */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetUrl = event.notification.data?.url || "/review";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        if (clients.length > 0) {
          // PWA is already open — tell it to navigate via postMessage, then focus.
          // We use postMessage instead of client.navigate() because navigate() is
          // NOT supported on iOS Safari PWAs and silently fails, leaving the app
          // at whatever URL it was at (often causing a blank screen).
          const client = clients[0];
          client.postMessage({ type: "NAVIGATE", url: targetUrl });
          return client.focus();
        }
        // PWA is not open — open it at the target URL directly.
        return self.clients.openWindow(targetUrl);
      })
  );
});
