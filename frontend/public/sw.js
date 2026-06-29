/**
 * sw.js — minimal app-shell service worker.
 * Authored in public/ so Vite serves it at /sw.js without bundling.
 *
 * PRIVACY: /upload, /summary, and /status responses are NEVER cached —
 * they carry the owner's transaction data and must remain fresh/local.
 * Only the static app shell is cached for offline capability.
 *
 * The routing policy below MUST be kept in sync with src/swRouting.js
 * (which is the unit-tested pure copy). Both are ~10 lines of the same logic.
 */

'use strict';

const CACHE = 'financetracker-shell-v1';

// App-shell files to pre-cache on install.
// Hashed JS/CSS assets are added opportunistically by the fetch handler;
// do NOT hardcode Vite-generated hashed filenames here.
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icon.svg'];

// API paths whose responses must NEVER be cached.
const API_PATHS = ['/upload', '/summary', '/status'];

/**
 * Routing policy — inlined copy of src/swRouting.js routeRequest().
 * @param {string} url
 * @param {string} method
 * @returns {'network-only' | 'shell-cache' | 'passthrough'}
 */
function routeRequest(url, method) {
  let pathname;
  try {
    pathname = new URL(url).pathname;
  } catch {
    return 'passthrough';
  }

  for (const p of API_PATHS) {
    if (pathname === p || pathname.startsWith(p + '/')) {
      return 'network-only';
    }
  }

  if (method === 'GET') {
    if (
      pathname === '/' ||
      pathname === '/index.html' ||
      pathname === '/manifest.webmanifest' ||
      pathname === '/icon.svg' ||
      pathname.endsWith('.js') ||
      pathname.endsWith('.css')
    ) {
      return 'shell-cache';
    }
  }

  return 'passthrough';
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)),
        ),
      ),
  );
  self.clients.claim();
});

// ---------------------------------------------------------------------------
// Fetch interception
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (e) => {
  const policy = routeRequest(e.request.url, e.request.method);

  if (policy === 'network-only') {
    // Do NOT call e.respondWith — let the browser fetch normally.
    // This guarantees API responses are never served from cache.
    return;
  }

  if (policy === 'shell-cache') {
    e.respondWith(
      caches.match(e.request).then((cached) => {
        if (cached) return cached;

        return fetch(e.request)
          .then((res) => {
            // Cache a clone; return the original.
            const clone = res.clone();
            caches.open(CACHE).then((c) => c.put(e.request, clone));
            return res;
          })
          .catch(() => {
            // Offline fallback: serve the cached index for navigations.
            if (e.request.mode === 'navigate') {
              return caches.match('/index.html');
            }
            // For other assets, just let the failure propagate.
            return undefined;
          });
      }),
    );
    return;
  }

  // policy === 'passthrough' — default browser behaviour; no e.respondWith.
});
