/**
 * netPositionController.test.js — DOM wiring tests for netPositionController.js.
 *
 * The Net position chart is a hand-built inline SVG (no charting library), so a
 * fake `fetchFn` is injected (no real network) and the rendered SVG / legend DOM
 * is asserted directly. All fixtures are SYNTHETIC (invented amounts), never real
 * transaction data or real balances.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { createNetPosition } from './netPositionController.js';
import { formatCurrency } from './summary.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Net position markup in index.html.
// ---------------------------------------------------------------------------

const NETPOS_HTML = `
  <p id="netpos-message" class="message-banner" hidden></p>
  <section class="card netpos-card">
    <h2 class="netpos-title">Net position</h2>
    <div class="trends-layout">
      <div class="trends-chart-col">
        <svg id="netpos-chart" class="trends-svg" viewBox="0 0 900 400"></svg>
      </div>
      <div class="trends-legend-col">
        <div class="trends-legend-head">Accounts</div>
        <div class="trends-legend-list" id="netpos-legend"></div>
        <div class="trends-legend-foot"></div>
      </div>
    </div>
  </section>
`;

// ---------------------------------------------------------------------------
// Synthetic fixtures — invented amounts, never real data.
// ---------------------------------------------------------------------------

// Mirrors the spec §5 contract (Westpac missing 2026-06).
const CANNED = {
  months: ['2026-05', '2026-06', '2026-07'],
  series: [
    { bank: 'commbank', values: ['1023.10', '998.40', '1101.55'] },
    { bank: 'westpac', values: ['502.00', null, '512.13'] },
  ],
  net: ['1525.10', null, '1613.68'],
};

// Both banks fully present -> the Net line is contiguous (draws a polyline).
const CANNED_CONTIGUOUS = {
  months: ['2026-05', '2026-06', '2026-07'],
  series: [
    { bank: 'commbank', values: ['1000.00', '1010.00', '1020.00'] },
    { bank: 'westpac', values: ['500.00', '510.00', '520.00'] },
  ],
  net: ['1500.00', '1520.00', '1540.00'],
};

const EMPTY = { months: [], series: [], net: [] };

// Single bank with an interior gap (two length-1 runs).
const GAP_SINGLETONS = {
  months: ['2026-04', '2026-05', '2026-06'],
  series: [{ bank: 'commbank', values: ['100.00', null, '200.00'] }],
  net: ['100.00', null, '200.00'],
};

// Single bank with a gap that leaves two 2-point runs.
const GAP_RUNS = {
  months: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05'],
  series: [{ bank: 'commbank', values: ['100.00', '150.00', null, '200.00', '250.00'] }],
  net: ['100.00', '150.00', null, '200.00', '250.00'],
};

// A signed series (a negative closing balance) for the Y-domain test.
const SIGNED = {
  months: ['2026-05', '2026-06'],
  series: [{ bank: 'commbank', values: ['-200.00', '300.00'] }],
  net: ['-200.00', '300.00'],
};

let controller;

const $ = (id) => document.getElementById(id);
const groups = () => [...$('netpos-chart').querySelectorAll('.s-group')];
const groupFor = (name) => $('netpos-chart').querySelector(`.s-group[data-line="${name}"]`);
const rows = () => [...$('netpos-legend').querySelectorAll('.trends-legend-row')];
const rowFor = (name) => rows().find((r) => r.dataset.legend === name);

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
  document.body.innerHTML = NETPOS_HTML;
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe('empty response', () => {
  it('shows the exact build-up message and renders no chart/legend', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    const msg = $('netpos-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toBe('Balances build up from your next upload.');
    expect(groups().length).toBe(0);
    expect(rows().length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Fetch failure — fixed safe message, never the raw error
// ---------------------------------------------------------------------------

describe('fetch failure', () => {
  it('shows the exact fixed message and does not leak the raw error', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('SYNTH_SECRET_STACK_DETAIL'));
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    const msg = $('netpos-message');
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toBe('Could not load net position.');
    expect(msg.textContent).not.toContain('SYNTH_SECRET_STACK_DETAIL');
    expect(groups().length).toBe(0);
  });

  it('does not throw', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    controller = createNetPosition({ root: document, fetchFn });
    await expect(controller.load()).resolves.not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Gap rendering — no bridging, no zeros, no phantom points
// ---------------------------------------------------------------------------

describe('gap rendering', () => {
  it('two length-1 runs produce ZERO polylines and exactly 2 circles', async () => {
    const fetchFn = vi.fn().mockResolvedValue(GAP_SINGLETONS);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    const g = groupFor('CommBank');
    expect(g.querySelectorAll('polyline').length).toBe(0);
    expect(g.querySelectorAll('.s-pt').length).toBe(2);
  });

  it('two 2-point runs produce exactly 2 s-line + 2 s-hit polylines and 4 circles', async () => {
    const fetchFn = vi.fn().mockResolvedValue(GAP_RUNS);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    const g = groupFor('CommBank');
    expect(g.querySelectorAll('.s-line').length).toBe(2);
    expect(g.querySelectorAll('.s-hit').length).toBe(2);
    expect(g.querySelectorAll('.s-pt').length).toBe(4);
  });

  it('renders no point at the null month; points align to their month indices', async () => {
    const fetchFn = vi.fn().mockResolvedValue(GAP_RUNS);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    // viewBox geometry: PAD_L=58, PLOT_W=820, xDen=4 -> step 205.
    const x = (i) => String(58 + (i * 820) / 4);
    const cxs = [...groupFor('CommBank').querySelectorAll('.s-pt')].map((c) => c.getAttribute('cx'));
    expect(cxs).toEqual([x(0), x(1), x(3), x(4)]);
    // The null month (index 2) has no point.
    expect(cxs).not.toContain(x(2));
  });
});

// ---------------------------------------------------------------------------
// Net line — derived, dashed (class netline), gaps preserved
// ---------------------------------------------------------------------------

describe('net line', () => {
  it('renders a Net series whose visible line carries the netline class', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_CONTIGUOUS);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    const net = groupFor('Net');
    expect(net).not.toBeNull();
    // The visible line is 's-line netline'; the bank lines are plain 's-line'.
    expect(net.querySelector('.s-line.netline')).not.toBeNull();
    expect(groupFor('CommBank').querySelector('.netline')).toBeNull();
  });

  it('the Net line breaks at a null net month (no point there)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    // net = ['1525.10', null, '1613.68'] -> two singleton runs -> 0 polylines, 2 points.
    const net = groupFor('Net');
    expect(net.querySelectorAll('polyline').length).toBe(0);
    expect(net.querySelectorAll('.s-pt').length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Legend — order + latest-non-null value
// ---------------------------------------------------------------------------

describe('legend', () => {
  it('renders rows CommBank / Westpac / Net in order', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    expect(rows().map((r) => r.dataset.legend)).toEqual(['CommBank', 'Westpac', 'Net']);
  });

  it('shows the latest non-null value per series, currency-formatted', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    expect(rowFor('CommBank').querySelector('.trends-legend-val').textContent)
      .toBe(formatCurrency(1101.55));
    // Westpac latest non-null skips the trailing... here the last value is present.
    expect(rowFor('Westpac').querySelector('.trends-legend-val').textContent)
      .toBe(formatCurrency(512.13));
    expect(rowFor('Net').querySelector('.trends-legend-val').textContent)
      .toBe(formatCurrency(1613.68));
  });

  it('uses the last NON-null value when a series ends on a gap', async () => {
    const trailingGap = {
      months: ['2026-05', '2026-06'],
      series: [{ bank: 'commbank', values: ['777.00', null] }],
      net: ['777.00', null],
    };
    const fetchFn = vi.fn().mockResolvedValue(trailingGap);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    expect(rowFor('CommBank').querySelector('.trends-legend-val').textContent)
      .toBe(formatCurrency(777));
  });
});

// ---------------------------------------------------------------------------
// Spotlight (hover) + hide (click) — parity with the trends idiom
// ---------------------------------------------------------------------------

describe('hover spotlight', () => {
  beforeEach(async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();
  });

  it('hovering a line makes it hot and dims the others (both sides)', () => {
    hover(groupFor('CommBank'));

    expect(groupFor('CommBank').classList.contains('hot')).toBe(true);
    expect(groupFor('Westpac').classList.contains('dim')).toBe(true);
    expect(rowFor('CommBank').classList.contains('hot')).toBe(true);
    expect(rowFor('Westpac').classList.contains('dim')).toBe(true);
  });

  it('hovering a legend row highlights the matching line (bidirectional)', () => {
    hover(rowFor('Net'));

    expect(groupFor('Net').classList.contains('hot')).toBe(true);
    expect(rowFor('Net').classList.contains('hot')).toBe(true);
    expect(groupFor('CommBank').classList.contains('dim')).toBe(true);
  });

  it('mouseleave clears the spotlight', () => {
    hover(groupFor('CommBank'));
    unhover(groupFor('CommBank'));

    expect(groupFor('CommBank').classList.contains('hot')).toBe(false);
    expect(groupFor('Westpac').classList.contains('dim')).toBe(false);
  });
});

describe('click to hide / restore', () => {
  beforeEach(async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();
  });

  it('clicking a line hides just that series and marks its row off', () => {
    click(groupFor('Westpac'));

    expect(groupFor('Westpac').classList.contains('hide')).toBe(true);
    expect(rowFor('Westpac').classList.contains('off')).toBe(true);
    expect(groupFor('CommBank').classList.contains('hide')).toBe(false);
  });

  it('clicking again restores the series', () => {
    click(groupFor('Westpac'));
    click(groupFor('Westpac'));

    expect(groupFor('Westpac').classList.contains('hide')).toBe(false);
    expect(rowFor('Westpac').classList.contains('off')).toBe(false);
  });

  it('clicking a legend row also toggles hide', () => {
    click(rowFor('Net'));
    expect(groupFor('Net').classList.contains('hide')).toBe(true);
    expect(rowFor('Net').classList.contains('off')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Signed Y scale — negatives render below the zero gridline
// ---------------------------------------------------------------------------

describe('signed scale', () => {
  it('a negative value renders BELOW a positive one (larger cy)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(SIGNED);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    const pts = [...groupFor('CommBank').querySelectorAll('.s-pt')];
    const cyNeg = Number(pts[0].getAttribute('cy')); // -200.00
    const cyPos = Number(pts[1].getAttribute('cy')); // 300.00
    // SVG y grows downward, so the negative value sits lower (greater cy).
    expect(cyNeg).toBeGreaterThan(cyPos);
  });
});

// ---------------------------------------------------------------------------
// textContent-only — no HTML injection from the response
// ---------------------------------------------------------------------------

describe('no HTML injection', () => {
  it('renders series names as text, not markup', async () => {
    const injected = {
      months: ['2026-06'],
      series: [{ bank: '<img src=x onerror=alert(1)>', values: ['10.00'] }],
      net: ['10.00'],
    };
    const fetchFn = vi.fn().mockResolvedValue(injected);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();

    // No <img> element created from the injected bank key.
    expect($('netpos-legend').querySelector('img')).toBeNull();
    expect($('netpos-chart').querySelector('img')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('clears the rendered chart and legend', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    controller = createNetPosition({ root: document, fetchFn });
    await controller.load();
    expect(groups().length).toBe(3);

    controller.destroy();
    expect(groups().length).toBe(0);
    expect(rows().length).toBe(0);
    controller = null;
  });

  it('is safe to call before load()', () => {
    controller = createNetPosition({ root: document, fetchFn: vi.fn() });
    expect(() => controller.destroy()).not.toThrow();
    controller = null;
  });
});
