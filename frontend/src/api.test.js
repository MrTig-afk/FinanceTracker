/**
 * api.test.js — unit tests for the network layer (api.js).
 * fetch is mocked via vi.stubGlobal — no real backend, no real network.
 * All fixtures are synthetic, generated inline. No real transaction data.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  fetchSummary,
  fetchMonth,
  fetchYear,
  fetchStatus,
  postReclassify,
  fetchCategoryContext,
  saveCategoryContext,
  ApiError,
} from './api.js';

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
// fetchMonth / fetchYear — v2 Pass 1 period views
// ---------------------------------------------------------------------------

const CANNED_MONTH_VIEW = {
  period: 'month',
  ym: '2026-06',
  prev_ym: '2026-05',
  totals: { Groceries: '-50.00' },
  net: '-50.00',
  count: 1,
  comparison: [],
  available_months: ['2026-06', '2026-05'],
};

const CANNED_YEAR_VIEW = {
  period: 'year',
  y: '2026',
  prev_y: '2025',
  totals: { Groceries: '-500.00' },
  net: '-500.00',
  count: 10,
  comparison: [],
  available_years: ['2026', '2025'],
};

describe('fetchMonth', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_MONTH_VIEW));
    const result = await fetchMonth('2026-06');
    expect(result).toEqual(CANNED_MONTH_VIEW);
  });

  it('appends ?ym= query param when a ym string is supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_MONTH_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchMonth('2026-06');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('/month?ym=2026-06');
  });

  it('omits the query string entirely when ym is not supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_MONTH_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchMonth();
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toMatch(/\/month$/);
    expect(calledUrl).not.toContain('?');
  });

  it('sends a GET request with no body', async () => {
    const mockFetch = makeOkFetch(CANNED_MONTH_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchMonth('2026-06');
    const options = mockFetch.mock.calls[0][1];
    expect(options.body).toBeUndefined();
    expect(options.method === undefined || options.method === 'GET').toBe(true);
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(fetchMonth()).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError on a 400 (malformed ym)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await fetchMonth('bad-value').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchMonth().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('fetchYear', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_YEAR_VIEW));
    const result = await fetchYear('2026');
    expect(result).toEqual(CANNED_YEAR_VIEW);
  });

  it('appends ?y= query param when a y string is supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_YEAR_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchYear('2026');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('/year?y=2026');
  });

  it('omits the query string entirely when y is not supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_YEAR_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchYear();
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toMatch(/\/year$/);
    expect(calledUrl).not.toContain('?');
  });

  it('sends a GET request with no body', async () => {
    const mockFetch = makeOkFetch(CANNED_YEAR_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchYear('2026');
    const options = mockFetch.mock.calls[0][1];
    expect(options.body).toBeUndefined();
    expect(options.method === undefined || options.method === 'GET').toBe(true);
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(fetchYear()).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError on a 400 (malformed y)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await fetchYear('bad-value').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchYear().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
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
// fetchCategoryContext / saveCategoryContext (SYNTHETIC hints only)
// ---------------------------------------------------------------------------

const CANNED_CONTEXT = {
  categories: [
    { name: 'Groceries', color: '#57b26f', hints: 'SYNTH HINT A', position: 0 },
    { name: 'Utilities', color: '#4a90d9', hints: 'SYNTH HINT B', position: 1 },
  ],
};

describe('fetchCategoryContext', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_CONTEXT));
    const result = await fetchCategoryContext();
    expect(result).toEqual(CANNED_CONTEXT);
  });

  it('GETs the /category-context URL', async () => {
    const mockFetch = makeOkFetch(CANNED_CONTEXT);
    vi.stubGlobal('fetch', mockFetch);
    await fetchCategoryContext();
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/category-context');
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(fetchCategoryContext()).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchCategoryContext().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('saveCategoryContext', () => {
  it('resolves to the updated categories JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_CONTEXT));
    const result = await saveCategoryContext([{ name: 'Groceries', hints: 'SYNTH HINT A' }]);
    expect(result).toEqual(CANNED_CONTEXT);
  });

  it('PUTs to /category-context with JSON {categories}', async () => {
    const mockFetch = makeOkFetch(CANNED_CONTEXT);
    vi.stubGlobal('fetch', mockFetch);
    await saveCategoryContext([{ name: 'Groceries', hints: 'SYNTH HINT A' }]);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/category-context');
    expect(options.method).toBe('PUT');
    expect(options.headers).toMatchObject({ 'Content-Type': 'application/json' });
    const body = JSON.parse(options.body);
    expect(body).toEqual({ categories: [{ name: 'Groceries', hints: 'SYNTH HINT A' }] });
  });

  it('drops extraneous fields (color/position) from each item', async () => {
    const mockFetch = makeOkFetch(CANNED_CONTEXT);
    vi.stubGlobal('fetch', mockFetch);
    await saveCategoryContext([
      { name: 'Groceries', hints: 'SYNTH HINT A', color: '#57b26f', position: 0 },
    ]);
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.categories[0]).toEqual({ name: 'Groceries', hints: 'SYNTH HINT A' });
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(422));
    await expect(saveCategoryContext([{ name: 'Groceries', hints: 'x' }])).rejects.toBeInstanceOf(
      ApiError,
    );
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await saveCategoryContext([{ name: 'Groceries', hints: 'x' }]).catch((e) => e);
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
