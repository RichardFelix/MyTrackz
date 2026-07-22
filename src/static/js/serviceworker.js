const CACHE_NAME = 'mytrackz-pwa-v2';
const LEGACY_CACHE_NAMES = new Set(['yamtrack-v1']);
const appScope = new URL(self.registration.scope);
const scopedUrl = (path) => new URL(path, appScope).toString();
const urlsToCache = [
  scopedUrl('offline/'),
  scopedUrl('static/favicon/android-chrome-192x192.png'),
  scopedUrl('static/favicon/android-chrome-512x512.png'),
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(urlsToCache)),
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') {
    return;
  }

  const requestUrl = new URL(event.request.url);
  if (requestUrl.origin !== appScope.origin) {
    return;
  }

  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(scopedUrl('offline/'))),
    );
    return;
  }

  if (urlsToCache.includes(requestUrl.toString())) {
    event.respondWith(
      caches.match(event.request).then((response) => response || fetch(event.request)),
    );
  }
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => Promise.all(
      cacheNames.map((cacheName) => {
        if (
          (cacheName.startsWith('mytrackz-pwa-') && cacheName !== CACHE_NAME)
          || LEGACY_CACHE_NAMES.has(cacheName)
        ) {
          return caches.delete(cacheName);
        }
        return undefined;
      }),
    )).then(() => self.clients.claim()),
  );
});

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
