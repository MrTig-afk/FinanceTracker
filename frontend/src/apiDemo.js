/**
 * apiDemo.js — drop-in replacement for api.js in the PUBLIC DEMO build.
 *
 * When the build runs with VITE_DEMO=1, vite.config.js aliases './api.js' to
 * this module, so every controller transparently talks to the synthetic
 * dataset in demoData.js instead of a backend. No network requests are made
 * anywhere in this file. Mutations behave coherently (fuel rule flips,
 * category overrides stick, transfer untag works) so the demo feels real,
 * but everything lives in page memory and resets on reload.
 *
 * MUST NOT import './api.js' (the alias would map the import onto itself).
 * Mirrors api.js's full export surface — keep the two files in sync.
 */

import {
  LEDGER,
  MONTHS,
  LATEST_YM,
  txnsFor,
  totalsFor,
  balanceSeries,
  accountBalancesFor,
  FUEL_SNACK_DESCRIPTIONS,
} from './demoData.js';
import { CATEGORY_COLORS } from './summary.js';

export const API_BASE = '';

export class ApiError extends Error {
  constructor(message, { status = null, cause = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.cause = cause;
  }
}

/** Small fixed latency so loading states render believably. */
const _delay = () => new Promise((r) => setTimeout(r, 120));

const _cents = (s) => Math.round(Number(s) * 100);
const _fmt = (cents) => (cents / 100).toFixed(2);
const _netOf = (totals) => _fmt(Object.values(totals).reduce((s, v) => s + _cents(v), 0));

// ---------------------------------------------------------------------------
// In-memory demo state (resets on reload)
// ---------------------------------------------------------------------------

const state = {
  fuelEnabled: false,
  transfersUnseen: 1,
  untaggedPairs: new Set(),
  settings: {
    corrections_enabled: true,
    notifications: Object.fromEntries(
      [
        'processed', 'processed_recovered', 'categorisation_failed',
        'categorisation_recovered', 'parse_error', 'drive_backup_failed',
        'local_backup_failed', 'duplicate_noop', 'generic_error',
        'monthly_reminder', 'budget_approaching', 'budget_exceeded',
        'transfer_detected', 'subscription_new', 'subscription_price_change',
        'income_missed',
      ].map((k) => [k, true]),
    ),
  },
  budgets: { Groceries: '450.00', 'Dining Out': '250.00' },
  corrections: [
    { id: 1, cleaned_description: 'CITYFUEL EXPRESS', category: 'Dining Out', created_at: '2026-05-14' },
    { id: 2, cleaned_description: 'ARCADE ALLEY', category: 'Entertainment', created_at: '2026-06-02' },
  ],
  hints: {
    Groceries: 'Supermarkets and fresh food markets.',
    Housing: 'Rent and household utility bills.',
    'Dining Out': 'Cafes, restaurants, takeaway.',
    Transport: 'Public transport and fuel.',
    Entertainment: 'Cinema, games, going out.',
    Subscriptions: 'Recurring digital services.',
    Income: 'Salary and other money in.',
    Other: 'Anything that fits nowhere else.',
  },
};

const BUDGETABLE = ['Groceries', 'Housing', 'Dining Out', 'Transport', 'Entertainment', 'Subscriptions', 'Other'];

function _summaryFor(ym) {
  const totals = totalsFor(ym, { fuelApplied: state.fuelEnabled });
  const eligible = LEDGER.filter(
    (t) => t.ym === ym && FUEL_SNACK_DESCRIPTIONS.has(t.description),
  );
  return {
    year_month: ym,
    totals,
    net: _netOf(totals),
    count: txnsFor(ym).length,
    fuel_rule_applied: state.fuelEnabled && eligible.length > 0,
    fuel_rule_enabled: state.fuelEnabled,
    fuel_rule_eligible: eligible.length,
    fuel_rule_eligible_amount: _fmt(eligible.reduce((s, t) => s + _cents(t.amount), 0)),
    account_balances: accountBalancesFor(ym),
    transfers_unseen: state.transfersUnseen,
  };
}

function _comparison(totals, prevTotals) {
  const cats = new Set([...Object.keys(totals), ...Object.keys(prevTotals)]);
  return [...cats]
    .map((category) => {
      const current = _cents(totals[category] ?? '0');
      const previous = _cents(prevTotals[category] ?? '0');
      const delta = current - previous;
      const pct = previous !== 0 ? Math.round((Math.abs(current) - Math.abs(previous)) / Math.abs(previous) * 1000) / 10 : null;
      return {
        category,
        current: _fmt(current),
        previous: _fmt(previous),
        delta: _fmt(delta),
        pct_change: pct,
      };
    })
    .sort((a, b) => Math.abs(_cents(b.current)) - Math.abs(_cents(a.current)));
}

// ---------------------------------------------------------------------------
// api.js surface
// ---------------------------------------------------------------------------

export async function fetchSummary(month) {
  await _delay();
  return _summaryFor(month ?? LATEST_YM);
}

export async function fetchMonth(ym) {
  await _delay();
  const target = ym ?? LATEST_YM;
  const i = MONTHS.indexOf(target);
  const prev = i > 0 ? MONTHS[i - 1] : null;
  const totals = totalsFor(target, { fuelApplied: state.fuelEnabled });
  return {
    period: 'month',
    ym: target,
    prev_ym: prev,
    totals,
    net: _netOf(totals),
    count: txnsFor(target).length,
    comparison: _comparison(totals, prev ? totalsFor(prev, { fuelApplied: state.fuelEnabled }) : {}),
    available_months: [...MONTHS].reverse(),
  };
}

export async function fetchYear(y) {
  await _delay();
  const target = y ?? '2026';
  const totals = {};
  let count = 0;
  for (const ym of MONTHS) {
    count += txnsFor(ym).length;
    for (const [cat, v] of Object.entries(totalsFor(ym, { fuelApplied: state.fuelEnabled }))) {
      totals[cat] = _fmt(_cents(totals[cat] ?? '0') + _cents(v));
    }
  }
  return {
    period: 'year',
    y: target,
    prev_y: null,
    totals,
    net: _netOf(totals),
    count,
    comparison: _comparison(totals, {}),
    available_years: ['2026'],
  };
}

export async function fetchTrends(months, end) {
  await _delay();
  const window = Math.min(Math.max(months ?? 6, 1), 24);
  const endYm = end && MONTHS.includes(end) ? end : LATEST_YM;
  const endIdx = MONTHS.indexOf(endYm);
  const startIdx = Math.max(0, endIdx - window + 1);
  const win = MONTHS.slice(startIdx, endIdx + 1);
  const cats = new Set();
  win.forEach((ym) => Object.keys(totalsFor(ym)).forEach((c) => cats.add(c)));
  const series = [...cats].map((category) => ({
    category,
    values: win.map((ym) => totalsFor(ym, { fuelApplied: state.fuelEnabled })[category] ?? '0.00'),
  }));
  const spend_by_month = win.map((ym) => {
    const totals = totalsFor(ym, { fuelApplied: state.fuelEnabled });
    const spend = Object.entries(totals)
      .filter(([c, v]) => c !== 'Income' && _cents(v) < 0)
      .reduce((s, [, v]) => s + Math.abs(_cents(v)), 0);
    return _fmt(spend);
  });
  return {
    window,
    end_month: endYm,
    months: win,
    series,
    spend_by_month,
    months_available: MONTHS.length,
  };
}

export async function fetchBalances() {
  await _delay();
  return balanceSeries();
}

export async function fetchCategoryTransactions(category, month) {
  await _delay();
  const ym = month ?? LATEST_YM;
  const txns = txnsFor(ym, { fuelApplied: state.fuelEnabled })
    .filter((t) => t.category === category)
    .map(({ id, date, description, amount, bank }) => ({ id, date, description, amount, bank }));
  return {
    category,
    month: ym,
    total: _fmt(txns.reduce((s, t) => s + _cents(t.amount), 0)),
    count: txns.length,
    transactions: txns,
  };
}

export async function fetchSearch(q, month) {
  await _delay();
  const needle = (q ?? '').trim().toLowerCase();
  const rows = LEDGER.filter((t) => {
    if (month && t.ym !== month) return false;
    if (!needle) return false;
    return (
      t.description.toLowerCase().includes(needle) ||
      (t.category ?? '').toLowerCase().includes(needle)
    );
  }).map(({ id, date, description, amount, bank, category }) => ({
    id, date, description, amount, bank, category,
  }));
  return {
    query: q,
    month: month ?? null,
    total: _fmt(rows.reduce((s, t) => s + _cents(t.amount), 0)),
    count: rows.length,
    transactions: rows,
  };
}

export async function fetchTransfers() {
  await _delay();
  const legs = LEDGER.filter((t) => t.category === 'Transfer');
  const pairs = [];
  if (legs.length === 2 && !state.untaggedPairs.has(1)) {
    const [out, inn] = legs[0].amount.startsWith('-') ? [legs[0], legs[1]] : [legs[1], legs[0]];
    pairs.push({
      id: 1,
      amount: inn.amount,
      created_at: `${LATEST_YM}-12T09:00:00`,
      out: { id: out.id, date: out.date, description: out.description, amount: out.amount, bank: out.bank },
      in: { id: inn.id, date: inn.date, description: inn.description, amount: inn.amount, bank: inn.bank },
    });
  }
  return { count: pairs.length, pairs };
}

export async function postTransferUntag(pairId) {
  await _delay();
  state.untaggedPairs.add(pairId);
  for (const leg of LEDGER.filter((t) => t.category === 'Transfer')) {
    leg.category = 'Other';
  }
  return { ok: true, pair_id: pairId, restored: 2 };
}

export async function postTransfersSeen() {
  await _delay();
  state.transfersUnseen = 0;
  return { ok: true, last_viewed_at: new Date().toISOString(), transfers_unseen: 0 };
}

export async function postReclassify(enabled, month) {
  await _delay();
  state.fuelEnabled = Boolean(enabled);
  return _summaryFor(month ?? LATEST_YM);
}

export async function postCategoryOverride(id, category) {
  await _delay();
  const row = LEDGER.find((t) => t.id === id);
  if (row) row.category = category;
  return _summaryFor(LATEST_YM);
}

export async function fetchCategoryContext() {
  await _delay();
  return {
    categories: Object.keys(state.hints).map((name, i) => ({
      name,
      color: CATEGORY_COLORS[name] ?? '#9E9E9E',
      hints: state.hints[name],
      position: i,
    })),
  };
}

export async function saveCategoryContext(categories) {
  await _delay();
  for (const c of categories) {
    if (c.name in state.hints) state.hints[c.name] = c.hints;
  }
  return fetchCategoryContext();
}

export async function postPushSubscribe() {
  await _delay();
  return { ok: true };
}

export async function postPushUnsubscribe() {
  await _delay();
  return { ok: true, removed: 0 };
}

export async function getSettings() {
  await _delay();
  return JSON.parse(JSON.stringify(state.settings));
}

export async function putSettings(partial) {
  await _delay();
  if (partial?.corrections_enabled !== undefined) {
    state.settings.corrections_enabled = Boolean(partial.corrections_enabled);
  }
  for (const [k, v] of Object.entries(partial?.notifications ?? {})) {
    if (k in state.settings.notifications) state.settings.notifications[k] = Boolean(v);
  }
  return getSettings();
}

export async function getBudgets() {
  await _delay();
  return { categories: [...BUDGETABLE], budgets: { ...state.budgets } };
}

export async function putBudgets(partial) {
  await _delay();
  for (const [cat, v] of Object.entries(partial?.budgets ?? {})) {
    if (v === null || v === '') delete state.budgets[cat];
    else state.budgets[cat] = _fmt(_cents(v));
  }
  return getBudgets();
}

export async function getScorecard() {
  await _delay();
  const months = MONTHS.map((month, i) => ({
    month,
    auto_categorised: 14,
    corrected: [2, 2, 1, 1, 1, 0][i],
    accuracy_pct: [86.7, 86.7, 93.3, 93.3, 93.3, 100][i],
  }));
  return { window: 6, months, current: months[months.length - 1] };
}

export async function getSubscriptions() {
  await _delay();
  const subscriptions = [
    { merchant: 'STREAMLINE PLUS', direction: 'expense', amount: '15.99', first_seen_month: MONTHS[0], last_seen_month: LATEST_YM, status: 'active' },
    { merchant: 'CLOUDNOTE PRO', direction: 'expense', amount: '9.50', first_seen_month: MONTHS[0], last_seen_month: LATEST_YM, status: 'active' },
    { merchant: 'BRIGHTPATH ANALYTICS SALARY', direction: 'income', amount: '4180.00', first_seen_month: MONTHS[0], last_seen_month: LATEST_YM, status: 'active' },
  ];
  return { count: subscriptions.length, subscriptions };
}

export async function getCorrections() {
  await _delay();
  return { enabled: state.settings.corrections_enabled, corrections: [...state.corrections] };
}

export async function deleteCorrection(id) {
  await _delay();
  const before = state.corrections.length;
  state.corrections = state.corrections.filter((c) => c.id !== id);
  return { ok: true, removed: before - state.corrections.length };
}

export async function getCategoriserStatus() {
  await _delay();
  return { configured: true, uncategorised_count: 0 };
}

export async function postCategoriserTest() {
  await _delay();
  return { configured: true, reachable: true, rate_limited: false, detail: 'Demo mode - no live call made.' };
}

export async function postCategoriserRetry() {
  await _delay();
  return { ok: true, categorised: 0, remaining: 0 };
}

export async function postReset() {
  await _delay();
  return { ok: true, cleared: {} };
}

export function transactionsCsvUrl() {
  const header = 'date,description,amount,bank,category';
  const rows = LEDGER.map(
    (t) => `${t.date},${t.description},${t.amount},${t.bank},${t.category}`,
  );
  return `data:text/csv;charset=utf-8,${encodeURIComponent([header, ...rows].join('\n'))}`;
}

export async function fetchStatus() {
  await _delay();
  return {
    status: 'ok',
    uptime_seconds: 3600,
    last_run_at: `${LATEST_YM}-30T09:00:00Z`,
    last_run: null,
    configured: { drive: false, openrouter: true },
  };
}
