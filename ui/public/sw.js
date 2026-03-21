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

  const targetUrl = event.notification.data?.url || "/budget";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        // Focus an existing Vaultic tab if one is open
        for (const client of clients) {
          const clientPath = new URL(client.url).pathname;
          if (clientPath.startsWith("/") && "focus" in client) {
            client.navigate(targetUrl);
            return client.focus();
          }
        }
        // No existing tab — open a new one
        return self.clients.openWindow(targetUrl);
      })
  );
});
