/**
 * summary.js — PURE data transforms.
 * No DOM, no network, no Chart.js. This module is the primary unit-test target.
 *
 * Rules (from decisions.md §7.9):
 *  - Income is excluded from the spend donut but kept in the totals table.
 *  - Pie value = absolute magnitude of each non-Income category whose total is
 *    net-negative (expense). Net-positive non-Income categories are excluded
 *    from the pie but kept in the totals table.
 *  - Uncategorised is a normal spend category (no special-casing beyond Income).
 *  - Net is taken straight from summary.net — never recomputed client-side.
 */

/** Fixed colour palette for the v1 taxonomy (matches the design mockup COLORS). */
export const CATEGORY_COLORS = {
  Groceries: '#57b26f',
  Utilities: '#4a90d9',
  Rent: '#9b6cd4',
  'Dining Out': '#e0913f',
  Transport: '#34a7a3',
  Entertainment: '#d96ba6',
  Subscriptions: '#6f6bd8',
  Other: '#a89f8c',
  Income: '#8BC34A',
  Uncategorised: '#9E9E9E',
};

/**
 * A small fallback palette used when a category name is not in CATEGORY_COLORS.
 * Kept distinct from the main palette to avoid collisions.
 */
const _FALLBACK_PALETTE = [
  '#F44336', '#FF5722', '#FFC107', '#CDDC39', '#009688',
  '#00BCD4', '#3F51B5', '#9C27B0', '#795548', '#607D8B',
];

/**
 * Parse an amount string (or number) to a finite number.
 * Treats any non-finite result as 0 — never returns NaN into Chart.js.
 * @param {string|number} amountStr
 * @returns {number}
 */
export function parseAmount(amountStr) {
  const n = Number(amountStr);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Format a value as Australian dollar currency.
 * Accepts both string and number input (passes through parseAmount first).
 * Examples: '-123.45' → '-$123.45', 1234.5 → '$1,234.50', 0 → '$0.00'.
 * @param {string|number} amount
 * @returns {string}
 */
export function formatCurrency(amount) {
  const n = parseAmount(amount);
  return new Intl.NumberFormat('en-AU', {
    style: 'currency',
    currency: 'AUD',
  }).format(n);
}

/**
 * Return a colour for the given category label.
 * Canonical names hit the fixed palette; unknown names get a deterministic
 * HSL colour derived from the sum of their char codes (stable across calls).
 * @param {string} category
 * @returns {string}  Hex colour or HSL string.
 */
export function colorFor(category) {
  if (Object.prototype.hasOwnProperty.call(CATEGORY_COLORS, category)) {
    return CATEGORY_COLORS[category];
  }
  // Deterministic fallback: sum char codes → hsl(angle, 65%, 55%)
  let hash = 0;
  for (let i = 0; i < category.length; i++) {
    hash += category.charCodeAt(i);
  }
  return `hsl(${hash % 360}, 65%, 55%)`;
}

/**
 * Transform a summary object into Chart.js-ready data for the spend donut.
 *
 * Rules:
 *  1. Exclude 'Income' (exact, case-sensitive).
 *  2. For each remaining category, compute v = parseAmount(value).
 *     Include only if v < 0 (net expense). v >= 0 → excluded from pie.
 *  3. Pie value = Math.abs(v).
 *  4. Sort included entries by magnitude DESC.
 *  5. Return { labels, values, colors }.
 *  6. Empty / all-excluded → { labels: [], values: [], colors: [] }.
 *
 * @param {{ totals: Record<string,string> }} summary
 * @returns {{ labels: string[], values: number[], colors: string[] }}
 */
export function toChartData(summary) {
  const totals = summary.totals ?? {};

  const entries = [];
  for (const [category, rawValue] of Object.entries(totals)) {
    if (category === 'Income') continue;           // rule 1
    const v = parseAmount(rawValue);
    if (v >= 0) continue;                          // rule 2: only net expenses
    entries.push({ category, magnitude: Math.abs(v) });
  }

  // Sort by magnitude DESC
  entries.sort((a, b) => b.magnitude - a.magnitude);

  const labels = entries.map((e) => e.category);
  const values = entries.map((e) => e.magnitude);
  const colors = entries.map((e) => colorFor(e.category));

  return { labels, values, colors };
}

/**
 * Build sorted rows for the totals table.
 * Includes ALL categories (Income, Uncategorised, spend, etc.).
 * Ordered by absolute amount DESC so the biggest movers appear first.
 *
 * @param {{ totals: Record<string,string> }} summary
 * @returns {Array<{ category: string, amount: number, formatted: string }>}
 */
export function categoryRows(summary) {
  const totals = summary.totals ?? {};

  const rows = Object.entries(totals).map(([category, rawValue]) => {
    const amount = parseAmount(rawValue);
    return { category, amount, formatted: formatCurrency(rawValue) };
  });

  // Sort by absolute amount DESC
  rows.sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));

  return rows;
}

/**
 * Sum of the spend-donut magnitudes — the "SPENT" count-up target.
 * Pure passthrough of toChartData(summary).values so the donut, legend
 * percentages, and the SPENT figure all agree on the same total.
 * @param {{ totals: Record<string,string> }} summary
 * @returns {number}  A non-negative number; 0 when there is nothing to chart.
 */
export function spendTotal(summary) {
  const { values } = toChartData(summary);
  return values.reduce((sum, v) => sum + v, 0);
}

/**
 * Extract the authoritative net figure from the summary.
 * Never recomputes from individual totals — the backend owns this value.
 * @param {{ net: string }} summary
 * @returns {number}
 */
export function computeNet(summary) {
  return parseAmount(summary.net);
}

/**
 * Convert a 'YYYY-MM' string to a human-readable month label.
 * Example: '2026-06' → 'June 2026'.
 * Malformed or empty input is returned unchanged (no throw).
 * @param {string} yearMonth
 * @returns {string}
 */
export function monthLabel(yearMonth) {
  if (!yearMonth || typeof yearMonth !== 'string') {
    return String(yearMonth ?? '');
  }

  const parts = yearMonth.split('-');
  if (parts.length < 2) return yearMonth;

  const y = Number(parts[0]);
  const m = Number(parts[1]);
  if (!Number.isFinite(y) || !Number.isFinite(m) || m < 1 || m > 12) {
    return yearMonth;
  }

  const date = new Date(y, m - 1, 1);
  return date.toLocaleString('en-AU', { month: 'long', year: 'numeric' });
}
