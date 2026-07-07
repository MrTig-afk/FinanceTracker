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
 * The push-notification constants/handlers below MUST be kept in sync with
 * src/swPush.js (v2 Pass 3) — same convention.
 */

'use strict';

const CACHE = 'financetracker-shell-v4';

// App-shell files to pre-cache on install. The SVG marks are included so the
// FinanceTracker/bank logos still render while the backend is unreachable.
// Hashed JS/CSS assets are added opportunistically by the fetch handler;
// do NOT hardcode Vite-generated hashed filenames here.
const SHELL = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/icon.svg',
  '/finance-tracker-mark.svg',
  '/finance-tracker-app-icon.svg',
  '/commbank-mark.svg',
  '/westpac-mark.svg',
];

// API paths whose responses must NEVER be cached (kept in sync with
// src/swRouting.js — every data-bearing endpoint listed explicitly).
const API_PATHS = [
  '/upload',
  '/summary',
  '/health',
  '/status',
  '/month',
  '/year',
  '/trends',
  '/search',
  '/transfers',
  '/budgets',
  '/balances',
  '/categoriser',
  '/category-transactions',
  '/category-override',
  '/category-context',
  '/subscriptions',
  '/corrections',
  '/settings',
  '/reclassify',
  '/reset',
  '/export',
  '/push',
  '/notify',
];

/**
 * Routing policy — inlined copy of src/swRouting.js routeRequest().
 * @param {string} url
 * @param {string} method
 * @param {string} [selfOrigin]
 * @returns {'network-only' | 'shell-cache' | 'passthrough'}
 */
function routeRequest(url, method, selfOrigin) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return 'passthrough';
  }

  if (selfOrigin && parsed.origin !== selfOrigin) {
    return 'passthrough';
  }

  const pathname = parsed.pathname;

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
      pathname.endsWith('.svg') ||
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
          keys
            // Prune old shell generations, but keep the health-check state
            // cache (see the periodic health check section below) across
            // shell version bumps.
            .filter((k) => k !== CACHE && k !== HEALTH_STATE_CACHE)
            .map((k) => caches.delete(k)),
        ),
      ),
  );
  self.clients.claim();
});

// ---------------------------------------------------------------------------
// Fetch interception
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (e) => {
  const policy = routeRequest(e.request.url, e.request.method, self.location.origin);

  if (policy === 'network-only') {
    // Do NOT call e.respondWith — let the browser fetch normally.
    // This guarantees API responses are never served from cache.
    return;
  }

  if (policy === 'shell-cache') {
    // Navigations (index.html) are network-first so a new deploy is picked up on
    // the next load: fetch fresh HTML (and thus fresh hashed asset refs), refresh
    // the cache, and fall back to cache when offline OR when the network hangs.
    //
    // The deadline matters: with the Tailscale route up but the laptop off, a
    // fetch does not fail — it hangs until the OS timeout (60s+ on iOS), which
    // reads as a blank screen. Abort after a few seconds and serve the cached
    // shell instead; the next reachable visit refreshes the cache as usual.
    if (e.request.mode === 'navigate') {
      // 1.5s: aggressive by design — the cached shell is almost always the
      // current build anyway, so a slow network just means cache-first with a
      // one-visit delay on picking up a new deploy.
      const NAV_TIMEOUT_MS = 1500;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), NAV_TIMEOUT_MS);
      e.respondWith(
        fetch(e.request, { signal: controller.signal })
          .then((res) => {
            clearTimeout(timer);
            const clone = res.clone();
            caches.open(CACHE).then((c) => c.put(e.request, clone));
            return res;
          })
          .catch(() => {
            clearTimeout(timer);
            return caches
              .match(e.request)
              .then((cached) => cached || caches.match('/index.html'));
          }),
      );
      return;
    }

    // Hashed assets (.js/.css/.svg) are cache-first — safe because the filename
    // hash changes each build, so a new build is a new URL (no staleness). The
    // network fill gets the same short deadline as navigations so an unreachable
    // laptop can never hang an asset load for the OS timeout.
    e.respondWith(
      caches.match(e.request).then((cached) => {
        if (cached) return cached;

        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 5000);
        return fetch(e.request, { signal: controller.signal })
          .then((res) => {
            clearTimeout(timer);
            // Cache a clone; return the original.
            const clone = res.clone();
            caches.open(CACHE).then((c) => c.put(e.request, clone));
            return res;
          })
          .catch(() => {
            clearTimeout(timer);
            // No cache and no network — let the failure propagate.
            return undefined;
          });
      }),
    );
    return;
  }

  // policy === 'passthrough' — default browser behaviour; no e.respondWith.
});

// ---------------------------------------------------------------------------
// Web Push. Independent of routing/caching above.
// Focus-aware routing: if a window client is FOCUSED/visible, relay the payload
// to it via postMessage (the page shows an in-app toast) and DO NOT raise an OS
// notification; otherwise show an OS notification. This logic mirrors the pure,
// unit-tested copy in src/swPush.js (routePush / normalizePushPayload / etc),
// kept in sync manually — same convention as routeRequest above.
//
// PRIVACY: server-sent title/body is COUNTS/STATUS-ONLY copy (guaranteed by the
// backend notifier — never amounts, balances, descriptions, categories, or
// accounts). Missing/malformed fields fall back to the fixed generic strings.
// ---------------------------------------------------------------------------
const PUSH_TITLE = 'FinanceTracker';
const PUSH_BODY = 'Your statement was processed';
const PUSH_MESSAGE_SOURCE = 'financetracker-push';

function normalizePushPayload(raw) {
  const obj = raw && typeof raw === 'object' ? raw : {};
  const type = typeof obj.type === 'string' && obj.type ? obj.type : 'generic';
  const title =
    typeof obj.title === 'string' && obj.title.trim() ? obj.title : PUSH_TITLE;
  const body = typeof obj.body === 'string' && obj.body ? obj.body : PUSH_BODY;
  return { type, title, body };
}

function isClientFocused(client) {
  if (!client) return false;
  return client.focused === true || client.visibilityState === 'visible';
}

self.addEventListener('push', (event) => {
  let raw = null;
  if (event.data) {
    try {
      raw = event.data.json();
    } catch {
      try {
        raw = { body: event.data.text() };
      } catch {
        raw = null;
      }
    }
  }
  const p = normalizePushPayload(raw);

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        const focused = clientList.filter(isClientFocused);
        if (focused.length > 0) {
          // App is in the foreground: hand off to the page for an in-app toast.
          const message = {
            source: PUSH_MESSAGE_SOURCE,
            type: p.type,
            title: p.title,
            body: p.body,
          };
          for (const client of focused) client.postMessage(message);
          return undefined;
        }
        // Backgrounded: raise an OS notification.
        return self.registration.showNotification(p.title, {
          body: p.body,
          icon: '/icon.svg',
          badge: '/icon.svg',
          tag: `financetracker-${p.type}`,
          data: { type: p.type, title: p.title, body: p.body },
        });
      }),
  );
});

// ---------------------------------------------------------------------------
// Periodic backend health check ("is the laptop up?"). Independent of the
// routing/caching and push handlers above. Mirrors the pure, unit-tested
// constants/helpers in src/swHealth.js — kept in sync manually, same
// convention as routeRequest/swPush above.
//
// A laptop that is off cannot send a Web Push, so this alert is DEVICE-LOCAL:
// the browser wakes this SW on its own schedule (periodicSync, Chromium
// installed-PWA only), the SW probes GET /health with a short deadline, and on
// failure raises a fixed status-only notification — at most one per 24h,
// reset by the first successful probe. No data is read or sent.
// ---------------------------------------------------------------------------
const HEALTH_TAG = 'financetracker-health';
const HEALTH_PATH = '/health';
const HEALTH_TIMEOUT_MS = 5000;
const HEALTH_ALERT_MIN_INTERVAL_MS = 24 * 60 * 60 * 1000;
// State lives in its own Cache (a SW has no localStorage): one synthetic
// entry whose body is {"lastAlertTs": <ms>}. activate() deliberately spares
// this cache when pruning old shell generations, so a shell version bump
// never resets the alert dedupe.
const HEALTH_STATE_CACHE = 'financetracker-health-state';
const HEALTH_STATE_KEY = '/__health-state';

function healthShouldAlert(lastAlertTs, now) {
  const last = typeof lastAlertTs === 'number' && Number.isFinite(lastAlertTs)
    ? lastAlertTs
    : null;
  if (last === null) return true;
  return now - last >= HEALTH_ALERT_MIN_INTERVAL_MS;
}

function readHealthState() {
  return caches
    .open(HEALTH_STATE_CACHE)
    .then((c) => c.match(HEALTH_STATE_KEY))
    .then((res) => (res ? res.text() : null))
    .then((text) => {
      if (!text) return null;
      try {
        const data = JSON.parse(text);
        const ts = data && typeof data === 'object' ? data.lastAlertTs : null;
        return typeof ts === 'number' && Number.isFinite(ts) ? ts : null;
      } catch {
        return null;
      }
    })
    .catch(() => null);
}

function writeHealthState(ts) {
  return caches
    .open(HEALTH_STATE_CACHE)
    .then((c) =>
      c.put(HEALTH_STATE_KEY, new Response(JSON.stringify({ lastAlertTs: ts }))),
    )
    .catch(() => undefined);
}

function clearHealthState() {
  return caches
    .open(HEALTH_STATE_CACHE)
    .then((c) => c.delete(HEALTH_STATE_KEY))
    .catch(() => undefined);
}

function checkHealth() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
  return fetch(HEALTH_PATH, { signal: controller.signal, cache: 'no-store' })
    .then((res) => {
      clearTimeout(timer);
      if (res && res.ok) return clearHealthState();
      return handleHealthFailure();
    })
    .catch(() => {
      clearTimeout(timer);
      return handleHealthFailure();
    });
}

function handleHealthFailure() {
  return readHealthState().then((lastAlertTs) => {
    if (!healthShouldAlert(lastAlertTs, Date.now())) return undefined;
    return Promise.resolve()
      .then(() =>
        self.registration.showNotification("Can't reach the laptop", {
          body:
            "FinanceTracker couldn't reach your laptop - data in the app may be stale.",
          icon: '/icon.svg',
          badge: '/icon.svg',
          tag: HEALTH_TAG,
        }),
      )
      .catch(() => undefined) // notification permission may be absent
      .then(() => writeHealthState(Date.now()));
  });
}

self.addEventListener('periodicsync', (event) => {
  if (event.tag === HEALTH_TAG) {
    event.waitUntil(checkHealth());
  }
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if ('focus' in client) return client.focus();
        }
        if (self.clients.openWindow) return self.clients.openWindow('/');
        return undefined;
      }),
  );
});
