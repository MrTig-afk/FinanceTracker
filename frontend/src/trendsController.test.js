/**
 * trendsController.test.js — DOM wiring tests for trendsController.js.
 *
 * The Trends chart is a hand-built inline SVG (no charting library), so there
 * is nothing to mock — a fake `fetchFn` is injected (no real network) and the
 * rendered SVG / legend DOM is asserted directly. All fixtures are SYNTHETIC
 * (invented categories/amounts), never real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { vi } from 'vitest';

import { createTrends } from './trendsController.js';
import { colorFor, formatCurrency } from './summary.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Trends view markup in index.html.
// ---------------------------------------------------------------------------

const TRENDS_HTML = `
  <select id="trends-window"></select>
  <p id="trends-message" hidden></p>
  <section class="card trends-card">
    <div class="trends-layout">
      <div class="trends-chart-col">
        <svg id="trends-chart" class="trends-svg" viewBox="0 0 900 400"></svg>
      </div>
      <div class="trends-legend-col">
        <div class="trends-legend-head">Categories</div>
        <div class="trends-legend-list" id="trends-legend"></div>
        <div class="trends-legend-foot"></div>
      </div>
    </div>
  </section>
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
  series: [{ category: 'Groceries', values: ['0.00', '0.00', '0.00', '0.00', '0.00', '-50.00'] }],
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
// excluding Income) — must also show the empty state, WITHOUT rendering lines.
const ALL_ZERO_TRENDS = {
  window: 3,
  end_month: '2026-06',
  months: ['2026-04', '2026-05', '2026-06'],
  series: [{ category: 'Groceries', values: ['0.00', '0.00', '0.00'] }],
  spend_by_month: ['0.00', '0.00', '0.00'],
  months_available: 3,
};

const _NOT_ENOUGH_HISTORY = 'Not enough history yet. Upload at least two months to see trends.';

let controller;

const $ = (id) => document.getElementById(id);
const groups = () => [...$('trends-chart').querySelectorAll('.s-group')];
const groupFor = (name) => $('trends-chart').querySelector(`.s-group[data-line="${name}"]`);
const rows = () => [...$('trends-legend').querySelectorAll('.trends-legend-row')];
const rowFor = (name) =>
  rows().find((r) => r.dataset.legend === name);

function hover(el) {
  el.dispatchEvent(new Event('mouseenter'));
}
function unhover(el) {
  el.dispatchEvent(new Event('mouseleave'));
}
function click(el) {
  el.dispatchEvent(new Event('click'));
}

beforeEach(() => {
  document.body.innerHTML = TRENDS_HTML;
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

  it('hides the message banner', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect($('trends-message').hidden).toBe(true);
  });

  it('renders one series group per NON-Income series (excludes Income)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const names = groups().map((g) => g.dataset.line);
    expect(names).toEqual(['Groceries', 'Transport']);
    expect(names).not.toContain('Income');
  });

  it('renders one legend row per NON-Income series', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const names = rows().map((r) => r.dataset.legend);
    expect(names).toEqual(['Groceries', 'Transport']);
  });

  it('colours each series line with colorFor(category)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const line = groupFor('Groceries').querySelector('.s-line');
    expect(line.getAttribute('stroke')).toBe(colorFor('Groceries'));
  });

  it('legend value shows the latest-month magnitude, currency-formatted', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    // Groceries latest value is '-92.00' -> abs 92 -> formatted.
    const val = rowFor('Groceries').querySelector('.trends-legend-val');
    expect(val.textContent).toBe(formatCurrency(92));
  });

  it('draws one point circle per month for each series', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    expect(groupFor('Groceries').querySelectorAll('.s-pt').length).toBe(6);
  });

  it('draws 5 gridlines and mono y-axis labels', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const svg = $('trends-chart');
    expect(svg.querySelectorAll('.grid-line').length).toBe(5);
    expect(svg.querySelectorAll('.axis-label').length).toBe(5);
  });

  it('renders x-axis labels as month abbreviations', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const labels = [...$('trends-chart').querySelectorAll('.x-label')].map((t) => t.textContent);
    expect(labels.length).toBe(6);
    // 'Jan' style abbreviation (locale-derived, matches the controller helper).
    expect(labels[0]).toBe(new Date(2026, 0, 1).toLocaleString('en-AU', { month: 'short' }));
  });

  it('populates the window <select> with [3, 6, 12, 24] labelled "N months"', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    const options = [...$('trends-window').options];
    expect(options.map((o) => o.value)).toEqual(['3', '6', '12', '24']);
    expect(options.map((o) => o.textContent)).toEqual(['3 months', '6 months', '12 months', '24 months']);
  });

  it('default-selects the response window', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect($('trends-window').value).toBe('6');
  });

  it('does not re-populate the <select> on a second load', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    await controller.load();
    expect($('trends-window').options.length).toBe(4);
  });
});

// ---------------------------------------------------------------------------
// Insufficient-history / empty states
// ---------------------------------------------------------------------------

describe('insufficient history (months_available <= 1)', () => {
  it('renders no series and shows the exact insufficient-history message', async () => {
    const fetchFn = vi.fn().mockResolvedValue(SINGLE_MONTH_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    expect(groups().length).toBe(0);
    const msg = $('trends-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toBe(_NOT_ENOUGH_HISTORY);
  });
});

describe('empty response (months.length === 0)', () => {
  it('renders no series and shows the exact insufficient-history message', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    expect(groups().length).toBe(0);
    expect($('trends-message').textContent).toBe(_NOT_ENOUGH_HISTORY);
  });
});

describe('all-zero datasets (populated window, every value zero)', () => {
  it('renders no series and shows the exact insufficient-history message', async () => {
    const fetchFn = vi.fn().mockResolvedValue(ALL_ZERO_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();

    expect(groups().length).toBe(0);
    expect($('trends-message').textContent).toBe(_NOT_ENOUGH_HISTORY);
  });

  it('clears a previously-rendered chart when transitioning to all-zero', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(CANNED_TRENDS)
      .mockResolvedValueOnce(ALL_ZERO_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(groups().length).toBe(2);

    await controller.load();
    expect(groups().length).toBe(0);
    expect(rows().length).toBe(0);
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

    const msg = $('trends-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toBe('Could not load trends.');
    expect(msg.textContent).not.toContain('SYNTH_SECRET_STACK_DETAIL');
  });

  it('does not throw', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createTrends({ root: document, fetchFn });
    await expect(controller.load()).resolves.not.toThrow();
  });

  it('renders no series', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    expect(groups().length).toBe(0);
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

    fetchFn.mockResolvedValueOnce({ ...CANNED_TRENDS, window: 12 });

    const select = $('trends-window');
    select.value = '12';
    select.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchFn).toHaveBeenLastCalledWith(12);
  });
});

// ---------------------------------------------------------------------------
// Spotlight (hover) — bidirectional line <-> legend highlight
// ---------------------------------------------------------------------------

describe('hover spotlight', () => {
  beforeEach(async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
  });

  it('hovering a line makes it hot and dims the others (both sides)', () => {
    hover(groupFor('Groceries'));

    expect(groupFor('Groceries').classList.contains('hot')).toBe(true);
    expect(groupFor('Transport').classList.contains('dim')).toBe(true);
    expect(rowFor('Groceries').classList.contains('hot')).toBe(true);
    expect(rowFor('Transport').classList.contains('dim')).toBe(true);
  });

  it('mouseleave clears the spotlight', () => {
    hover(groupFor('Groceries'));
    unhover(groupFor('Groceries'));

    expect(groupFor('Groceries').classList.contains('hot')).toBe(false);
    expect(groupFor('Transport').classList.contains('dim')).toBe(false);
    expect(rowFor('Transport').classList.contains('dim')).toBe(false);
  });

  it('hovering a legend row highlights the matching line (bidirectional)', () => {
    hover(rowFor('Transport'));

    expect(groupFor('Transport').classList.contains('hot')).toBe(true);
    expect(rowFor('Transport').classList.contains('hot')).toBe(true);
    expect(groupFor('Groceries').classList.contains('dim')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Click-to-hide + restore (per-series `hidden` set)
// ---------------------------------------------------------------------------

describe('click to hide / restore', () => {
  beforeEach(async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
  });

  it('clicking a line hides just that series and marks its row off', () => {
    click(groupFor('Groceries'));

    expect(groupFor('Groceries').classList.contains('hide')).toBe(true);
    expect(rowFor('Groceries').classList.contains('off')).toBe(true);
    // The other series stays put.
    expect(groupFor('Transport').classList.contains('hide')).toBe(false);
    expect(rowFor('Transport').classList.contains('off')).toBe(false);
  });

  it('clicking again restores the series', () => {
    click(groupFor('Groceries'));
    click(groupFor('Groceries'));

    expect(groupFor('Groceries').classList.contains('hide')).toBe(false);
    expect(rowFor('Groceries').classList.contains('off')).toBe(false);
  });

  it('clicking a legend row also toggles hide', () => {
    click(rowFor('Transport'));
    expect(groupFor('Transport').classList.contains('hide')).toBe(true);
    expect(rowFor('Transport').classList.contains('off')).toBe(true);
  });

  it('multiple series can be hidden independently', () => {
    click(groupFor('Groceries'));
    click(rowFor('Transport'));

    expect(groupFor('Groceries').classList.contains('hide')).toBe(true);
    expect(groupFor('Transport').classList.contains('hide')).toBe(true);
  });

  it('hovering a hidden row does not spotlight it', () => {
    click(rowFor('Groceries'));
    hover(rowFor('Groceries'));

    expect(groupFor('Groceries').classList.contains('hot')).toBe(false);
    expect(rowFor('Groceries').classList.contains('hot')).toBe(false);
    // Visible series is not dimmed by a hover on a hidden row.
    expect(groupFor('Transport').classList.contains('dim')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('clears the rendered chart and legend', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    controller.destroy();

    expect(groups().length).toBe(0);
    expect(rows().length).toBe(0);
    controller = null;
  });

  it('removes the <select> change listener', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRENDS);
    controller = createTrends({ root: document, fetchFn });
    await controller.load();
    controller.destroy();

    const select = $('trends-window');
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
