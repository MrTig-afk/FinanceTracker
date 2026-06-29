/**
 * swRouting.test.js — unit tests for swRouting.js.
 * Privacy-critical: asserts that API paths (/upload, /summary, /status) are
 * ALWAYS 'network-only' and NEVER 'shell-cache'. No real service worker
 * environment — swRouting.js has no SW globals and is a pure function module.
 */

import { describe, it, expect } from 'vitest';
import { API_PATHS, routeRequest } from './swRouting.js';

// ---------------------------------------------------------------------------
// API_PATHS constant
// ---------------------------------------------------------------------------

describe('API_PATHS', () => {
  it('contains /upload', () => {
    expect(API_PATHS).toContain('/upload');
  });

  it('contains /summary', () => {
    expect(API_PATHS).toContain('/summary');
  });

  it('contains /status', () => {
    expect(API_PATHS).toContain('/status');
  });
});

// ---------------------------------------------------------------------------
// routeRequest — API data paths (MUST be network-only, NEVER cached)
// ---------------------------------------------------------------------------

describe('routeRequest — API paths always return network-only', () => {
  it('/upload POST → network-only', () => {
    expect(routeRequest('https://host/upload', 'POST')).toBe('network-only');
  });

  it('/upload GET → network-only', () => {
    // Even a GET to /upload must never be cached.
    expect(routeRequest('https://host/upload', 'GET')).toBe('network-only');
  });

  it('/summary GET → network-only', () => {
    expect(routeRequest('https://host/summary', 'GET')).toBe('network-only');
  });

  it('/summary with a query string → network-only', () => {
    expect(routeRequest('https://host/summary?month=2026-06', 'GET')).toBe('network-only');
  });

  it('/status GET → network-only', () => {
    expect(routeRequest('https://host/status', 'GET')).toBe('network-only');
  });

  it('/status POST → network-only', () => {
    expect(routeRequest('https://host/status', 'POST')).toBe('network-only');
  });
});

// ---------------------------------------------------------------------------
// PRIVACY CRITICAL: no API path must ever return 'shell-cache'
// ---------------------------------------------------------------------------

describe('privacy invariant: API paths never return shell-cache', () => {
  const methods = ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'];

  for (const path of ['/upload', '/summary', '/status']) {
    for (const method of methods) {
      it(`${method} ${path} is NOT shell-cache`, () => {
        const result = routeRequest(`https://host${path}`, method);
        expect(result).not.toBe('shell-cache');
      });
    }
  }

  it('none of the API_PATHS returns shell-cache for GET', () => {
    for (const p of API_PATHS) {
      expect(routeRequest(`https://host${p}`, 'GET')).not.toBe('shell-cache');
    }
  });
});

// ---------------------------------------------------------------------------
// routeRequest — static app-shell assets should be shell-cache (GET only)
// ---------------------------------------------------------------------------

describe('routeRequest — shell assets return shell-cache for GET', () => {
  it('/ GET → shell-cache', () => {
    expect(routeRequest('https://host/', 'GET')).toBe('shell-cache');
  });

  it('/index.html GET → shell-cache', () => {
    expect(routeRequest('https://host/index.html', 'GET')).toBe('shell-cache');
  });

  it('/manifest.webmanifest GET → shell-cache', () => {
    expect(routeRequest('https://host/manifest.webmanifest', 'GET')).toBe('shell-cache');
  });

  it('/icon.svg GET → shell-cache', () => {
    expect(routeRequest('https://host/icon.svg', 'GET')).toBe('shell-cache');
  });

  it('hashed JS bundle GET → shell-cache', () => {
    expect(routeRequest('https://host/app.12ab34cd.js', 'GET')).toBe('shell-cache');
  });

  it('CSS file GET → shell-cache', () => {
    expect(routeRequest('https://host/styles.a1b2c3.css', 'GET')).toBe('shell-cache');
  });
});

// ---------------------------------------------------------------------------
// routeRequest — passthrough cases
// ---------------------------------------------------------------------------

describe('routeRequest — passthrough', () => {
  it('a POST to a non-API path → passthrough', () => {
    // Non-GET, non-API → passthrough (not cached, not intercepted).
    expect(routeRequest('https://host/some-path', 'POST')).toBe('passthrough');
  });

  it('a cross-origin URL → passthrough', () => {
    expect(routeRequest('https://other.domain/resource', 'GET')).toBe('passthrough');
  });

  it('an invalid/empty URL → passthrough (no throw)', () => {
    expect(() => routeRequest('not-a-url', 'GET')).not.toThrow();
    expect(routeRequest('not-a-url', 'GET')).toBe('passthrough');
  });

  it('DELETE to a JS file → passthrough (not GET)', () => {
    expect(routeRequest('https://host/app.js', 'DELETE')).toBe('passthrough');
  });
});
