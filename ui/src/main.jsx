import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import "./index.css";

// Listen for navigation messages from the service worker.
// The notificationclick handler sends {type:"NAVIGATE", url:"/review"} via
// postMessage because client.navigate() is not supported on iOS Safari PWAs.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data?.type === "NAVIGATE" && event.data?.url) {
      window.location.href = event.data.url;
    }
  });
}

// ── Service Worker registration ───────────────────────────────────────────────
// Register sw.js for Web Push support. The SW handles incoming push events
// and shows OS notifications. It does NOT cache any assets (no offline mode).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .catch((err) => console.warn("SW registration failed:", err));
  });
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
