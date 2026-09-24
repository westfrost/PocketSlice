// PocketSlice service worker: caches the app shell so the PWA opens instantly.
// API calls are never cached.
const CACHE = 'pocketslice-v1';
const SHELL = [
  '/', '/css/app.css', '/js/app.js', '/js/api.js', '/js/viewer.js',
  '/js/views/slice.js', '/js/views/printer.js', '/js/views/files.js', '/js/views/settings.js',
  '/vendor/three/three.module.js', '/vendor/three/STLLoader.js', '/vendor/three/OrbitControls.js',
  '/icons/favicon.png', '/icons/icon-192.png', '/manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // network first, fall back to cache (keeps the shell fresh but usable offline)
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request, { ignoreSearch: url.pathname === '/' })),
  );
});
