/**
 * searchController.test.js — DOM wiring tests for searchController.js.
 *
 * A fake `fetchFn` is injected (no real network); fake timers drive the debounce.
 * All fixtures are SYNTHETIC (invented merchants/amounts), never real data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { createSearch } from './searchController.js';
import { formatCurrency } from './summary.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Search view markup in index.html.
// ---------------------------------------------------------------------------

const SEARCH_HTML = `
  <div class="search-toolbar">
    <input id="search-input" type="search" class="search-input" />
  </div>
  <p id="search-message" class="message-banner"></p>
  <section class="card">
    <div id="search-results" class="cat-drawer-body"></div>
  </section>
`;

// ---------------------------------------------------------------------------
// Synthetic canned responses.
// ---------------------------------------------------------------------------

const CANNED_RESULTS = {
  query: 'coffee',
  month: null,
  total: '-16.75',
  count: 2,
  transactions: [
    { id: 2, date: '2026-06-20', description: 'SYNTH COFFEE B', amount: '-4.25', bank: 'commbank', category: 'Dining Out' },
    { id: 1, date: '2026-06-12', description: 'SYNTH COFFEE A', amount: '-12.50', bank: 'commbank', category: 'Dining Out' },
  ],
};

const EMPTY_RESULTS = {
  query: 'zzz',
  month: null,
  total: '0.00',
  count: 0,
  transactions: [],
};

const _HINT = 'Type to search your transactions.';
const _NO_RESULTS = 'No transactions match that search.';
const _ERROR = 'Could not run search.';

const $ = (id) => document.getElementById(id);
const rows = () => [...$('search-results').querySelectorAll('.cat-drawer-row')];

function type(value) {
  const input = $('search-input');
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

let controller;

beforeEach(() => {
  document.body.innerHTML = SEARCH_HTML;
  vi.useFakeTimers();
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// load() — initial hint state
// ---------------------------------------------------------------------------

describe('load()', () => {
  it('shows the hint and clears input/results', () => {
    const fetchFn = vi.fn();
    controller = createSearch({ root: document, fetchFn });
    $('search-input').value = 'stale';
    controller.load();
    expect($('search-message').textContent).toBe(_HINT);
    expect($('search-input').value).toBe('');
    expect(rows().length).toBe(0);
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Debounce
// ---------------------------------------------------------------------------

describe('debounce', () => {
  it('fires exactly one fetch with the final value after rapid typing', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });

    type('c');
    type('co');
    type('cof');
    type('coffee');

    // Nothing fires until the debounce window elapses.
    expect(fetchFn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(250);

    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith('coffee');
  });

  it('does not fetch before the debounce window elapses', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    type('coffee');
    await vi.advanceTimersByTimeAsync(249);
    expect(fetchFn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Results rendering
// ---------------------------------------------------------------------------

describe('results', () => {
  it('renders one .cat-drawer-row per transaction using the shared row builder', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    type('coffee');
    await vi.advanceTimersByTimeAsync(250);

    expect(rows().length).toBe(2);
    // Row built via buildRowMain -> .cat-drawer-row-main with the shared classes.
    expect(rows()[0].querySelector('.cat-drawer-row-main')).not.toBeNull();
    expect(rows()[0].querySelector('.cat-drawer-desc').textContent).toBe('SYNTH COFFEE B');
    expect(rows()[0].querySelector('.cat-drawer-amount').textContent).toBe(formatCurrency('-4.25'));
  });

  it('shows a count + total line in the message banner', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    type('coffee');
    await vi.advanceTimersByTimeAsync(250);
    const msg = $('search-message').textContent;
    expect(msg).toContain('2 transactions');
    expect(msg).toContain(formatCurrency('-16.75'));
  });

  it('uses the singular noun for a single result', async () => {
    const single = { ...CANNED_RESULTS, count: 1, transactions: [CANNED_RESULTS.transactions[0]] };
    const fetchFn = vi.fn().mockResolvedValue(single);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    type('coffee');
    await vi.advanceTimersByTimeAsync(250);
    expect($('search-message').textContent).toContain('1 transaction ');
  });

  it('renders read-only rows (no category picker)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    type('coffee');
    await vi.advanceTimersByTimeAsync(250);
    expect($('search-results').querySelector('select')).toBeNull();
    expect($('search-results').querySelector('.cat-drawer-picker')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Blank input
// ---------------------------------------------------------------------------

describe('blank input', () => {
  it('shows the hint, clears results, and does not fetch', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });

    // First a real search to populate results.
    type('coffee');
    await vi.advanceTimersByTimeAsync(250);
    expect(rows().length).toBe(2);

    fetchFn.mockClear();
    type('   '); // whitespace only -> treated as blank
    await vi.advanceTimersByTimeAsync(250);

    expect(fetchFn).not.toHaveBeenCalled();
    expect(rows().length).toBe(0);
    expect($('search-message').textContent).toBe(_HINT);
  });
});

// ---------------------------------------------------------------------------
// No results / error states
// ---------------------------------------------------------------------------

describe('empty + error states', () => {
  it('shows the no-results message when the response is empty', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    type('zzz');
    await vi.advanceTimersByTimeAsync(250);
    expect(rows().length).toBe(0);
    expect($('search-message').textContent).toBe(_NO_RESULTS);
  });

  it('shows a fixed error message when fetchFn rejects (no throw, no raw error)', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom stack SYNTH-SECRET'));
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    type('coffee');
    await vi.advanceTimersByTimeAsync(250);
    expect($('search-message').textContent).toBe(_ERROR);
    expect($('search-message').textContent).not.toContain('SYNTH-SECRET');
    expect(rows().length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Stale-response guard
// ---------------------------------------------------------------------------

describe('stale-response guard', () => {
  it('ignores an in-flight response when the input changed since it was issued', async () => {
    // First query resolves slowly; second query's value differs -> first is dropped.
    let resolveFirst;
    const firstPromise = new Promise((res) => { resolveFirst = res; });

    const fetchFn = vi.fn()
      .mockReturnValueOnce(firstPromise)
      .mockResolvedValueOnce({
        ...CANNED_RESULTS,
        transactions: [
          { id: 9, date: '2026-06-25', description: 'SYNTH TEA', amount: '-2.00', bank: 'commbank', category: 'Dining Out' },
        ],
        count: 1,
        total: '-2.00',
      });

    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });

    type('coffee');
    await vi.advanceTimersByTimeAsync(250); // issues request #1 (pending)

    type('tea');
    await vi.advanceTimersByTimeAsync(250); // issues + resolves request #2

    // Now resolve the STALE first request; its render must be discarded.
    resolveFirst(CANNED_RESULTS);
    await Promise.resolve();
    await Promise.resolve();

    const descs = rows().map((r) => r.querySelector('.cat-drawer-desc').textContent);
    expect(descs).toEqual(['SYNTH TEA']);
    expect(descs).not.toContain('SYNTH COFFEE B');
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('detaches the input listener so later typing does not fetch', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_RESULTS);
    controller = createSearch({ root: document, fetchFn, debounceMs: 250 });
    controller.destroy();
    controller = null;

    type('coffee');
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchFn).not.toHaveBeenCalled();
  });
});
