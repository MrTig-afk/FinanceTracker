/**
 * swRouting.js — pure, SW-globals-free routing policy.
 * Unit-tested by swRouting.test.js (no service-worker environment needed).
 * public/sw.js mirrors the same policy inline (kept in sync manually).
 *
 * PRIVACY: API paths (/upload, /summary, /status) MUST NEVER be cached —
 * they carry the owner's transaction data. Only the static app shell is cached.
 */

/**
 * API paths whose responses must never be cached.
 * Any request whose pathname starts with one of these → network-only.
 */
export const API_PATHS = ['/upload', '/summary', '/status'];

/**
 * Determine the caching policy for a request.
 *
 * @param {string} url     Full URL string of the request.
 * @param {string} method  HTTP method ('GET', 'POST', …).
 * @returns {'network-only' | 'shell-cache' | 'passthrough'}
 *
 * Policy:
 *  - API data paths (/upload, /summary, /status) → 'network-only'  [ANY method]
 *  - GET requests whose pathname matches the static shell → 'shell-cache'
 *  - Everything else (cross-origin, non-GET non-API, unknown paths) → 'passthrough'
 */
export function routeRequest(url, method) {
  let pathname;
  try {
    pathname = new URL(url).pathname;
  } catch {
    return 'passthrough';
  }

  // API data paths — NEVER cache (owner's transaction data).
  for (const p of API_PATHS) {
    if (pathname === p || pathname.startsWith(p + '/')) {
      return 'network-only';
    }
  }

  // App-shell static assets — cache-first for GET only.
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

  // Cross-origin requests, non-GET non-API requests, and unrecognised paths.
  return 'passthrough';
}
