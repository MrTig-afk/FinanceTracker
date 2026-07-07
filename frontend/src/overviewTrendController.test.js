/**
 * overviewTrendController.test.js — DOM wiring tests for overviewTrendController.js.
 * Chart.js is mocked via vi.mock (hoisted) so no real canvas is needed.
 * A fake `fetchFn` is injected — no real network. All fixtures are
 * SYNTHETIC (invented categories/amounts), never real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ---------------------------------------------------------------------------
// Mock chart.js BEFORE overviewTrendController.js is imported (hoisted).
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
    BarController: {},
    BarElement: {},
    CategoryScale: {},
    LinearScale: {},
    Tooltip: {},
  };
});

import { Chart } from 'chart.js';
import { createOverviewTrend } from './overviewTrendController.js';
import { monthLabel } from './summary.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Overview mini bar markup in index.html.
// ---------------------------------------------------------------------------

const OVERVIEW_TREND_HTML = `
  <p id="overview-trend-message" hidden></p>
  <canvas id="overview-trend-canvas" width="360" height="180"></canvas>
`;

// ---------------------------------------------------------------------------
// Synthetic fixtures — invented amounts, never real data.
// ---------------------------------------------------------------------------

const CANNED_TRENDS = {
  window: 6,
  end_month: '2026-06',
  months: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'],
  series: [
    { category: 'Groceries', values: ['-100.00', '-90.00', '-80.00', '-95.00', '-85.00', '-92.00'] },
  ],
  spend_by_month: ['120.00', '115.00', '95.00', '117.00', '103.00', '113.00'],
  months_available: 6,
};

const EMPTY_TRENDS = {
  window: 6,
  end_month: null,
  months: [],
  series: [],
  spend_by_month: [],
  months_available: 0,
};

let controller;

beforeEach(() => {
  document.body.innerHTML = OVERVIEW_TREND_HTML;
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
  it('calls fetchFn with no arguments', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();
    expect(fetchFn).toHaveBeenCalledWith();
  });

  it('constructs exactly one bar Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();
    expect(Chart).toHaveBeenCalledTimes(1);
    expect(Chart.mock.calls[0][1].type).toBe('bar');
  });

  it('hides the message banner', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('overview-trend-message').hidden).toBe(true);
  });

  it('builds ONE dataset from spend_by_month', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    expect(config.data.datasets.length).toBe(1);
    expect(config.data.datasets[0].data).toEqual([120, 115, 95, 117, 103, 113]);
  });

  it('maps chart labels via monthLabel(months)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    expect(config.data.labels).toEqual(CANNED_TRENDS.months.map(monthLabel));
  });

  it('uses a single fixed bar colour', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    expect(typeof config.data.datasets[0].backgroundColor).toBe('string');
    expect(config.data.datasets[0].backgroundColor.length).toBeGreaterThan(0);
  });

  it('hides the legend (single series)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();

    const config = Chart.mock.calls[0][1];
    expect(config.options.plugins.legend.display).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe('empty state (months.length === 0)', () => {
  it('does NOT construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('shows the exact "No spending history yet." message', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();

    const msg = document.getElementById('overview-trend-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toBe('No spending history yet.');
  });
});

// ---------------------------------------------------------------------------
// Best-effort contract — load() NEVER throws, even on fetchFn rejection.
// ---------------------------------------------------------------------------

describe('best-effort load() — fetchFn rejects', () => {
  it('resolves without throwing', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('SYNTH_SECRET_STACK_DETAIL'));
    controller = createOverviewTrend({ root: document, fetchFn });
    await expect(controller.load()).resolves.not.toThrow();
  });

  it('does not construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('never exposes the raw error text', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('SYNTH_SECRET_STACK_DETAIL'));
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();

    const msg = document.getElementById('overview-trend-message');
    expect(msg.textContent).not.toContain('SYNTH_SECRET_STACK_DETAIL');
  });

  it('keeps the message hidden — the offline banner owns the explanation', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('network down'));
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();

    expect(document.getElementById('overview-trend-message').hidden).toBe(true);
  });

  it('hides a previously shown message when a later load fails', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(EMPTY_TRENDS)
      .mockRejectedValueOnce(new Error('network down'));
    controller = createOverviewTrend({ root: document, fetchFn });

    await controller.load();
    expect(document.getElementById('overview-trend-message').hidden).toBe(false);

    await controller.load();
    expect(document.getElementById('overview-trend-message').hidden).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('destroys a live chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createOverviewTrend({ root: document, fetchFn });
    await controller.load();
    const instance = Chart.mock.results[0].value;

    controller.destroy();
    expect(instance.destroy).toHaveBeenCalledOnce();
    controller = null;
  });

  it('is safe to call before load()', () => {
    controller = createOverviewTrend({ root: document, fetchFn: vi.fn() });
    expect(() => controller.destroy()).not.toThrow();
    controller = null;
  });
});
