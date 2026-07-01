/**
 * summary.test.js — unit tests for the pure transform functions in summary.js.
 * All fixtures are synthetic, generated inline. No real transaction data.
 */

import { describe, it, expect } from 'vitest';
import {
  CATEGORY_COLORS,
  parseAmount,
  formatCurrency,
  colorFor,
  toChartData,
  categoryRows,
  computeNet,
  monthLabel,
  spendTotal,
  accountBalances,
} from './summary.js';

// ---------------------------------------------------------------------------
// Synthetic fixtures — invented inline, no real values.
// ---------------------------------------------------------------------------

const SUMMARY = {
  year_month: '2026-06',
  totals: {
    Groceries: '-123.45',
    'Dining Out': '-67.80',
    Transport: '-200.00',
    Income: '4200.00',
    Uncategorised: '-12.00',
    Subscriptions: '10.00',
  },
  net: '-1234.56',
  count: 42,
};

const EMPTY = { year_month: '2026-06', totals: {}, net: '0.00', count: 0 };

// ---------------------------------------------------------------------------
// parseAmount
// ---------------------------------------------------------------------------

describe('parseAmount', () => {
  it('parses a negative decimal string to a negative number', () => {
    expect(parseAmount('-123.45')).toBe(-123.45);
  });

  it('parses a positive decimal string', () => {
    expect(parseAmount('4200.00')).toBe(4200);
  });

  it('returns 0 for a non-numeric string — never NaN', () => {
    expect(parseAmount('abc')).toBe(0);
  });

  it('returns 0 for undefined (Number(undefined) = NaN)', () => {
    expect(parseAmount(undefined)).toBe(0);
  });

  it('returns 0 for null (Number(null) = 0 — this is already 0)', () => {
    // Number(null) === 0, which is finite, so parseAmount returns 0
    expect(parseAmount(null)).toBe(0);
  });

  it('handles a zero string', () => {
    expect(parseAmount('0.00')).toBe(0);
  });

  it('passes a raw number through unchanged when finite', () => {
    expect(parseAmount(-50.5)).toBe(-50.5);
  });
});

// ---------------------------------------------------------------------------
// formatCurrency
// ---------------------------------------------------------------------------

describe('formatCurrency', () => {
  it('formats a negative decimal string as -$123.45 (en-AU AUD)', () => {
    expect(formatCurrency('-123.45')).toBe('-$123.45');
  });

  it('formats a positive float with thousands separator and trailing zero', () => {
    expect(formatCurrency(1234.5)).toBe('$1,234.50');
  });

  it('formats numeric zero as $0.00', () => {
    expect(formatCurrency(0)).toBe('$0.00');
  });

  it('formats a large string with two levels of comma grouping', () => {
    expect(formatCurrency('1000000')).toBe('$1,000,000.00');
  });

  it('accepts a negative integer and produces -$40.00', () => {
    expect(formatCurrency(-40)).toBe('-$40.00');
  });

  it('runs string amounts through parseAmount first (non-numeric string → $0.00)', () => {
    expect(formatCurrency('not-a-number')).toBe('$0.00');
  });
});

// ---------------------------------------------------------------------------
// toChartData — the spend donut transform
// ---------------------------------------------------------------------------

describe('toChartData', () => {
  it('excludes Income from labels (case-sensitive exact match)', () => {
    const { labels } = toChartData(SUMMARY);
    expect(labels).not.toContain('Income');
  });

  it('excludes net-positive non-Income category Subscriptions (+10.00)', () => {
    const { labels } = toChartData(SUMMARY);
    expect(labels).not.toContain('Subscriptions');
  });

  it('includes Uncategorised as a normal net-expense category', () => {
    const { labels } = toChartData(SUMMARY);
    expect(labels).toContain('Uncategorised');
  });

  it('sorts labels by magnitude DESC — Transport (200) is first', () => {
    const { labels } = toChartData(SUMMARY);
    expect(labels[0]).toBe('Transport');
  });

  it('produces Transport > Groceries > Dining Out > Uncategorised order', () => {
    const { labels, values } = toChartData(SUMMARY);
    expect(labels).toEqual(['Transport', 'Groceries', 'Dining Out', 'Uncategorised']);
    expect(values[0]).toBe(200);
    expect(values[1]).toBeCloseTo(123.45);
    expect(values[2]).toBeCloseTo(67.8);
    expect(values[3]).toBe(12);
  });

  it('pie values are absolute magnitudes — all positive', () => {
    const { values } = toChartData(SUMMARY);
    values.forEach((v) => expect(v).toBeGreaterThan(0));
  });

  it('returns one colour string per label', () => {
    const { labels, colors } = toChartData(SUMMARY);
    expect(colors.length).toBe(labels.length);
    colors.forEach((c) => {
      expect(typeof c).toBe('string');
      expect(c.length).toBeGreaterThan(0);
    });
  });

  it('returns empty arrays for an EMPTY summary (fail-safe empty state)', () => {
    expect(toChartData(EMPTY)).toEqual({ labels: [], values: [], colors: [] });
  });

  it('returns empty arrays when only Income is present (all excluded)', () => {
    const incomeOnly = {
      year_month: '2026-06',
      totals: { Income: '5000.00' },
      net: '5000.00',
      count: 1,
    };
    expect(toChartData(incomeOnly)).toEqual({ labels: [], values: [], colors: [] });
  });

  it('handles an unknown category label without crashing (taxonomy drift)', () => {
    const drift = {
      year_month: '2026-06',
      totals: { FutureCategory: '-88.00' },
      net: '-88.00',
      count: 1,
    };
    const { labels, values, colors } = toChartData(drift);
    expect(labels).toEqual(['FutureCategory']);
    expect(values).toEqual([88]);
    expect(colors[0].length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// categoryRows — totals table
// ---------------------------------------------------------------------------

describe('categoryRows', () => {
  it('returns all 6 categories including Income and Subscriptions', () => {
    expect(categoryRows(SUMMARY)).toHaveLength(6);
  });

  it('places Income first — highest absolute amount (4200)', () => {
    const rows = categoryRows(SUMMARY);
    expect(rows[0].category).toBe('Income');
    expect(Math.abs(rows[0].amount)).toBe(4200);
  });

  it('every row.formatted starts with $ or -$', () => {
    const rows = categoryRows(SUMMARY);
    rows.forEach((row) => {
      expect(row.formatted).toMatch(/^-?\$/);
    });
  });

  it('every row.amount is a finite number (never NaN)', () => {
    const rows = categoryRows(SUMMARY);
    rows.forEach((row) => {
      expect(Number.isFinite(row.amount)).toBe(true);
    });
  });

  it('returns an empty array for EMPTY summary', () => {
    expect(categoryRows(EMPTY)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// computeNet
// ---------------------------------------------------------------------------

describe('computeNet', () => {
  it('returns the parsed net from summary.net (backend-authoritative)', () => {
    expect(computeNet(SUMMARY)).toBeCloseTo(-1234.56);
  });

  it('returns 0 for an empty summary', () => {
    expect(computeNet(EMPTY)).toBe(0);
  });

  it('does not re-sum from totals — trusts summary.net', () => {
    // Net differs from the sum of totals — computeNet must return summary.net.
    const custom = { ...SUMMARY, net: '-99.00' };
    expect(computeNet(custom)).toBeCloseTo(-99);
  });
});

// ---------------------------------------------------------------------------
// colorFor
// ---------------------------------------------------------------------------

describe('colorFor', () => {
  it('returns the palette hex for a known category (Groceries)', () => {
    expect(colorFor('Groceries')).toBe(CATEGORY_COLORS['Groceries']);
  });

  it('returns a non-empty string for an unknown category', () => {
    const c = colorFor('NovelLabel');
    expect(typeof c).toBe('string');
    expect(c.length).toBeGreaterThan(0);
  });

  it('is deterministic — same unknown label always returns the same colour', () => {
    expect(colorFor('NovelLabel')).toBe(colorFor('NovelLabel'));
  });

  it('different unknown labels produce different colours (hash spread)', () => {
    // Distinct labels with different char-sum hashes should differ.
    expect(colorFor('Alpha')).not.toBe(colorFor('ZZZ'));
  });

  it('CATEGORY_COLORS covers all v1 taxonomy keys', () => {
    const required = [
      'Groceries', 'Utilities', 'Rent', 'Dining Out', 'Transport',
      'Entertainment', 'Subscriptions', 'Income', 'Other', 'Uncategorised',
    ];
    required.forEach((key) => {
      expect(CATEGORY_COLORS).toHaveProperty(key);
      expect(typeof CATEGORY_COLORS[key]).toBe('string');
    });
  });
});

// ---------------------------------------------------------------------------
// spendTotal
// ---------------------------------------------------------------------------

describe('spendTotal', () => {
  it('sums the expense magnitudes (Groceries + Dining Out + Transport + Uncategorised)', () => {
    expect(spendTotal(SUMMARY)).toBeCloseTo(123.45 + 67.8 + 200 + 12);
  });

  it('excludes Income from the sum', () => {
    const incomeOnly = {
      year_month: '2026-06',
      totals: { Income: '5000.00' },
      net: '5000.00',
      count: 1,
    };
    expect(spendTotal(incomeOnly)).toBe(0);
  });

  it('returns 0 for an EMPTY summary', () => {
    expect(spendTotal(EMPTY)).toBe(0);
  });

  it('matches the sum of toChartData(summary).values', () => {
    const { values } = toChartData(SUMMARY);
    const total = values.reduce((s, v) => s + v, 0);
    expect(spendTotal(SUMMARY)).toBeCloseTo(total);
  });
});

// ---------------------------------------------------------------------------
// monthLabel
// ---------------------------------------------------------------------------

describe('monthLabel', () => {
  it('converts 2026-06 to June 2026', () => {
    expect(monthLabel('2026-06')).toBe('June 2026');
  });

  it('returns the empty string unchanged — no throw', () => {
    expect(monthLabel('')).toBe('');
  });

  it('returns a single-part string unchanged (< 2 dash-separated parts)', () => {
    expect(monthLabel('bad')).toBe('bad');
  });

  it('returns a multi-part non-numeric string unchanged (month part NaN)', () => {
    // 'bad-only-one-part' splits into ['bad', 'only', 'one', 'part'] — 'bad' is NaN
    expect(monthLabel('bad-only-one-part')).toBe('bad-only-one-part');
  });

  it('handles a different valid month — January 2026', () => {
    expect(monthLabel('2026-01')).toBe('January 2026');
  });

  it('handles December correctly', () => {
    expect(monthLabel('2025-12')).toBe('December 2025');
  });
});

// ---------------------------------------------------------------------------
// accountBalances — T8
// ---------------------------------------------------------------------------

describe('accountBalances', () => {
  it('happy path: both banks, CommBank before Westpac', () => {
    const summary = {
      account_balances: {
        westpac: { opening: '500.00', closing: '550.00' },
        commbank: { opening: '100.00', closing: '75.00' },
      },
    };
    const rows = accountBalances(summary);
    expect(rows).toEqual([
      { bank: 'commbank', label: 'CommBank', opening: 100, closing: 75 },
      { bank: 'westpac', label: 'Westpac', opening: 500, closing: 550 },
    ]);
  });

  it('missing account_balances key returns []', () => {
    expect(accountBalances({})).toEqual([]);
  });

  it('empty account_balances object returns []', () => {
    expect(accountBalances({ account_balances: {} })).toEqual([]);
  });

  it('null opening/closing pass through as null (never coerced to 0)', () => {
    const summary = {
      account_balances: { commbank: { opening: null, closing: null } },
    };
    const rows = accountBalances(summary);
    expect(rows).toEqual([
      { bank: 'commbank', label: 'CommBank', opening: null, closing: null },
    ]);
  });

  it('only one bank present -> single-entry array', () => {
    const summary = {
      account_balances: { westpac: { opening: '10.00', closing: '20.00' } },
    };
    const rows = accountBalances(summary);
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe('Westpac');
  });

  it('unknown bank key is defensively capitalised, never throws', () => {
    const summary = {
      account_balances: { revolut: { opening: '1.00', closing: '2.00' } },
    };
    const rows = accountBalances(summary);
    expect(rows).toEqual([
      { bank: 'revolut', label: 'Revolut', opening: 1, closing: 2 },
    ]);
  });
});
