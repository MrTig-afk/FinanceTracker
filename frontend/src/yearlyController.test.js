/**
 * yearlyController.test.js — DOM wiring tests for yearlyController.js.
 * Chart.js is mocked via vi.mock (hoisted) so no real canvas is needed.
 * A fake `fetchFn` is injected — no real network. All fixtures are
 * SYNTHETIC (invented categories/amounts), never real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ---------------------------------------------------------------------------
// Mock chart.js BEFORE yearlyController.js is imported (hoisted by Vitest).
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
import { createYearly } from './yearlyController.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Yearly view markup in index.html.
// ---------------------------------------------------------------------------

const YEARLY_HTML = `
  <select id="yearly-select"></select>
  <p id="yearly-message" hidden></p>
  <canvas id="yearly-canvas" width="200" height="200"></canvas>
  <span id="yearly-spent"></span>
  <div id="yearly-kpi-spent"></div>
  <div id="yearly-income"></div>
  <div id="yearly-net"></div>
  <div id="yearly-legend" class="legend legend--wrap"></div>
  <table>
    <thead><tr><th>Category</th><th>Amount</th></tr></thead>
    <tbody id="yearly-totals"></tbody>
  </table>
  <table>
    <thead><tr>
      <th>Category</th><th>This year</th><th>Last year</th><th>Change</th><th>Change %</th>
    </tr></thead>
    <tbody id="yearly-compare"></tbody>
    <tfoot><tr id="yearly-compare-foot"></tr></tfoot>
  </table>
  <span id="yearly-compare-label"></span>
`;

// ---------------------------------------------------------------------------
// Synthetic fixtures — invented categories/amounts, never real data.
// ---------------------------------------------------------------------------

const CANNED_YEAR = {
  period: 'year',
  y: '2026',
  prev_y: '2025',
  totals: { Groceries: '-1500.00', Rent: '-10800.00', Income: '38400.00' },
  net: '26100.00',
  count: 120,
  comparison: [
    { category: 'Rent', current: '-10800.00', previous: '-10800.00', delta: '0.00', pct_change: 0.0 },
    { category: 'Groceries', current: '-1500.00', previous: '-1200.00', delta: '-300.00', pct_change: 25.0 },
    { category: 'Income', current: '38400.00', previous: '36000.00', delta: '2400.00', pct_change: 6.7 },
  ],
  available_years: ['2026', '2025', '2023'],
};

const CANNED_YEAR_NO_PREV = {
  period: 'year',
  y: '2026',
  prev_y: null,
  totals: { Groceries: '-400.00' },
  net: '-400.00',
  count: 6,
  comparison: [
    { category: 'Groceries', current: '-400.00', previous: '0.00', delta: '-400.00', pct_change: null },
  ],
  available_years: ['2026'],
};

const EMPTY_YEAR = {
  period: 'year',
  y: null,
  prev_y: null,
  totals: {},
  net: '0.00',
  count: 0,
  comparison: [],
  available_years: [],
};

const DOWN_ROW_YEAR = {
  period: 'year',
  y: '2026',
  prev_y: '2025',
  totals: { Transport: '-200.00' },
  net: '-200.00',
  count: 10,
  comparison: [
    { category: 'Transport', current: '-200.00', previous: '-800.00', delta: '600.00', pct_change: -75.0 },
  ],
  available_years: ['2026', '2025'],
};

let controller;

beforeEach(() => {
  document.body.innerHTML = YEARLY_HTML;
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
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(fetchFn).toHaveBeenCalledWith(undefined);
  });

  it('constructs exactly one Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(Chart).toHaveBeenCalledTimes(1);
  });

  it('hides the message banner', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('yearly-message').hidden).toBe(true);
  });

  it('sets #yearly-spent to a formatted currency string', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('yearly-spent').textContent).toContain('$');
  });

  it('sets #yearly-net with net-positive class for a positive net', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    const netEl = document.getElementById('yearly-net');
    expect(netEl.classList.contains('net-positive')).toBe(true);
  });

  it('renders one totals row per category', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    const rows = document.querySelectorAll('#yearly-totals tr');
    expect(rows.length).toBe(3);
  });

  it('renders one comparison row per comparison entry', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    const rows = document.querySelectorAll('#yearly-compare tr');
    expect(rows.length).toBe(3);
  });

  it('sets the compare label to the raw prev_y string (no transform)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('yearly-compare-label').textContent).toBe('2025');
  });

  it('populates the <select> from available_years with the response y selected, option text = raw year', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();

    const select = document.getElementById('yearly-select');
    const options = [...select.options];
    expect(options.map((o) => o.value)).toEqual(['2026', '2025', '2023']);
    expect(options.map((o) => o.textContent)).toEqual(['2026', '2025', '2023']);
    expect(select.value).toBe('2026');
  });
});

// ---------------------------------------------------------------------------
// Comparison delta classes + pct text
// ---------------------------------------------------------------------------

describe('comparison row classes + pct text', () => {
  it('applies delta-up when pct_change > 0', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();

    const rows = [...document.querySelectorAll('#yearly-compare tr')];
    const groceriesRow = rows.find((r) => r.textContent.includes('Groceries'));
    const cells = groceriesRow.querySelectorAll('td');
    // category, this year, last year, change, change % — change + % carry the class
    expect(cells.length).toBe(5);
    expect(cells[3].classList.contains('delta-up')).toBe(true);
    expect(cells[4].classList.contains('delta-up')).toBe(true);
    expect(cells[4].textContent).toContain('25%');
  });

  it('applies delta-down when pct_change < 0', async () => {
    const fetchFn = vi.fn().mockResolvedValue(DOWN_ROW_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();

    const row = document.querySelector('#yearly-compare tr');
    const cells = row.querySelectorAll('td');
    expect(cells[3].classList.contains('delta-down')).toBe(true);
    expect(cells[4].textContent).toContain('-75%');
  });

  it('applies delta-new and renders the n/a placeholder when pct_change is null', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR_NO_PREV);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();

    const row = document.querySelector('#yearly-compare tr');
    const cells = row.querySelectorAll('td');
    expect(cells[3].classList.contains('delta-new')).toBe(true);
    expect(cells[4].textContent).toBe('n/a');
  });

  it('renders the last-year (previous) column in each comparison row', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();

    const rows = [...document.querySelectorAll('#yearly-compare tr')];
    const groceriesRow = rows.find((r) => r.textContent.includes('Groceries'));
    const cells = groceriesRow.querySelectorAll('td');
    // cells[2] is "Last year" — Groceries previous was -1200.00
    expect(cells[2].textContent).toContain('1,200');
  });

  it('shows "no prior year" in the compare label when prev_y is null', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR_NO_PREV);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('yearly-compare-label').textContent).toBe('no prior year');
  });
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe('empty state (count === 0)', () => {
  it('does NOT construct a Chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(Chart).not.toHaveBeenCalled();
  });

  it('shows the message banner with non-empty text', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    const msg = document.getElementById('yearly-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent.length).toBeGreaterThan(0);
  });

  it('clears the totals and comparison tables', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.querySelectorAll('#yearly-totals tr').length).toBe(0);
    expect(document.querySelectorAll('#yearly-compare tr').length).toBe(0);
  });

  it('empties the <select>', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('yearly-select').options.length).toBe(0);
  });

  it('transitions cleanly from populated to empty on a subsequent load', async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce(CANNED_YEAR)
      .mockResolvedValueOnce(EMPTY_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.querySelectorAll('#yearly-totals tr').length).toBe(3);

    await controller.load();
    expect(document.querySelectorAll('#yearly-totals tr').length).toBe(0);
    expect(document.getElementById('yearly-message').hidden).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Fetch failure
// ---------------------------------------------------------------------------

describe('fetch failure', () => {
  it('shows a fixed safe message — never the raw error', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('SYNTH_SECRET_STACK_DETAIL'));
    controller = createYearly({ root: document, fetchFn });
    await controller.load();

    const msg = document.getElementById('yearly-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).not.toContain('SYNTH_SECRET_STACK_DETAIL');
    expect(msg.textContent.length).toBeGreaterThan(0);
  });

  it('does not throw', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createYearly({ root: document, fetchFn });
    await expect(controller.load()).resolves.not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// <select> change -> fetchFn(value) + re-render
// ---------------------------------------------------------------------------

describe('<select> change', () => {
  it('calls fetchFn with the selected value and re-renders', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();

    const otherYear = {
      ...CANNED_YEAR,
      y: '2025',
      prev_y: '2023',
      totals: { Groceries: '-1200.00' },
      comparison: [
        { category: 'Groceries', current: '-1200.00', previous: '-1000.00', delta: '-200.00', pct_change: 20.0 },
      ],
    };
    fetchFn.mockResolvedValueOnce(otherYear);

    const select = document.getElementById('yearly-select');
    select.value = '2025';
    select.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchFn).toHaveBeenLastCalledWith('2025');
    expect(document.querySelectorAll('#yearly-totals tr').length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Hero: legend + KPI row
// ---------------------------------------------------------------------------

describe('hero legend + KPI row', () => {
  it('renders one legend row per spend category (Income excluded from the donut)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    // Groceries + Rent are expenses; Income is excluded from the spend donut.
    const rows = document.querySelectorAll('#yearly-legend .legend-row');
    expect(rows.length).toBe(2);
  });

  it('each legend row has a dot, name, amount, pct and a share bar', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    const row = document.querySelector('#yearly-legend .legend-row');
    expect(row.querySelector('.legend-dot')).not.toBeNull();
    expect(row.querySelector('.legend-name')).not.toBeNull();
    expect(row.querySelector('.legend-amount')).not.toBeNull();
    expect(row.querySelector('.legend-pct')).not.toBeNull();
    expect(row.querySelector('.legend-bar-fill')).not.toBeNull();
  });

  it('sets the Spent, Income and Net KPI values (Income = Net + Spent)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    // Spent = 1500 + 10800 = 12300, Net = 26100, Income = 38400.
    expect(document.getElementById('yearly-kpi-spent').textContent).toContain('12,300');
    expect(document.getElementById('yearly-income').textContent).toContain('38,400');
    const netEl = document.getElementById('yearly-net');
    expect(netEl.classList.contains('net-positive')).toBe(true);
    expect(netEl.textContent).toContain('26,100');
  });

  it('clears the legend and KPI values on the empty state', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.querySelectorAll('#yearly-legend .legend-row').length).toBe(0);
    expect(document.getElementById('yearly-income').textContent).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Comparison totals footer
// ---------------------------------------------------------------------------

describe('comparison totals footer', () => {
  it('renders a Total footer row with five cells', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    const foot = document.getElementById('yearly-compare-foot');
    const cells = foot.querySelectorAll('td');
    expect(cells.length).toBe(5);
    expect(cells[0].textContent).toBe('Total');
  });

  it('clears the footer on the empty state', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    expect(document.getElementById('yearly-compare-foot').querySelectorAll('td').length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('destroys a live chart instance', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    const instance = Chart.mock.results[0].value;

    controller.destroy();
    expect(instance.destroy).toHaveBeenCalledOnce();
    controller = null;
  });

  it('removes the <select> change listener', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_YEAR);
    controller = createYearly({ root: document, fetchFn });
    await controller.load();
    controller.destroy();

    const select = document.getElementById('yearly-select');
    select.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchFn).toHaveBeenCalledTimes(1);
    controller = null;
  });

  it('is safe to call before load()', () => {
    controller = createYearly({ root: document, fetchFn: vi.fn() });
    expect(() => controller.destroy()).not.toThrow();
    controller = null;
  });
});
