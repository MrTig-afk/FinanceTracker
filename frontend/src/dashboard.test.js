/**
 * dashboard.test.js — DOM-rendering tests for dashboard.js.
 * Chart.js is mocked via vi.mock (hoisted) so module-level Chart.register
 * does not throw. jsdom provides the DOM environment.
 * All fixtures are synthetic, generated inline. No real transaction data.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ApiError } from './api.js';

// ---------------------------------------------------------------------------
// Mock chart.js BEFORE dashboard.js is imported.
// vi.mock is automatically hoisted above all imports by Vitest.
// ---------------------------------------------------------------------------

vi.mock('chart.js', () => {
  // Use a normal function (not an arrow) so it can be called with `new`:
  // Vitest 4 no longer lets an arrow-function mock implementation be constructed.
  const Chart = vi.fn(function () {
    return { destroy: vi.fn() };
  });
  Chart.register = vi.fn();
  return {
    Chart,
    DoughnutController: {},
    ArcElement: {},
    Tooltip: {},
    Legend: {},
  };
});

// Import the mocked Chart and the module under test AFTER vi.mock declaration.
import { Chart } from 'chart.js';
import { createDashboard } from './dashboard.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the element IDs required by createDashboard().
// ---------------------------------------------------------------------------

const DOM_HTML = `
  <span id="month-label"></span>
  <span id="net-value"></span>
  <canvas id="chart"></canvas>
  <table><tbody id="totals-body"></tbody></table>
  <span id="status-dot"></span>
  <div id="message" hidden></div>
  <button id="refresh"></button>
`;

// ---------------------------------------------------------------------------
// Synthetic fixtures — no real transaction data.
// ---------------------------------------------------------------------------

/** Three categories: two net-expense + Income (net-positive, excluded from pie). */
const SYNTHETIC_SUMMARY = {
  year_month: '2026-06',
  totals: {
    Groceries: '-300.00',
    Transport: '-150.00',
    Income: '3000.00',
  },
  net: '-450.00',
  count: 10,
};

/** All-positive / empty — drives empty state. */
const EMPTY_SUMMARY = {
  year_month: '2026-06',
  totals: {},
  net: '0.00',
  count: 0,
};

/** Only Income in totals — pie is empty but table has one row. */
const INCOME_ONLY_SUMMARY = {
  year_month: '2026-07',
  totals: { Income: '2500.00' },
  net: '2500.00',
  count: 1,
};

// ---------------------------------------------------------------------------
// Setup — rebuild DOM and clear Chart mock before every test.
// ---------------------------------------------------------------------------

let dash;

beforeEach(() => {
  document.body.innerHTML = DOM_HTML;
  Chart.mockClear();
  dash = createDashboard(document);
});

// ---------------------------------------------------------------------------
// render — happy path with spending data
// ---------------------------------------------------------------------------

describe('render with summary data', () => {
  it('sets #month-label to the human-readable month', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(document.getElementById('month-label').textContent).toBe('June 2026');
  });

  it('sets #net-value to a formatted currency string', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const text = document.getElementById('net-value').textContent;
    expect(text).toContain('$');
    expect(text).toContain('450');
  });

  it('populates #totals-body with one <tr> per category (3 rows)', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const rows = document.getElementById('totals-body').querySelectorAll('tr');
    expect(rows.length).toBe(3);
  });

  it('each totals row has exactly 2 <td> cells', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const rows = document.getElementById('totals-body').querySelectorAll('tr');
    rows.forEach((row) => {
      expect(row.querySelectorAll('td').length).toBe(2);
    });
  });

  it('constructs exactly one Chart instance', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(Chart).toHaveBeenCalledTimes(1);
  });

  it('hides #message when spending data is present', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(document.getElementById('message').hidden).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// render — empty summary (no net-expense categories → empty-state)
// ---------------------------------------------------------------------------

describe('render with empty totals', () => {
  it('does NOT construct a Chart instance', () => {
    dash.render(EMPTY_SUMMARY);
    expect(Chart).not.toHaveBeenCalled();
  });

  it('makes #message visible', () => {
    dash.render(EMPTY_SUMMARY);
    expect(document.getElementById('message').hidden).toBe(false);
  });

  it('#message is non-empty', () => {
    dash.render(EMPTY_SUMMARY);
    expect(document.getElementById('message').textContent.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// render — income-only (pie empty but table has rows)
// ---------------------------------------------------------------------------

describe('render with income-only summary', () => {
  it('does NOT construct a Chart (no net-expense category)', () => {
    dash.render(INCOME_ONLY_SUMMARY);
    expect(Chart).not.toHaveBeenCalled();
  });

  it('still populates #totals-body (Income row present)', () => {
    dash.render(INCOME_ONLY_SUMMARY);
    const rows = document.getElementById('totals-body').querySelectorAll('tr');
    expect(rows.length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// showEmpty
// ---------------------------------------------------------------------------

describe('showEmpty', () => {
  it('shows #message', () => {
    dash.showEmpty();
    expect(document.getElementById('message').hidden).toBe(false);
  });

  it('#message text mentions upload', () => {
    dash.showEmpty();
    expect(document.getElementById('message').textContent).toContain('upload');
  });

  it('does not construct a Chart', () => {
    dash.showEmpty();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('clears #totals-body', () => {
    // First render to populate, then showEmpty must clear.
    dash.render(SYNTHETIC_SUMMARY);
    dash.showEmpty();
    const rows = document.getElementById('totals-body').querySelectorAll('tr');
    expect(rows.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// showError
// ---------------------------------------------------------------------------

describe('showError', () => {
  it('shows #message', () => {
    dash.showError(new ApiError('request failed', { status: 503 }));
    expect(document.getElementById('message').hidden).toBe(false);
  });

  it('#message contains the HTTP status code', () => {
    dash.showError(new ApiError('request failed', { status: 503 }));
    expect(document.getElementById('message').textContent).toContain('503');
  });

  it('#message uses a generic string — does not expose raw error message', () => {
    const err = new ApiError('super secret internal error details', { status: 503 });
    dash.showError(err);
    const text = document.getElementById('message').textContent;
    expect(text).not.toContain('super secret internal error details');
  });

  it('#message contains "Could not load summary"', () => {
    dash.showError(new ApiError('anything', { status: 500 }));
    expect(document.getElementById('message').textContent).toContain('Could not load summary');
  });

  it('works when err.status is null (network failure, no HTTP status)', () => {
    const err = new ApiError('network error');
    // should not throw
    expect(() => dash.showError(err)).not.toThrow();
    expect(document.getElementById('message').textContent).toContain('Could not load summary');
  });
});

// ---------------------------------------------------------------------------
// Double render — destroy-before-recreate (prevents "Canvas already in use")
// ---------------------------------------------------------------------------

describe('double render', () => {
  it('calls .destroy() on the first Chart instance before creating the second', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const firstInstance = Chart.mock.results[0].value;

    // First instance must NOT be destroyed yet.
    expect(firstInstance.destroy).not.toHaveBeenCalled();

    dash.render(SYNTHETIC_SUMMARY);

    // First instance must be destroyed before the second render.
    expect(firstInstance.destroy).toHaveBeenCalledOnce();
  });

  it('creates two Chart instances total after two renders', () => {
    dash.render(SYNTHETIC_SUMMARY);
    dash.render(SYNTHETIC_SUMMARY);
    expect(Chart).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// destroy — public teardown method
// ---------------------------------------------------------------------------

describe('destroy', () => {
  it('calls .destroy() on a live Chart instance', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const instance = Chart.mock.results[0].value;
    expect(instance.destroy).not.toHaveBeenCalled();

    dash.destroy();
    expect(instance.destroy).toHaveBeenCalledOnce();
  });

  it('is safe to call when no Chart has been created yet', () => {
    expect(() => dash.destroy()).not.toThrow();
  });
});
