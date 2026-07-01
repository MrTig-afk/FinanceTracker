/**
 * api.test.js — unit tests for the network layer (api.js).
 * fetch is mocked via vi.stubGlobal — no real backend, no real network.
 * All fixtures are synthetic, generated inline. No real transaction data.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { fetchSummary, fetchStatus, postReclassify, ApiError } from './api.js';

// ---------------------------------------------------------------------------
// Synthetic canned response — no real amounts, no real categories.
// ---------------------------------------------------------------------------

const CANNED_SUMMARY = {
  year_month: '2026-06',
  totals: { Groceries: '-50.00', Income: '1000.00' },
  net: '950.00',
  count: 2,
};

// ---------------------------------------------------------------------------
// Helpers for building mock fetch responses.
// ---------------------------------------------------------------------------

function makeOkFetch(body = CANNED_SUMMARY) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

function makeErrorFetch(status = 500) {
  return vi.fn().mockResolvedValue({ ok: false, status });
}

function makeNetworkFailFetch(message = 'Failed to fetch') {
  return vi.fn().mockRejectedValue(new TypeError(message));
}

// ---------------------------------------------------------------------------
// Cleanup — restore globals after every test.
// ---------------------------------------------------------------------------

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// fetchSummary
// ---------------------------------------------------------------------------

describe('fetchSummary', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch());
    const result = await fetchSummary('2026-06');
    expect(result).toEqual(CANNED_SUMMARY);
  });

  it('appends ?month= query param when a month string is supplied', async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal('fetch', mockFetch);
    await fetchSummary('2026-06');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('/summary?month=2026-06');
  });

  it('omits the query string entirely when no month is supplied', async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal('fetch', mockFetch);
    await fetchSummary();
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toMatch(/\/summary$/);
    expect(calledUrl).not.toContain('?');
  });

  it('sends an Accept: application/json header', async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal('fetch', mockFetch);
    await fetchSummary();
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(fetchSummary()).rejects.toBeInstanceOf(ApiError);
  });

  it('ApiError carries the HTTP status code from the response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(404));
    const err = await fetchSummary().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('rejects with ApiError (not raw TypeError) on network-level failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchSummary().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });

  it('does not propagate the raw TypeError as the rejection value', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchSummary().catch((e) => e);
    // Must be ApiError, not the underlying TypeError
    expect(err.constructor.name).toBe('ApiError');
  });

  it('rejects with ApiError on a 403 Forbidden', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(403));
    const err = await fetchSummary().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
  });
});

// ---------------------------------------------------------------------------
// postReclassify — apply/revert the small-fuel-stop rule
// ---------------------------------------------------------------------------

describe('postReclassify', () => {
  it('resolves to the updated summary JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch());
    const result = await postReclassify(true, '2026-06');
    expect(result).toEqual(CANNED_SUMMARY);
  });

  it('POSTs to /reclassify with enabled and month query params', async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal('fetch', mockFetch);
    await postReclassify(true, '2026-06');
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/reclassify?');
    expect(url).toContain('enabled=true');
    expect(url).toContain('month=2026-06');
    expect(options.method).toBe('POST');
  });

  it('serialises enabled=false and omits month when not supplied', async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal('fetch', mockFetch);
    await postReclassify(false);
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('enabled=false');
    expect(url).not.toContain('month=');
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(postReclassify(true)).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await postReclassify(true).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// ApiError — class invariants
// ---------------------------------------------------------------------------

describe('ApiError', () => {
  it('has name === "ApiError"', () => {
    const e = new ApiError('test');
    expect(e.name).toBe('ApiError');
  });

  it('stores the status option', () => {
    const e = new ApiError('test', { status: 503 });
    expect(e.status).toBe(503);
  });

  it('is an instance of Error', () => {
    expect(new ApiError('test')).toBeInstanceOf(Error);
  });

  it('defaults status to null when not provided', () => {
    const e = new ApiError('test');
    expect(e.status).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// fetchStatus — best-effort; never throws.
// ---------------------------------------------------------------------------

describe('fetchStatus', () => {
  it('resolves null on a network-level rejection — does not throw', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch('offline'));
    await expect(fetchStatus()).resolves.toBeNull();
  });

  it('resolves null on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(503));
    await expect(fetchStatus()).resolves.toBeNull();
  });

  it('resolves parsed JSON on a 200 response', async () => {
    const STATUS = {
      ok: true,
      configured: { drive: false, openrouter: true },
      last_run: null,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => STATUS,
    }));
    const result = await fetchStatus();
    expect(result).toEqual(STATUS);
  });

  it('includes /status in the request URL', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    vi.stubGlobal('fetch', mockFetch);
    await fetchStatus();
    expect(mockFetch.mock.calls[0][0]).toContain('/status');
  });
});
