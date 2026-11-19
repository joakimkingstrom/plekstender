// Plekstender's service worker: keeps the app shell so the installed app opens without a
// connection (and opens faster). It never touches /api: data always comes from the server.
//
// - Pages: network first, falling back to the cached app shell when offline.
// - /assets/* (content-hashed by Vite): cache first; a new version has new file names.
// - Icons and the manifest: served from the cache, refreshed in the background.
const VERSION = 'v1'
const SHELL = `plekstender-shell-${VERSION}`
const ASSETS = `plekstender-assets-${VERSION}`
const PRECACHE = ['/', '/manifest.webmanifest', '/brand/favicon.svg', '/brand/icon-192.png']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((n) => n !== SHELL && n !== ASSETS).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Keep the latest shell (every route serves the same index.html).
          if (response.ok) {
            const copy = response.clone()
            void caches.open(SHELL).then((cache) => cache.put('/', copy))
          }
          return response
        })
        .catch(() => caches.match('/', { cacheName: SHELL }).then((cached) => cached ?? Response.error())),
    )
    return
  }

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.open(ASSETS).then((cache) =>
        cache.match(request).then(
          (cached) =>
            cached ??
            fetch(request).then((response) => {
              if (response.ok) void cache.put(request, response.clone())
              return response
            }),
        ),
      ),
    )
    return
  }

  if (url.pathname.startsWith('/brand/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith(
      caches.open(SHELL).then((cache) =>
        cache.match(request).then((cached) => {
          const fresh = fetch(request)
            .then((response) => {
              if (response.ok) void cache.put(request, response.clone())
              return response
            })
            .catch(() => cached ?? Response.error())
          return cached ?? fresh
        }),
      ),
    )
  }
})
