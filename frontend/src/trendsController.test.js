/**
 * trendsController.test.js — DOM wiring tests for trendsController.js.
 * Chart.js is mocked via vi.mock (hoisted) so no real canvas is needed.
 * A fake `fetchFn` is injected — no real network. All fixtures are
 * SYNTHETIC (invented categories/amounts), never real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ---------------------------------------------------------------------------
// Mock chart.js BEFORE trendsController.js is imported (hoisted by Vitest).
// ---------------------------------------------------------------------------

vi.mock('chart.js', () => {
  const Chart = vi.fn(function () {
    return {
      destroy: vi.fn(),
      update: vi.fn(),
    };
  });
  Chart.register = vi.fn();
  return {
    Chart,
    LineController: {},
    LineElement: {},
    PointElement: {},
    CategoryScale: {},
    LinearScale: {},
    Legend: {},
    Tooltip: {},
  };
});

import { Chart } from 'chart.js';
import { createTrends } from './trendsController.js';
import { colorFor, monthLabel } from './summary.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Trends view markup in index.html.
// ---------------------------------------------------------------------------

const TRENDS_HTML = `
  <select id="trends-window"></select>
  <p id="trends-message" hidden></p>
  <canvas id="trends-canvas" width="720" height="360"></canvas>
`;

// ---------------------------------------------------------------------------
// Synthetic fixtures — invented categories/amounts, never real data.
// ---------------------------------------------------------------------------

const CANNED_TRENDS = {
  window: 6,
  end_month: '2026-06',
  months: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'],
  series: [
    { category: 'Groceries', values: ['-100.00', '-90.00', '-80.00', '-95.00', '-85.00', '-92.00'] },
    { category: 'Transport', values: ['-20.00', '-25.00', '-15.00', '-22.00', '-18.00', '-21.00'] },
    { category: 'Income', values: ['3000.00', '3000.00', '3000.00', '3000.00', '3000.00', '3000.00'] },
  ],
  spend_by_month: ['120.00', '115.00', '95.00', '117.00', '103.00', '113.00'],
  months_available: 6,
};

// months_available is a GLOBAL distinct-month count, independent of the
// requested window — a 1-populated-month DB can still return a full
// zero-filled 6-month window with non-empty months/series.
const SINGLE_MONTH_TRENDS = {
  window: 6,
  end_month: '2026-06',
  months: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'],
  series: [
    { category: 'Groceries', values: ['0.00', '0.00', '0.00', '0.00', '0.00', '-50.00'] },
  ],
  spend_by_month: ['0.00', '0.00', '0.00', '0.00', '0.00', '50.00'],
  months_available: 1,
};

const EMPTY_TRENDS = {
  window: 6,
  end_month: null,
  months: [],
  series: [],
  spend_by_month: [],
  months_available: 0,
};

// A populated window where every dataset value is exactly zero (after
// building datasets, excluding Income) — must also show the empty state,
// WITHOUT ever constructing a Chart.
const ALL_ZERO_TRENDS = {
  window: 3,
  end_month: '2026-06',
  months: ['2026-04', '2026-05', '2026-06'],
  series: [
    { category: 'Groceries', values: ['0.00', '0.00', '0.00'] },
  ],
  spend_by_month: ['0.00', '0.00', '0.00'],
  months_available: 3,
};

const _NOT_ENOUGH_HISTORY = 'Not enough history yet. Upload at least two months to see trends.';

let controller;

beforeEach(() => {
  document.body.innerHTML = TRENDS_HTML;
  Chart.mockClear();
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// load() — happy path
// ---------------------------------------------------------------------------

describe('load() happy path', () => {
  it('calls fetchFn with no argument', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(fetchFn).toHaveBeenCalledWith(undefined);
  });

  it('constructs exactly one Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(Chart).toHaveBeenCalledTimes(1);
  });

  it('hides the message banner', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('trends-message').hidden).toBe(true);
  });

  it('builds one dataset per NON-Income series (excludes Income)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    const labels = config.data.datasets.map((d) => d.label);
    expect(labels).toEqual(['Groceries', 'Transport']);
    expect(labels).not.toContain('Income');
  });

  it('maps chart labels via monthLabel(months)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    expect(config.data.labels).toEqual(CANNED_TRENDS.months.map(monthLabel));
  });

  it('sets dataset colours via colorFor(category)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    const groceries = config.data.datasets.find((d) => d.label === 'Groceries');
    expect(groceries.borderColor).toBe(colorFor('Groceries'));
    expect(groceries.backgroundColor).toBe(colorFor('Groceries'));
  });

  it('dataset data values are Math.abs of the series values', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    const groceries = config.data.datasets.find((d) => d.label === 'Groceries');
    expect(groceries.data).toEqual([100, 90, 80, 95, 85, 92]);
  });

  it('populates the window <select> with [3, 6, 12, 24] labelled "N months"', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const select = document.getElementById('trends-window');
    const options = [...select.options];
    expect(options.map((o) => o.value)).toEqual(['3', '6', '12', '24']);
    expect(options.map((o) => o.textContent)).toEqual(['3 months', '6 months', '12 months', '24 months']);
  });

  it('default-selects the response window', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const select = document.getElementById('trends-window');
    expect(select.value).toBe('6');
  });

  it('does not re-populate the <select> on a second load', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    await controller.load();

    const select = document.getElementById('trends-window');
    expect(select.options.length).toBe(4);
  });
});

// ---------------------------------------------------------------------------
// Insufficient-history / empty states
// ---------------------------------------------------------------------------

describe('insufficient history (months_available <= 1)', () => {
  it('does NOT construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(SINGLE_MONTH_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('shows the exact insufficient-history message', async () => {
    const fetchFn = vi.fn().mockResolvedValue(SINGLE_MONTH_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const msg = document.getElementById('trends-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toBe(_NOT_ENOUGH_HISTORY);
  });
});

describe('empty response (months.length === 0)', () => {
  it('does NOT construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('shows the exact insufficient-history message', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('trends-message').textContent).toBe(_NOT_ENOUGH_HISTORY);
  });
});

describe('all-zero datasets (populated window, every value zero)', () => {
  it('does NOT construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(ALL_ZERO_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('shows the exact insufficient-history message', async () => {
    const fetchFn = vi.fn().mockResolvedValue(ALL_ZERO_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('trends-message').textContent).toBe(_NOT_ENOUGH_HISTORY);
  });

  it('destroys a previously-rendered chart when transitioning to all-zero', async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce(CANNED_TRENDS)
      .mockResolvedValueOnce(ALL_ZERO_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    const instance = Chart.mock.results[0].value;

    await controller.load();
    expect(instance.destroy).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// Fetch failure
// ---------------------------------------------------------------------------

describe('fetch failure', () => {
  it('shows the exact fixed safe message — never the raw error', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('SYNTH_SECRET_STACK_DETAIL'));
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const msg = document.getElementById('trends-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toBe('Could not load trends.');
    expect(msg.textContent).not.toContain('SYNTH_SECRET_STACK_DETAIL');
  });

  it('does not throw', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createTrends({ root: document, fetchFn });
    await expect(controller.load()).resolves.not.toThrow();
  });

  it('does not construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// <select> change -> fetchFn(Number(value)) + re-render
// ---------------------------------------------------------------------------

describe('<select> change', () => {
  it('re-invokes fetchFn with the chosen number', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const twelveMonths = { ...CANNED_TRENDS, window: 12 };
    fetchFn.mockResolvedValueOnce(twelveMonths);

    const select = document.getElementById('trends-window');
    select.value = '12';
    select.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchFn).toHaveBeenLastCalledWith(12);
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('destroys a live chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    const instance = Chart.mock.results[0].value;

    controller.destroy();
    expect(instance.destroy).toHaveBeenCalledOnce();
    controller = null;
  });

  it('removes the <select> change listener', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    controller.destroy();

    const select = document.getElementById('trends-window');
    select.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 0));

    // No extra call beyond the initial load()'s call count.
    expect(fetchFn).toHaveBeenCalledTimes(1);
    controller = null;
  });

  it('is safe to call before load()', () => {
    controller = createTrends({ root: document, fetchFn: vi.fn() });
    expect(() => controller.destroy()).not.toThrow();
    controller = null;
  });
});
