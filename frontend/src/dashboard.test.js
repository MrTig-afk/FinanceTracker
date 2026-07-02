/**
 * dashboard.test.js — DOM-rendering tests for dashboard.js.
 * Chart.js is mocked via vi.mock (hoisted) so module-level Chart.register
 * does not throw. jsdom provides the DOM environment. requestAnimationFrame
 * is stubbed to fire immediately with a large timestamp so the SPENT
 * count-up and legend-bar reveal land synchronously (see dashboard.js's
 * ease-out-cubic tween, which uses the rAF-supplied timestamp, not Date.now()).
 * All fixtures are synthetic, generated inline. No real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ApiError } from './api.js';
import { spendTotal, formatCurrency } from './summary.js';

// ---------------------------------------------------------------------------
// Mock chart.js BEFORE dashboard.js is imported.
// vi.mock is automatically hoisted above all imports by Vitest.
// ---------------------------------------------------------------------------

vi.mock('chart.js', () => {
  // Use a normal function (not an arrow) so it can be called with `new`:
  // Vitest 4 no longer lets an arrow-function mock implementation be constructed.
  const Chart = vi.fn(function () {
    return {
      destroy: vi.fn(),
      update: vi.fn(),
      setActiveElements: vi.fn(),
      data: { datasets: [{ borderColor: null }] },
    };
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
  <div id="sidebar-month"></div>
  <div id="net-value"></div>
  <canvas id="donut-canvas" width="200" height="200"></canvas>
  <span id="spent-total"></span>
  <div id="legend"></div>
  <span id="status-dot"></span>
  <div id="message" hidden></div>
  <button id="refresh"></button>
  <input id="fuel-rule-toggle" type="checkbox" />
  <div id="balances"></div>
`;

// ---------------------------------------------------------------------------
// Synthetic fixtures — no real transaction data.
// ---------------------------------------------------------------------------

/** Two expense categories + Income (net-positive, excluded from pie). */
const SYNTHETIC_SUMMARY = {
  year_month: '2026-06',
  totals: {
    Groceries: '-300.00',
    Transport: '-150.00',
    Income: '3000.00',
  },
  net: '-450.00',
  count: 10,
  fuel_rule_applied: false,
  fuel_rule_eligible: 3,
  fuel_rule_eligible_amount: '-24.10',
};

/** Transport + Dining Out present so the pulse test can check both. */
const PULSE_SUMMARY = {
  year_month: '2026-06',
  totals: {
    Groceries: '-100.00',
    Transport: '-40.00',
    'Dining Out': '-20.00',
  },
  net: '-160.00',
  count: 6,
  fuel_rule_applied: true,
  fuel_rule_eligible: 2,
  fuel_rule_eligible_amount: '-12.50',
};

/** All-positive / empty — drives empty state. */
const EMPTY_SUMMARY = {
  year_month: '2026-06',
  totals: {},
  net: '0.00',
  count: 0,
  fuel_rule_applied: false,
  fuel_rule_eligible: 0,
  fuel_rule_eligible_amount: '0.00',
};

/** Only Income in totals — pie/legend empty. */
const INCOME_ONLY_SUMMARY = {
  year_month: '2026-07',
  totals: { Income: '2500.00' },
  net: '2500.00',
  count: 1,
  fuel_rule_applied: false,
  fuel_rule_eligible: 0,
  fuel_rule_eligible_amount: '0.00',
};

/** Two accounts; Westpac closing undetermined. */
const BALANCES_SUMMARY = {
  ...SYNTHETIC_SUMMARY,
  account_balances: {
    commbank: { opening: '1000.00', closing: '918.10' },
    westpac: { opening: '2000.00', closing: null },
  },
};

// ---------------------------------------------------------------------------
// Setup — rebuild DOM, clear Chart mock, stub rAF before every test.
// ---------------------------------------------------------------------------

let dash;

beforeEach(() => {
  document.body.innerHTML = DOM_HTML;
  Chart.mockClear();

  // Fire the rAF callback immediately with a large timestamp so the
  // ease-out-cubic tween's `p` hits 1 on the very first (synchronous) frame.
  vi.stubGlobal('requestAnimationFrame', (cb) => {
    cb(1e12);
    return 1;
  });
  vi.stubGlobal('cancelAnimationFrame', () => {});

  dash = createDashboard(document);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// render — happy path with spending data
// ---------------------------------------------------------------------------

describe('render with summary data', () => {
  it('sets #month-label to "Spending breakdown for <Month Year>"', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(document.getElementById('month-label').textContent).toBe(
      'Spending breakdown for June 2026',
    );
  });

  it('sets #sidebar-month to "<Month Year> · CommBank + Westpac imported."', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(document.getElementById('sidebar-month').textContent).toContain('June 2026');
    expect(document.getElementById('sidebar-month').textContent).toContain('imported');
  });

  it('sets #net-value to a formatted currency string', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const text = document.getElementById('net-value').textContent;
    expect(text).toContain('$');
    expect(text).toContain('450');
  });

  it('constructs exactly one Chart instance', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(Chart).toHaveBeenCalledTimes(1);
  });

  it('hides #message when spending data is present', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(document.getElementById('message').hidden).toBe(true);
  });

  it('#spent-total equals formatCurrency(spendTotal(summary))', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(document.getElementById('spent-total').textContent).toBe(
      formatCurrency(spendTotal(SYNTHETIC_SUMMARY)),
    );
  });

  it('#legend has one .legend-row per expense category (Groceries, Transport)', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const rows = document.querySelectorAll('#legend .legend-row');
    expect(rows.length).toBe(2);
  });

  it('each legend row has a color dot, name, amount, and pct', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const rows = document.querySelectorAll('#legend .legend-row');
    rows.forEach((row) => {
      const dot = row.querySelector('.legend-dot');
      const name = row.querySelector('.legend-name');
      const amount = row.querySelector('.legend-amount');
      const pct = row.querySelector('.legend-pct');
      expect(dot.style.background).toBeTruthy();
      expect(name.textContent.length).toBeGreaterThan(0);
      expect(amount.textContent).toMatch(/^-?\$/);
      expect(pct.textContent).toMatch(/%$/);
    });
  });

  it('legend rows are ordered by magnitude DESC (Groceries before Transport)', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const names = [...document.querySelectorAll('#legend .legend-name')].map(
      (el) => el.textContent,
    );
    expect(names).toEqual(['Groceries', 'Transport']);
  });
});

// ---------------------------------------------------------------------------
// Chart tooltip disabled + donut/legend cross-highlight
// ---------------------------------------------------------------------------

describe('chart tooltip + legend cross-highlight', () => {
  it('disables the built-in Chart.js tooltip', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const config = Chart.mock.calls[0][1];
    expect(config.options.plugins.tooltip.enabled).toBe(false);
  });

  it('onHover highlights the matching legend row and clears on no-hover', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const config = Chart.mock.calls[0][1];
    const rows = document.querySelectorAll('#legend .legend-row');

    config.options.onHover({}, [{ index: 0 }]);
    expect(rows[0].classList.contains('is-hover')).toBe(true);
    expect(rows[1].classList.contains('is-hover')).toBe(false);

    config.options.onHover({}, []);
    rows.forEach((row) => expect(row.classList.contains('is-hover')).toBe(false));
  });

  it('legend mouseenter/mouseleave sets and clears active chart elements', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const instance = Chart.mock.results[0].value;
    const firstRow = document.querySelector('#legend .legend-row');

    firstRow.dispatchEvent(new Event('mouseenter'));
    expect(instance.setActiveElements).toHaveBeenCalledWith([{ datasetIndex: 0, index: 0 }]);
    expect(instance.update).toHaveBeenCalled();

    firstRow.dispatchEvent(new Event('mouseleave'));
    expect(instance.setActiveElements).toHaveBeenCalledWith([]);
  });

  // -------------------------------------------------------------------------
  // Change 1 — bidirectional symmetry (previously legend->arc did not add
  // .is-hover to the row itself; onHover->legend did). Both directions must
  // now produce the identical (arc lifted, row .is-hover) pair.
  // -------------------------------------------------------------------------

  it('legend mouseenter adds .is-hover to THAT SAME row (previously missing)', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const rows = document.querySelectorAll('#legend .legend-row');

    rows[0].dispatchEvent(new Event('mouseenter'));
    expect(rows[0].classList.contains('is-hover')).toBe(true);
    expect(rows[1].classList.contains('is-hover')).toBe(false);
  });

  it('legend mouseleave removes .is-hover from the row', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const rows = document.querySelectorAll('#legend .legend-row');

    rows[0].dispatchEvent(new Event('mouseenter'));
    expect(rows[0].classList.contains('is-hover')).toBe(true);

    rows[0].dispatchEvent(new Event('mouseleave'));
    expect(rows[0].classList.contains('is-hover')).toBe(false);
  });

  it('hovering a non-zero-index legend row lifts the matching arc index', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const instance = Chart.mock.results[0].value;
    const rows = document.querySelectorAll('#legend .legend-row');

    rows[1].dispatchEvent(new Event('mouseenter'));
    expect(instance.setActiveElements).toHaveBeenCalledWith([{ datasetIndex: 0, index: 1 }]);
    expect(rows[1].classList.contains('is-hover')).toBe(true);
    expect(rows[0].classList.contains('is-hover')).toBe(false);
  });

  it('arc onHover([{index:1}]) highlights legend row 1 (arc->legend direction)', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const config = Chart.mock.calls[0][1];
    const rows = document.querySelectorAll('#legend .legend-row');

    config.options.onHover({}, [{ index: 1 }]);
    expect(rows[1].classList.contains('is-hover')).toBe(true);
    expect(rows[0].classList.contains('is-hover')).toBe(false);
  });

  it('leaving the canvas (onHover with empty elements) clears the legend highlight', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const config = Chart.mock.calls[0][1];
    const instance = Chart.mock.results[0].value;
    const rows = document.querySelectorAll('#legend .legend-row');

    config.options.onHover({}, [{ index: 0 }]);
    expect(rows[0].classList.contains('is-hover')).toBe(true);

    config.options.onHover({}, []);
    rows.forEach((row) => expect(row.classList.contains('is-hover')).toBe(false));
    expect(instance.setActiveElements).toHaveBeenLastCalledWith([]);
  });

  it('the idx===hoveredIndex guard skips a redundant chart.update() on repeat mouseenter', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const instance = Chart.mock.results[0].value;
    const firstRow = document.querySelector('#legend .legend-row');

    firstRow.dispatchEvent(new Event('mouseenter'));
    expect(instance.update).toHaveBeenCalledTimes(1);

    // Re-entering the SAME row (already hoveredIndex 0) must not call update again.
    firstRow.dispatchEvent(new Event('mouseenter'));
    expect(instance.update).toHaveBeenCalledTimes(1);
    expect(instance.setActiveElements).toHaveBeenCalledTimes(1);
  });

  it('the guard also applies to onHover with the same arc index repeated', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const config = Chart.mock.calls[0][1];
    const instance = Chart.mock.results[0].value;

    config.options.onHover({}, [{ index: 0 }]);
    expect(instance.update).toHaveBeenCalledTimes(1);

    config.options.onHover({}, [{ index: 0 }]);
    expect(instance.update).toHaveBeenCalledTimes(1);
  });

  it('hoveredIndex resets on re-render so hovering index 0 again still updates', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const firstRowGen1 = document.querySelector('#legend .legend-row');
    firstRowGen1.dispatchEvent(new Event('mouseenter'));

    // Re-render — a fresh Chart instance + fresh legend rows + hoveredIndex reset to null.
    dash.render(SYNTHETIC_SUMMARY);
    const secondInstance = Chart.mock.results[1].value;
    const firstRowGen2 = document.querySelector('#legend .legend-row');

    firstRowGen2.dispatchEvent(new Event('mouseenter'));
    expect(secondInstance.setActiveElements).toHaveBeenCalledWith([
      { datasetIndex: 0, index: 0 },
    ]);
    expect(secondInstance.update).toHaveBeenCalledTimes(1);
    expect(firstRowGen2.classList.contains('is-hover')).toBe(true);
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

  it('#spent-total is formatCurrency(0)', () => {
    dash.render(EMPTY_SUMMARY);
    expect(document.getElementById('spent-total').textContent).toBe(formatCurrency(0));
  });

  it('#legend is empty', () => {
    dash.render(EMPTY_SUMMARY);
    expect(document.querySelectorAll('#legend .legend-row').length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// render — income-only (pie/legend empty)
// ---------------------------------------------------------------------------

describe('render with income-only summary', () => {
  it('does NOT construct a Chart (no net-expense category)', () => {
    dash.render(INCOME_ONLY_SUMMARY);
    expect(Chart).not.toHaveBeenCalled();
  });

  it('#legend has no rows (Income excluded from the donut)', () => {
    dash.render(INCOME_ONLY_SUMMARY);
    expect(document.querySelectorAll('#legend .legend-row').length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// fuel-rule toggle — reflects summary.fuel_rule_applied
// ---------------------------------------------------------------------------

describe('fuel-rule toggle state', () => {
  it('checks the toggle when fuel_rule_applied is true', () => {
    dash.render({ ...SYNTHETIC_SUMMARY, fuel_rule_applied: true });
    expect(document.getElementById('fuel-rule-toggle').checked).toBe(true);
  });

  it('unchecks the toggle when fuel_rule_applied is false', () => {
    document.getElementById('fuel-rule-toggle').checked = true;
    dash.render({ ...SYNTHETIC_SUMMARY, fuel_rule_applied: false });
    expect(document.getElementById('fuel-rule-toggle').checked).toBe(false);
  });

  it('unchecks the toggle when fuel_rule_applied is absent', () => {
    document.getElementById('fuel-rule-toggle').checked = true;
    const { fuel_rule_applied, ...rest } = SYNTHETIC_SUMMARY;
    dash.render(rest);
    expect(document.getElementById('fuel-rule-toggle').checked).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// pulse:true — transient highlight classes
// ---------------------------------------------------------------------------

describe('render(summary, { pulse: true })', () => {
  it('adds .pulse-hi to Transport and Dining Out legend rows only', () => {
    dash.render(PULSE_SUMMARY, { pulse: true });
    const rows = document.querySelectorAll('#legend .legend-row');
    const byCategory = {};
    rows.forEach((row) => {
      byCategory[row.dataset.category] = row;
    });
    expect(byCategory['Transport'].classList.contains('pulse-hi')).toBe(true);
    expect(byCategory['Dining Out'].classList.contains('pulse-hi')).toBe(true);
    expect(byCategory['Groceries'].classList.contains('pulse-hi')).toBe(false);
  });

  it('does not add pulse classes when pulse is not passed (default false)', () => {
    dash.render(PULSE_SUMMARY);
    expect(document.querySelectorAll('.pulse-hi').length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Account balances (T9) — local-only, never $0.00 in place of unavailable
// ---------------------------------------------------------------------------

describe('render populates #balances', () => {
  it('renders one .balance-row per bank with formatted opening/closing', () => {
    dash.render(BALANCES_SUMMARY);
    const rows = document.querySelectorAll('#balances .balance-row');
    expect(rows.length).toBe(2);
  });

  it('CommBank row shows formatted opening and closing figures', () => {
    dash.render(BALANCES_SUMMARY);
    const rows = document.querySelectorAll('#balances .balance-row');
    const cbRow = [...rows].find((r) =>
      r.querySelector('.balance-row-bank').textContent === 'CommBank',
    );
    const figures = cbRow.querySelector('.balance-row-figures').textContent;
    expect(figures).toContain(formatCurrency('1000.00'));
    expect(figures).toContain(formatCurrency('918.10'));
  });

  it('a null side renders — not $0.00', () => {
    dash.render(BALANCES_SUMMARY);
    const rows = document.querySelectorAll('#balances .balance-row');
    const wpRow = [...rows].find((r) =>
      r.querySelector('.balance-row-bank').textContent === 'Westpac',
    );
    const figures = wpRow.querySelector('.balance-row-figures').textContent;
    expect(figures).toContain('—');
    expect(figures).not.toContain('$0.00');
  });

  it('no account_balances -> #balances has no .balance-row elements', () => {
    dash.render(SYNTHETIC_SUMMARY);
    expect(document.querySelectorAll('#balances .balance-row').length).toBe(0);
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

  it('clears #legend', () => {
    dash.render(SYNTHETIC_SUMMARY);
    dash.showEmpty();
    expect(document.querySelectorAll('#legend .legend-row').length).toBe(0);
  });

  it('sets #spent-total to formatCurrency(0)', () => {
    dash.render(SYNTHETIC_SUMMARY);
    dash.showEmpty();
    expect(document.getElementById('spent-total').textContent).toBe(formatCurrency(0));
  });

  it('unchecks the fuel toggle', () => {
    document.getElementById('fuel-rule-toggle').checked = true;
    dash.showEmpty();
    expect(document.getElementById('fuel-rule-toggle').checked).toBe(false);
  });

  it('clears #balances', () => {
    dash.render(BALANCES_SUMMARY);
    expect(document.querySelectorAll('#balances .balance-row').length).toBeGreaterThan(0);
    dash.showEmpty();
    expect(document.getElementById('balances').textContent).toBe('');
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

    expect(firstInstance.destroy).not.toHaveBeenCalled();

    dash.render(SYNTHETIC_SUMMARY);

    expect(firstInstance.destroy).toHaveBeenCalledOnce();
  });

  it('creates two Chart instances total after two renders', () => {
    dash.render(SYNTHETIC_SUMMARY);
    dash.render(SYNTHETIC_SUMMARY);
    expect(Chart).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// applyChartTheme
// ---------------------------------------------------------------------------

describe('applyChartTheme', () => {
  it('is a no-op when no chart has been created', () => {
    expect(() => dash.applyChartTheme()).not.toThrow();
  });

  it('updates the live chart border color and calls update("none")', () => {
    dash.render(SYNTHETIC_SUMMARY);
    const instance = Chart.mock.results[0].value;
    dash.applyChartTheme();
    expect(instance.update).toHaveBeenCalledWith('none');
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
