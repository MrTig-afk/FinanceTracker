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
  fetchTrends,
  fetchBalances,
  fetchStatus,
  postReclassify,
  fetchCategoryContext,
  saveCategoryContext,
  fetchCategoryTransactions,
  fetchSearch,
  fetchTransfers,
  postTransferUntag,
  postTransfersSeen,
  postCategoryOverride,
  postPushSubscribe,
  postPushUnsubscribe,
  getSettings,
  putSettings,
  getBudgets,
  putBudgets,
  getScorecard,
  getSubscriptions,
  getCorrections,
  deleteCorrection,
  getCategoriserStatus,
  postCategoriserTest,
  postCategoriserRetry,
  postReset,
  transactionsCsvUrl,
  API_BASE,
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
// fetchCategoryTransactions — v2 category drill-down
// ---------------------------------------------------------------------------

const CANNED_DRILLDOWN = {
  category: 'Subscriptions',
  month: '2026-06',
  total: '-170.01',
  count: 1,
  transactions: [
    { date: '2026-06-12', description: 'SYNTH SUB', amount: '-170.01', bank: 'commbank' },
  ],
};

describe('fetchCategoryTransactions', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_DRILLDOWN));
    const result = await fetchCategoryTransactions('Subscriptions', '2026-06');
    expect(result).toEqual(CANNED_DRILLDOWN);
  });

  it('encodes the category and month as query params', async () => {
    const mockFetch = makeOkFetch(CANNED_DRILLDOWN);
    vi.stubGlobal('fetch', mockFetch);
    await fetchCategoryTransactions('Dining Out', '2026-06');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('/category-transactions?');
    expect(calledUrl).toContain('category=Dining+Out');
    expect(calledUrl).toContain('month=2026-06');
  });

  it('omits the month param when not supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_DRILLDOWN);
    vi.stubGlobal('fetch', mockFetch);
    await fetchCategoryTransactions('Subscriptions');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('category=Subscriptions');
    expect(calledUrl).not.toContain('month=');
  });

  it('rejects with ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await fetchCategoryTransactions('Bananas').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchCategoryTransactions('Subscriptions').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// fetchSearch — v6 local full-text transaction search
// ---------------------------------------------------------------------------

const CANNED_SEARCH = {
  query: 'coffee',
  month: '2026-06',
  total: '-16.75',
  count: 1,
  transactions: [
    { id: 3, date: '2026-06-12', description: 'SYNTH COFFEE', amount: '-16.75', bank: 'commbank', category: 'Dining Out' },
  ],
};

describe('fetchSearch', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_SEARCH));
    const result = await fetchSearch('coffee', '2026-06');
    expect(result).toEqual(CANNED_SEARCH);
  });

  it('encodes q and month as query params on /search', async () => {
    const mockFetch = makeOkFetch(CANNED_SEARCH);
    vi.stubGlobal('fetch', mockFetch);
    await fetchSearch('coffee', '2026-06');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('/search?');
    expect(calledUrl).toContain('q=coffee');
    expect(calledUrl).toContain('month=2026-06');
  });

  it('omits the month param when not supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_SEARCH);
    vi.stubGlobal('fetch', mockFetch);
    await fetchSearch('coffee');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('q=coffee');
    expect(calledUrl).not.toContain('month=');
  });

  it('URL-encodes special characters in the query', async () => {
    const mockFetch = makeOkFetch(CANNED_SEARCH);
    vi.stubGlobal('fetch', mockFetch);
    await fetchSearch('a b&c');
    const calledUrl = mockFetch.mock.calls[0][0];
    // URLSearchParams encodes the space and ampersand — never breaks the URL.
    expect(calledUrl).toContain('q=a+b%26c');
  });

  it('sends an Accept: application/json header', async () => {
    const mockFetch = makeOkFetch(CANNED_SEARCH);
    vi.stubGlobal('fetch', mockFetch);
    await fetchSearch('coffee');
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
  });

  it('rejects with ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await fetchSearch('x', '2026/06').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchSearch('coffee').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });
});

// ---------------------------------------------------------------------------
// fetchTransfers / postTransferUntag — v6 internal transfer netting
// ---------------------------------------------------------------------------

const CANNED_TRANSFERS = {
  count: 1,
  pairs: [
    {
      id: 7,
      amount: '500.00',
      created_at: '2026-06-02T00:00:00Z',
      out: { id: 1, date: '2026-06-01', description: 'SYNTH XFER OUT', amount: '-500.00', bank: 'commbank' },
      in: { id: 2, date: '2026-06-02', description: 'SYNTH XFER IN', amount: '500.00', bank: 'westpac' },
    },
  ],
};

describe('fetchTransfers', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_TRANSFERS));
    const result = await fetchTransfers();
    expect(result).toEqual(CANNED_TRANSFERS);
  });

  it('GETs the /transfers URL', async () => {
    const mockFetch = makeOkFetch(CANNED_TRANSFERS);
    vi.stubGlobal('fetch', mockFetch);
    await fetchTransfers();
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('/transfers');
  });

  it('sends an Accept: application/json header and no body', async () => {
    const mockFetch = makeOkFetch(CANNED_TRANSFERS);
    vi.stubGlobal('fetch', mockFetch);
    await fetchTransfers();
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
    expect(options.body).toBeUndefined();
  });

  it('rejects with ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    const err = await fetchTransfers().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchTransfers().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });
});

describe('postTransferUntag', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch({ ok: true, pair_id: 7, restored: 2 }));
    const result = await postTransferUntag(7);
    expect(result).toEqual({ ok: true, pair_id: 7, restored: 2 });
  });

  it('POSTs to /transfers/{id}/untag', async () => {
    const mockFetch = makeOkFetch({ ok: true, pair_id: 7, restored: 2 });
    vi.stubGlobal('fetch', mockFetch);
    await postTransferUntag(7);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/transfers/7/untag');
    expect(options.method).toBe('POST');
  });

  it('URL-encodes the pair id', async () => {
    const mockFetch = makeOkFetch({ ok: true, pair_id: 0, restored: 0 });
    vi.stubGlobal('fetch', mockFetch);
    await postTransferUntag('a/b');
    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('/transfers/a%2Fb/untag');
  });

  it('rejects with ApiError carrying the status on a 404 (unknown pair)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(404));
    const err = await postTransferUntag(999).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await postTransferUntag(7).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('postTransfersSeen', () => {
  const SEEN_OK = { ok: true, last_viewed_at: '2026-06-02T00:00:00+00:00', transfers_unseen: 0 };

  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(SEEN_OK));
    const result = await postTransfersSeen();
    expect(result).toEqual(SEEN_OK);
  });

  it('POSTs to /transfers/seen with no body', async () => {
    const mockFetch = makeOkFetch(SEEN_OK);
    vi.stubGlobal('fetch', mockFetch);
    await postTransfersSeen();
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/transfers/seen');
    expect(options.method).toBe('POST');
    expect(options.body).toBeUndefined();
  });

  it('rejects with ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    const err = await postTransfersSeen().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await postTransfersSeen().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });
});

// ---------------------------------------------------------------------------
// postCategoryOverride — manual category correction
// ---------------------------------------------------------------------------

describe('postCategoryOverride', () => {
  it('resolves to the updated summary JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch());
    const result = await postCategoryOverride(42, 'Dining Out');
    expect(result).toEqual(CANNED_SUMMARY);
  });

  it('POSTs {id, category} JSON body to /category-override', async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal('fetch', mockFetch);
    await postCategoryOverride(42, 'Dining Out');
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/category-override');
    expect(options.method).toBe('POST');
    expect(options.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(JSON.parse(options.body)).toEqual({ id: 42, category: 'Dining Out' });
  });

  it('rejects with ApiError carrying the status on a 400 (unknown category)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await postCategoryOverride(42, 'Bananas').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it('rejects with ApiError carrying the status on a 404 (row not found)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(404));
    const err = await postCategoryOverride(999999, 'Groceries').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await postCategoryOverride(42, 'Groceries').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
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
// fetchTrends — v2 Pass 2 category-trend window
// ---------------------------------------------------------------------------

const CANNED_TRENDS_VIEW = {
  window: 6,
  end_month: '2026-06',
  months: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'],
  series: [{ category: 'Groceries', values: ['-50.00', '-40.00', '-60.00', '-55.00', '-45.00', '-52.00'] }],
  spend_by_month: ['50.00', '40.00', '60.00', '55.00', '45.00', '52.00'],
  months_available: 6,
};

describe('fetchTrends', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_TRENDS_VIEW));
    const result = await fetchTrends(6, '2026-06');
    expect(result).toEqual(CANNED_TRENDS_VIEW);
  });

  it('appends ?months= when months is supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_TRENDS_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchTrends(3);
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('/trends?months=3');
  });

  it('appends both months and end when both are supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_TRENDS_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchTrends(12, '2026-06');
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain('months=12');
    expect(calledUrl).toContain('end=2026-06');
  });

  it('omits the query string entirely when neither arg is supplied', async () => {
    const mockFetch = makeOkFetch(CANNED_TRENDS_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchTrends();
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toMatch(/\/trends$/);
    expect(calledUrl).not.toContain('?');
  });

  it('sends an Accept: application/json header', async () => {
    const mockFetch = makeOkFetch(CANNED_TRENDS_VIEW);
    vi.stubGlobal('fetch', mockFetch);
    await fetchTrends();
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(fetchTrends()).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError on a 400 (malformed months/end)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await fetchTrends(0).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchTrends().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// fetchBalances — net-position closing-balance series (v7 feature 3)
// ---------------------------------------------------------------------------

const CANNED_BALANCES = {
  months: ['2026-05', '2026-06'],
  series: [
    { bank: 'commbank', values: ['1000.00', '1010.00'] },
    { bank: 'westpac', values: ['500.00', null] },
  ],
  net: ['1500.00', null],
};

describe('fetchBalances', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch(CANNED_BALANCES));
    const result = await fetchBalances();
    expect(result).toEqual(CANNED_BALANCES);
  });

  it('hits ${API_BASE}/balances with no query string', async () => {
    const mockFetch = makeOkFetch(CANNED_BALANCES);
    vi.stubGlobal('fetch', mockFetch);
    await fetchBalances();
    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toBe(`${API_BASE}/balances`);
    expect(calledUrl).not.toContain('?');
  });

  it('sends an Accept: application/json header', async () => {
    const mockFetch = makeOkFetch(CANNED_BALANCES);
    vi.stubGlobal('fetch', mockFetch);
    await fetchBalances();
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(fetchBalances()).rejects.toBeInstanceOf(ApiError);
  });

  it('ApiError carries the HTTP status code from the response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(404));
    const err = await fetchBalances().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await fetchBalances().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
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
// Settings + Feature E endpoints (getSettings / putSettings / corrections /
// categoriser / reset / csv url). SYNTHETIC fixtures only.
// ---------------------------------------------------------------------------

const CANNED_SETTINGS = {
  corrections_enabled: false,
  notifications: { processed: true, monthly_reminder: false },
};

describe('getSettings', () => {
  it('GETs /settings and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch(CANNED_SETTINGS);
    vi.stubGlobal('fetch', mockFetch);
    const result = await getSettings();
    expect(result).toEqual(CANNED_SETTINGS);
    expect(mockFetch.mock.calls[0][0]).toContain('/settings');
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(getSettings()).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await getSettings().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('putSettings', () => {
  it('PUTs the partial JSON body to /settings', async () => {
    const mockFetch = makeOkFetch(CANNED_SETTINGS);
    vi.stubGlobal('fetch', mockFetch);
    await putSettings({ notifications: { processed: false } });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/settings');
    expect(options.method).toBe('PUT');
    expect(options.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(JSON.parse(options.body)).toEqual({ notifications: { processed: false } });
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(422));
    await expect(putSettings({ corrections_enabled: true })).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await putSettings({}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// getBudgets / putBudgets — v6 per-category monthly budgets
// ---------------------------------------------------------------------------

const CANNED_BUDGETS = {
  categories: ['Groceries', 'Housing', 'Dining Out', 'Transport', 'Entertainment', 'Subscriptions', 'Other'],
  budgets: { Groceries: '250.00' },
};

describe('getBudgets', () => {
  it('GETs /budgets and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch(CANNED_BUDGETS);
    vi.stubGlobal('fetch', mockFetch);
    const result = await getBudgets();
    expect(result).toEqual(CANNED_BUDGETS);
    expect(mockFetch.mock.calls[0][0]).toContain('/budgets');
  });

  it('sends an Accept: application/json header and no body', async () => {
    const mockFetch = makeOkFetch(CANNED_BUDGETS);
    vi.stubGlobal('fetch', mockFetch);
    await getBudgets();
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
    expect(options.body).toBeUndefined();
  });

  it('rejects with ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    const err = await getBudgets().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await getBudgets().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });
});

// ---------------------------------------------------------------------------
// getScorecard — v7 categoriser accuracy scorecard (read-only)
// ---------------------------------------------------------------------------

const CANNED_SCORECARD = {
  window: 3,
  months: [
    { month: '2026-05', auto_categorised: 0, corrected: 0, accuracy_pct: null },
    { month: '2026-06', auto_categorised: 103, corrected: 4, accuracy_pct: 96 },
    { month: '2026-07', auto_categorised: 0, corrected: 0, accuracy_pct: null },
  ],
  current: { month: '2026-07', auto_categorised: 0, corrected: 0, accuracy_pct: null },
};

describe('getScorecard', () => {
  it('GETs /categoriser/scorecard and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch(CANNED_SCORECARD);
    vi.stubGlobal('fetch', mockFetch);
    const result = await getScorecard();
    expect(result).toEqual(CANNED_SCORECARD);
    expect(mockFetch.mock.calls[0][0]).toContain('/categoriser/scorecard');
  });

  it('sends an Accept: application/json header and no body', async () => {
    const mockFetch = makeOkFetch(CANNED_SCORECARD);
    vi.stubGlobal('fetch', mockFetch);
    await getScorecard();
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
    expect(options.body).toBeUndefined();
  });

  it('rejects with ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    const err = await getScorecard().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await getScorecard().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });
});

describe('putBudgets', () => {
  it('PUTs the partial JSON body to /budgets', async () => {
    const mockFetch = makeOkFetch(CANNED_BUDGETS);
    vi.stubGlobal('fetch', mockFetch);
    await putBudgets({ budgets: { Groceries: '250' } });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/budgets');
    expect(options.method).toBe('PUT');
    expect(options.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(JSON.parse(options.body)).toEqual({ budgets: { Groceries: '250' } });
  });

  it('serialises a null value (clear) in the body', async () => {
    const mockFetch = makeOkFetch(CANNED_BUDGETS);
    vi.stubGlobal('fetch', mockFetch);
    await putBudgets({ budgets: { Groceries: null } });
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ budgets: { Groceries: null } });
  });

  it('sends an empty object body when called with no argument', async () => {
    const mockFetch = makeOkFetch(CANNED_BUDGETS);
    vi.stubGlobal('fetch', mockFetch);
    await putBudgets();
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({});
  });

  it('rejects with ApiError carrying the status on a 400 (invalid amount)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await putBudgets({ budgets: { Groceries: '-5' } }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await putBudgets({ budgets: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// getSubscriptions — v6 recurring-payment watch (read-only)
// ---------------------------------------------------------------------------

const CANNED_SUBSCRIPTIONS = {
  count: 2,
  subscriptions: [
    {
      merchant: 'STREAMCO',
      direction: 'spend',
      amount: '22.99',
      first_seen_month: '2026-04',
      last_seen_month: '2026-06',
      status: 'active',
    },
    {
      merchant: 'ACME SALARY',
      direction: 'income',
      amount: '5000.00',
      first_seen_month: '2026-01',
      last_seen_month: '2026-04',
      status: 'ended',
    },
  ],
};

describe('getSubscriptions', () => {
  it('GETs /subscriptions and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch(CANNED_SUBSCRIPTIONS);
    vi.stubGlobal('fetch', mockFetch);
    const result = await getSubscriptions();
    expect(result).toEqual(CANNED_SUBSCRIPTIONS);
    expect(mockFetch.mock.calls[0][0]).toContain('/subscriptions');
  });

  it('sends an Accept: application/json header and no body', async () => {
    const mockFetch = makeOkFetch(CANNED_SUBSCRIPTIONS);
    vi.stubGlobal('fetch', mockFetch);
    await getSubscriptions();
    const options = mockFetch.mock.calls[0][1];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
    expect(options.body).toBeUndefined();
  });

  it('rejects with ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    const err = await getSubscriptions().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await getSubscriptions().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });
});

const CANNED_CORRECTIONS = {
  enabled: true,
  corrections: [
    { id: 1, cleaned_description: 'SYNTH MERCHANT', category: 'Dining Out', created_at: '2026-06-01' },
  ],
};

describe('getCorrections', () => {
  it('GETs /corrections and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch(CANNED_CORRECTIONS);
    vi.stubGlobal('fetch', mockFetch);
    const result = await getCorrections();
    expect(result).toEqual(CANNED_CORRECTIONS);
    expect(mockFetch.mock.calls[0][0]).toContain('/corrections');
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await getCorrections().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('deleteCorrection', () => {
  it('DELETEs /corrections/{id}', async () => {
    const mockFetch = makeOkFetch({ ok: true, removed: 1 });
    vi.stubGlobal('fetch', mockFetch);
    const result = await deleteCorrection(7);
    expect(result).toEqual({ ok: true, removed: 1 });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/corrections/7');
    expect(options.method).toBe('DELETE');
  });

  it('rejects with ApiError carrying the status on a 404', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(404));
    const err = await deleteCorrection(999).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});

describe('getCategoriserStatus', () => {
  it('GETs /categoriser/status and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch({ configured: true, uncategorised_count: 4 });
    vi.stubGlobal('fetch', mockFetch);
    const result = await getCategoriserStatus();
    expect(result).toEqual({ configured: true, uncategorised_count: 4 });
    expect(mockFetch.mock.calls[0][0]).toContain('/categoriser/status');
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await getCategoriserStatus().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('postCategoriserTest', () => {
  it('POSTs /categoriser/test and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch({ configured: true, reachable: true, rate_limited: false, detail: '' });
    vi.stubGlobal('fetch', mockFetch);
    const result = await postCategoriserTest();
    expect(result.reachable).toBe(true);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/categoriser/test');
    expect(options.method).toBe('POST');
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(postCategoriserTest()).rejects.toBeInstanceOf(ApiError);
  });
});

describe('postCategoriserRetry', () => {
  it('POSTs /categoriser/retry and resolves parsed JSON', async () => {
    const mockFetch = makeOkFetch({ ok: true, categorised: 3, remaining: 0 });
    vi.stubGlobal('fetch', mockFetch);
    const result = await postCategoriserRetry();
    expect(result).toEqual({ ok: true, categorised: 3, remaining: 0 });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/categoriser/retry');
    expect(options.method).toBe('POST');
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await postCategoriserRetry().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('postReset', () => {
  it('POSTs {confirm} JSON body to /reset', async () => {
    const mockFetch = makeOkFetch({ ok: true, cleared: {} });
    vi.stubGlobal('fetch', mockFetch);
    await postReset('RESET');
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/reset');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ confirm: 'RESET' });
  });

  it('rejects with ApiError carrying the status on a 400 (wrong confirm)', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(400));
    const err = await postReset('nope').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });
});

describe('transactionsCsvUrl', () => {
  it('returns the CSV export URL under API_BASE (no network call)', () => {
    expect(transactionsCsvUrl()).toBe(`${API_BASE}/export/transactions.csv`);
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

// ---------------------------------------------------------------------------
// postPushSubscribe / postPushUnsubscribe — v2 Pass 3 (SYNTHETIC subscription only)
// ---------------------------------------------------------------------------

const CANNED_SYNTH_SUB = {
  endpoint: 'https://example.test/push/SYNTH_ENDPOINT',
  keys: { p256dh: 'synth_p256dh', auth: 'synth_auth' },
};

describe('postPushSubscribe', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch({ ok: true }));
    const result = await postPushSubscribe(CANNED_SYNTH_SUB);
    expect(result).toEqual({ ok: true });
  });

  it('POSTs the subscription JSON body to /push/subscribe', async () => {
    const mockFetch = makeOkFetch({ ok: true });
    vi.stubGlobal('fetch', mockFetch);
    await postPushSubscribe(CANNED_SYNTH_SUB);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/push/subscribe');
    expect(options.method).toBe('POST');
    expect(options.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(JSON.parse(options.body)).toEqual(CANNED_SYNTH_SUB);
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(422));
    await expect(postPushSubscribe(CANNED_SYNTH_SUB)).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await postPushSubscribe(CANNED_SYNTH_SUB).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});

describe('postPushUnsubscribe', () => {
  it('resolves to parsed JSON on a 200 response', async () => {
    vi.stubGlobal('fetch', makeOkFetch({ ok: true, removed: 1 }));
    const result = await postPushUnsubscribe(CANNED_SYNTH_SUB.endpoint);
    expect(result).toEqual({ ok: true, removed: 1 });
  });

  it('POSTs {endpoint} JSON body to /push/unsubscribe', async () => {
    const mockFetch = makeOkFetch({ ok: true, removed: 1 });
    vi.stubGlobal('fetch', mockFetch);
    await postPushUnsubscribe(CANNED_SYNTH_SUB.endpoint);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/push/unsubscribe');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ endpoint: CANNED_SYNTH_SUB.endpoint });
  });

  it('rejects with ApiError on a non-200 response', async () => {
    vi.stubGlobal('fetch', makeErrorFetch(500));
    await expect(postPushUnsubscribe(CANNED_SYNTH_SUB.endpoint)).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError (not raw TypeError) on network failure', async () => {
    vi.stubGlobal('fetch', makeNetworkFailFetch());
    const err = await postPushUnsubscribe(CANNED_SYNTH_SUB.endpoint).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
  });
});
