// BusTrust service worker: the app opens with no signal, showing the last screens seen.
// Pages and live data go network-first (fresh beats cached); built assets cache-first.

const VERSION = "bt-v1";
const SHELL = ["/", "/nearby", "/alerts", "/routes", "/how", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function networkFirst(request) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(request);
    if (res.ok) cache.put(request, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(request, { ignoreSearch: request.mode === "navigate" });
    if (hit) return hit;
    if (request.mode === "navigate") return (await cache.match("/")) ?? Response.error();
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) cache.put(request, res.clone());
  return res;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // map tiles handle their own caching
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(request));
  } else if (request.mode === "navigate" || url.pathname.startsWith("/v1/")) {
    event.respondWith(networkFirst(request));
  }
});
