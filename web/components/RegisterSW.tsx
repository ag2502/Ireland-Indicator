"use client";

import { useEffect } from "react";

/** Installs the service worker that keeps the app shell and last boards for offline use. */
export function RegisterSW() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }, []);
  return null;
}
