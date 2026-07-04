/**
 * swRouting.js — pure, SW-globals-free routing policy.
 * Unit-tested by swRouting.test.js (no service-worker environment needed).
 * public/sw.js mirrors the same policy inline (kept in sync manually).
 *
 * PRIVACY: API data paths MUST NEVER be cached — they carry the owner's
 * transaction data. Only the static app shell (HTML, JS, CSS, SVG marks,
 * manifest) is cached, so the app still renders while the laptop or the
 * Tailscale link is down.
 */

/**
 * API paths whose responses must never be cached.
 * Any request whose pathname starts with one of these → network-only.
 * Every data-bearing endpoint is listed explicitly so the never-cache
 * contract is deliberate, not an accident of the shell whitelist.
 */
export const API_PATHS = [
  '/upload',
  '/summary',
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
 * Determine the caching policy for a request.
 *
 * @param {string} url         Full URL string of the request.
 * @param {string} method      HTTP method ('GET', 'POST', …).
 * @param {string} [selfOrigin] The service worker's own origin. When provided,
 *                              any other origin is 'passthrough' before path
 *                              rules apply (sw.js always passes it; the pure
 *                              tests may omit it for path-only assertions).
 * @returns {'network-only' | 'shell-cache' | 'passthrough'}
 *
 * Policy:
 *  - Cross-origin (when selfOrigin known) → 'passthrough'
 *  - API data paths → 'network-only'  [ANY method]
 *  - GET requests whose pathname matches the static shell → 'shell-cache'
 *  - Everything else (non-GET non-API, unknown paths) → 'passthrough'
 */
export function routeRequest(url, method, selfOrigin) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return 'passthrough';
  }

  // Foreign origins are never our shell and never our API.
  if (selfOrigin && parsed.origin !== selfOrigin) {
    return 'passthrough';
  }

  const pathname = parsed.pathname;

  // API data paths — NEVER cache (owner's transaction data).
  for (const p of API_PATHS) {
    if (pathname === p || pathname.startsWith(p + '/')) {
      return 'network-only';
    }
  }

  // App-shell static assets — cache-first for GET only. .svg covers the app
  // icon plus the FinanceTracker/CommBank/Westpac marks, so logos still render
  // while the backend is unreachable.
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

  // Non-GET non-API requests and unrecognised paths.
  return 'passthrough';
}
