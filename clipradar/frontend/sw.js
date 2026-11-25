// Service worker: guarda la interfaz para que abra al instante y sin conexión.
// Los datos (API, vídeos) siempre se piden a la red: nunca se muestran datos viejos como si fueran actuales.
const VERSION = "cr-v1";
const SHELL = [
  "/", "/index.html", "/css/app.css", "/manifest.webmanifest", "/icons/icon.svg", "/icons/icon-192.png",
  "/js/app.js", "/js/api.js", "/js/ui.js",
  "/js/views/login.js", "/js/views/dashboard.js", "/js/views/monitor.js", "/js/views/clips.js",
  "/js/views/editor.js", "/js/views/publish.js", "/js/views/settings.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/")) return; // red siempre
  // Interfaz: red primero, caché si no hay conexión.
  e.respondWith(
    fetch(e.request).then((res) => {
      const copy = res.clone();
      if (res.ok) caches.open(VERSION).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("/index.html")))
  );
});
