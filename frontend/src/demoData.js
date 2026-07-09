/**
 * demoData.js — synthetic dataset powering the public demo build (apiDemo.js).
 *
 * EVERY value in this file is invented in code — no real transactions, banks
 * figures, or merchants. A single generated ledger is the source of truth;
 * summaries, month/year views, trends, balances, search, and drill-downs are
 * all derived from it, so every screen agrees numerically.
 *
 * This module is only reachable from apiDemo.js, which is only in the module
 * graph when the build is made with VITE_DEMO=1 (see vite.config.js). Normal
 * production builds tree-shake both files away entirely.
 */

export const MONTHS = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'];
export const LATEST_YM = MONTHS[MONTHS.length - 1];

/** Descriptions the small-fuel-stop rule reclassifies Transport -> Dining Out. */
export const FUEL_SNACK_DESCRIPTIONS = new Set(['CITYFUEL EXPRESS SNACK']);

// ---------------------------------------------------------------------------
// Ledger generation — recurring synthetic merchants with per-month variation.
// Amounts are strings of cents/100; negative = spend, positive = income.
// ---------------------------------------------------------------------------

// [description, bank, category, day, [6 monthly amounts in cents]]
// A single number means "same every month".
const TEMPLATES = [
  ['BRIGHTPATH ANALYTICS SALARY', 'commbank', 'Income', 15, 418000],
  ['OAKFIELD REALTY RENT', 'commbank', 'Housing', 1, -165000],
  ['VOLTA ENERGY BILL', 'commbank', 'Housing', 18, [-9240, -10115, -8860, -7995, -8430, -9010]],
  ['GREENFIELD GROCER', 'commbank', 'Groceries', 6, [-13825, -12960, -14210, -13340, -12775, -13590]],
  ['GREENFIELD GROCER', 'commbank', 'Groceries', 20, [-11940, -12615, -11480, -12230, -12890, -11760]],
  ['MARKET LANE FRESH', 'westpac', 'Groceries', 13, [-5260, -4815, -5590, -5120, -4930, -5410]],
  ['LUNA ESPRESSO BAR', 'commbank', 'Dining Out', 9, [-2450, -2180, -2620, -2390, -2540, -2310]],
  ['SAIGON STREET KITCHEN', 'westpac', 'Dining Out', 22, [-4880, -5230, -4610, -5060, -4750, -5340]],
  ['METRO TRANSIT TAP', 'commbank', 'Transport', 11, [-4260, -3980, -4410, -4120, -4340, -4050]],
  ['CITYFUEL PETROL', 'commbank', 'Transport', 25, [-6890, -7240, -6550, -7010, -6720, -7150]],
  ['CITYFUEL EXPRESS SNACK', 'commbank', 'Transport', 27, -640],
  ['STREAMLINE PLUS', 'westpac', 'Subscriptions', 4, -1599],
  ['CLOUDNOTE PRO', 'commbank', 'Subscriptions', 8, -950],
  ['GALAXY CINEMAS', 'westpac', 'Entertainment', 16, [-3800, 0, -3800, 0, -3800, 0]],
  ['ARCADE ALLEY', 'commbank', 'Entertainment', 21, [0, -2250, 0, -2250, 0, -2250]],
  ['CORNER GIFT CO', 'westpac', 'Other', 24, [-3120, 0, -1890, -2540, 0, -3350]],
];

// An internal cross-bank transfer pair in the latest month (netted out of
// spending; shown in the Transfers view instead).
const TRANSFER_LEGS = [
  { ym: LATEST_YM, date: `${LATEST_YM}-10`, description: 'SAVINGS TOP UP', amount: '-400.00', bank: 'commbank' },
  { ym: LATEST_YM, date: `${LATEST_YM}-11`, description: 'SAVINGS TOP UP', amount: '400.00', bank: 'westpac' },
];

const _fmt = (cents) => (cents / 100).toFixed(2);

function _buildLedger() {
  const rows = [];
  let id = 1;
  MONTHS.forEach((ym, i) => {
    for (const [description, bank, category, day, amounts] of TEMPLATES) {
      const cents = Array.isArray(amounts) ? amounts[i] : amounts;
      if (!cents) continue; // 0 = merchant not visited this month
      rows.push({
        id: id++,
        ym,
        date: `${ym}-${String(day).padStart(2, '0')}`,
        description,
        amount: _fmt(cents),
        bank,
        category,
      });
    }
  });
  for (const leg of TRANSFER_LEGS) {
    rows.push({ id: id++, category: 'Transfer', ...leg });
  }
  return rows;
}

/** The full synthetic ledger. Mutable on purpose: demo category overrides edit it. */
export const LEDGER = _buildLedger();

// ---------------------------------------------------------------------------
// Derivations — everything below computes from LEDGER so the views agree.
// ---------------------------------------------------------------------------

const _cents = (s) => Math.round(Number(s) * 100);

/** Non-transfer rows for a month, with the fuel rule optionally applied. */
export function txnsFor(ym, { fuelApplied = false } = {}) {
  return LEDGER.filter((t) => t.ym === ym && t.category !== 'Transfer').map((t) => {
    if (fuelApplied && FUEL_SNACK_DESCRIPTIONS.has(t.description) && t.category === 'Transport') {
      return { ...t, category: 'Dining Out' };
    }
    return t;
  });
}

/** {category: signed string} totals for a month. */
export function totalsFor(ym, opts) {
  const sums = {};
  for (const t of txnsFor(ym, opts)) {
    sums[t.category] = (sums[t.category] ?? 0) + _cents(t.amount);
  }
  return Object.fromEntries(Object.entries(sums).map(([c, v]) => [c, _fmt(v)]));
}

function _netOf(totals) {
  return _fmt(Object.values(totals).reduce((s, v) => s + _cents(v), 0));
}

/** Closing balances per bank per month (deterministic running balances). */
export function balanceSeries() {
  const opening = { commbank: 412000, westpac: 268000 };
  const running = { ...opening };
  const series = { commbank: [], westpac: [] };
  const net = [];
  for (const ym of MONTHS) {
    for (const t of LEDGER.filter((r) => r.ym === ym)) {
      running[t.bank] += _cents(t.amount);
    }
    series.commbank.push(_fmt(running.commbank));
    // One honest gap: a month where the demo "did not import" a Westpac CSV.
    series.westpac.push(ym === '2026-02' ? null : _fmt(running.westpac));
    net.push(ym === '2026-02' ? null : _fmt(running.commbank + running.westpac));
  }
  return {
    months: [...MONTHS],
    series: [
      { bank: 'commbank', values: series.commbank },
      { bank: 'westpac', values: series.westpac },
    ],
    net,
  };
}

/** Opening/closing per bank for one month's summary card. */
export function accountBalancesFor(ym) {
  const all = balanceSeries();
  const i = all.months.indexOf(ym);
  if (i < 0) return {};
  const out = {};
  for (const s of all.series) {
    const prev = i > 0 ? s.values[i - 1] : null;
    out[s.bank] = { opening: prev, closing: s.values[i] };
  }
  return out;
}
