/**
 * monthlyController.test.js — DOM wiring tests for monthlyController.js.
 * Chart.js is mocked via vi.mock (hoisted) so no real canvas is needed.
 * A fake `fetchFn` is injected — no real network. All fixtures are
 * SYNTHETIC (invented categories/amounts), never real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ---------------------------------------------------------------------------
// Mock chart.js BEFORE monthlyController.js is imported (hoisted by Vitest).
// ---------------------------------------------------------------------------

vi.mock('chart.js', () => {
  const Chart = vi.fn(function () {
    return {
      destroy: vi.fn(),
      update: vi.fn(),
      data: { datasets: [{ borderColor: null }] },
    };
  });
  Chart.register = vi.fn();
  return {
    Chart,
    DoughnutController: {},
    ArcElement: {},
    Legend: {},
  };
});

import { Chart } from 'chart.js';
import { createMonthly } from './monthlyController.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Monthly view markup in index.html.
// ---------------------------------------------------------------------------

const MONTHLY_HTML = `
  <select id="monthly-select"></select>
  <p id="monthly-message" hidden></p>
  <canvas id="monthly-canvas" width="200" height="200"></canvas>
  <span id="monthly-spent"></span>
  <div id="monthly-net"></div>
  <table><tbody id="monthly-totals"></tbody></table>
  <table><tbody id="monthly-compare"></tbody></table>
  <span id="monthly-compare-label"></span>
`;

// ---------------------------------------------------------------------------
// Synthetic fixtures — invented categories/amounts, never real data.
// ---------------------------------------------------------------------------

const CANNED_MONTH = {
  period: 'month',
  ym: '2026-06',
  prev_ym: '2026-05',
  totals: { Groceries: '-150.00', Rent: '-900.00', Income: '3200.00' },
  net: '2150.00',
  count: 12,
  comparison: [
    { category: 'Rent', current: '-900.00', previous: '-900.00', delta: '0.00', pct_change: 0.0 },
    { category: 'Groceries', current: '-150.00', previous: '-100.00', delta: '-50.00', pct_change: 50.0 },
    { category: 'Income', current: '3200.00', previous: '3000.00', delta: '200.00', pct_change: 6.7 },
  ],
  available_months: ['2026-06', '2026-05', '2026-03'],
};

const CANNED_MONTH_NO_PREV = {
  period: 'month',
  ym: '2026-06',
  prev_ym: null,
  totals: { Groceries: '-40.00' },
  net: '-40.00',
  count: 1,
  comparison: [
    { category: 'Groceries', current: '-40.00', previous: '0.00', delta: '-40.00', pct_change: null },
  ],
  available_months: ['2026-06'],
};

const EMPTY_MONTH = {
  period: 'month',
  ym: null,
  prev_ym: null,
  totals: {},
  net: '0.00',
  count: 0,
  comparison: [],
  available_months: [],
};

const DOWN_ROW_MONTH = {
  period: 'month',
  ym: '2026-06',
  prev_ym: '2026-05',
  totals: { Transport: '-20.00' },
  net: '-20.00',
  count: 2,
  comparison: [
    { category: 'Transport', current: '-20.00', previous: '-80.00', delta: '60.00', pct_change: -75.0 },
  ],
  available_months: ['2026-06', '2026-05'],
};

let controller;

beforeEach(() => {
  document.body.innerHTML = MONTHLY_HTML;
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
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(fetchFn).toHaveBeenCalledWith(undefined);
  });

  it('constructs exactly one Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(Chart).toHaveBeenCalledTimes(1);
  });

  it('hides the message banner', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('monthly-message').hidden).toBe(true);
  });

  it('sets #monthly-spent to a formatted currency string', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('monthly-spent').textContent).toContain('$');
  });

  it('sets #monthly-net with net-positive class for a positive net', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    const netEl = document.getElementById('monthly-net');
    expect(netEl.classList.contains('net-positive')).toBe(true);
    expect(netEl.textContent).toContain('2,150');
  });

  it('renders one totals row per category', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    const rows = document.querySelectorAll('#monthly-totals tr');
    expect(rows.length).toBe(3);
  });

  it('renders one comparison row per comparison entry', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    const rows = document.querySelectorAll('#monthly-compare tr');
    expect(rows.length).toBe(3);
  });

  it('sets the compare label to the month label of prev_ym', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('monthly-compare-label').textContent).toBe('May 2026');
  });

  it('populates the <select> from available_months with the response ym selected', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();

    const select = document.getElementById('monthly-select');
    const options = [...select.options];
    expect(options.map((o) => o.value)).toEqual(['2026-06', '2026-05', '2026-03']);
    expect(select.value).toBe('2026-06');
  });
});

// ---------------------------------------------------------------------------
// Comparison delta classes + pct text
// ---------------------------------------------------------------------------

describe('comparison row classes + pct text', () => {
  it('applies delta-up when pct_change > 0', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();

    const rows = [...document.querySelectorAll('#monthly-compare tr')];
    const groceriesRow = rows.find((r) => r.textContent.includes('Groceries'));
    const cells = groceriesRow.querySelectorAll('td');
    // category, current, delta, pct — delta + pct cells carry the class
    expect(cells[2].classList.contains('delta-up')).toBe(true);
    expect(cells[3].classList.contains('delta-up')).toBe(true);
    expect(cells[3].textContent).toBe('50%');
  });

  it('applies delta-down when pct_change < 0', async () => {
    const fetchFn = vi.fn().mockResolvedValue(DOWN_ROW_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();

    const row = document.querySelector('#monthly-compare tr');
    const cells = row.querySelectorAll('td');
    expect(cells[2].classList.contains('delta-down')).toBe(true);
    expect(cells[3].textContent).toBe('-75%');
  });

  it('applies delta-new and renders the em-dash placeholder when pct_change is null', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH_NO_PREV);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();

    const row = document.querySelector('#monthly-compare tr');
    const cells = row.querySelectorAll('td');
    expect(cells[2].classList.contains('delta-new')).toBe(true);
    expect(cells[3].textContent).toBe('n/a');
  });

  it('shows "no prior month" in the compare label when prev_ym is null', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH_NO_PREV);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('monthly-compare-label').textContent).toBe('no prior month');
  });
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe('empty state (count === 0)', () => {
  it('does NOT construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('shows the message banner with non-empty text', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    const msg = document.getElementById('monthly-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent.length).toBeGreaterThan(0);
  });

  it('clears the totals and comparison tables', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(document.querySelectorAll('#monthly-totals tr').length).toBe(0);
    expect(document.querySelectorAll('#monthly-compare tr').length).toBe(0);
  });

  it('empties the <select>', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('monthly-select').options.length).toBe(0);
  });

  it('transitions cleanly from populated to empty on a subsequent load', async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce(CANNED_MONTH)
      .mockResolvedValueOnce(EMPTY_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    expect(document.querySelectorAll('#monthly-totals tr').length).toBe(3);

    await controller.load();
    expect(document.querySelectorAll('#monthly-totals tr').length).toBe(0);
    expect(document.getElementById('monthly-message').hidden).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Fetch failure
// ---------------------------------------------------------------------------

describe('fetch failure', () => {
  it('shows a fixed safe message — never the raw error', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('SYNTH_SECRET_STACK_DETAIL'));
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();

    const msg = document.getElementById('monthly-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).not.toContain('SYNTH_SECRET_STACK_DETAIL');
    expect(msg.textContent.length).toBeGreaterThan(0);
  });

  it('does not throw', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createMonthly({ root: document, fetchFn });
    await expect(controller.load()).resolves.not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// <select> change -> fetchFn(value) + re-render
// ---------------------------------------------------------------------------

describe('<select> change', () => {
  it('calls fetchFn with the selected value and re-renders', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();

    const otherMonth = {
      ...CANNED_MONTH,
      ym: '2026-05',
      prev_ym: '2026-03',
      totals: { Groceries: '-100.00' },
      comparison: [
        { category: 'Groceries', current: '-100.00', previous: '-90.00', delta: '-10.00', pct_change: 11.1 },
      ],
    };
    fetchFn.mockResolvedValueOnce(otherMonth);

    const select = document.getElementById('monthly-select');
    select.value = '2026-05';
    select.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchFn).toHaveBeenLastCalledWith('2026-05');
    expect(document.querySelectorAll('#monthly-totals tr').length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('destroys a live chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    const instance = Chart.mock.results[0].value;

    controller.destroy();
    expect(instance.destroy).toHaveBeenCalledOnce();
    controller = null;
  });

  it('removes the <select> change listener', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_MONTH);
    controller = createMonthly({ root: document, fetchFn });
    await controller.load();
    controller.destroy();

    const select = document.getElementById('monthly-select');
    select.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 0));

    // No extra call beyond the initial load()'s call count.
    expect(fetchFn).toHaveBeenCalledTimes(1);
    controller = null;
  });

  it('is safe to call before load()', () => {
    controller = createMonthly({ root: document, fetchFn: vi.fn() });
    expect(() => controller.destroy()).not.toThrow();
    controller = null;
  });
});
