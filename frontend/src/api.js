/**
 * api.js — network layer.
 * Talks to the owner's own backend only (/summary, /month, /year, /trends,
 * /search, /status, /reclassify, /category-context, /push/subscribe, /push/unsubscribe).
 * No secrets here. VITE_API_BASE is a non-secret URL (localhost / Tailscale).
 */

export const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

export class ApiError extends Error {
  constructor(message, { status = null, cause = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.cause = cause;
  }
}

/**
 * Deadline for the summary fetch. When the laptop is off but the Tailscale
 * route still exists, a fetch to it does not fail — it HANGS until the OS
 * connection timeout (60s+ on iOS), leaving the owner staring at a loading
 * state. Aborting after a short deadline turns that hang into a normal
 * network error, which the dashboard answers with the offline snapshot.
 */
const SUMMARY_TIMEOUT_MS = 5000;

/** AbortSignal.timeout where supported (iOS 16.4+); undefined elsewhere. */
function _deadline(ms) {
  return typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
    ? AbortSignal.timeout(ms)
    : undefined;
}

/**
 * Fetch the monthly summary from the backend.
 * @param {string|undefined} month  Optional 'YYYY-MM'. Omitted → backend returns latest.
 * @returns {Promise<object>}       Parsed JSON summary object.
 * @throws {ApiError}               On network failure, timeout, or non-2xx response.
 */
export async function fetchSummary(month) {
  let url = `${API_BASE}/summary`;
  if (month) {
    url += `?${new URLSearchParams({ month }).toString()}`;
  }

  let res;
  try {
    res = await fetch(url, {
      headers: { Accept: 'application/json' },
      signal: _deadline(SUMMARY_TIMEOUT_MS),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the monthly breakdown + month-over-month comparison from the backend.
 * @param {string|undefined} ym  Optional 'YYYY-MM'. Omitted → backend returns latest.
 * @returns {Promise<object>}    Parsed JSON month_view object.
 * @throws {ApiError}            On network failure or non-2xx response.
 */
export async function fetchMonth(ym) {
  let url = `${API_BASE}/month`;
  if (ym) {
    url += `?${new URLSearchParams({ ym }).toString()}`;
  }

  let res;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the yearly breakdown + year-over-year comparison from the backend.
 * @param {string|undefined} y  Optional 'YYYY'. Omitted → backend returns latest.
 * @returns {Promise<object>}   Parsed JSON year_view object.
 * @throws {ApiError}           On network failure or non-2xx response.
 */
export async function fetchYear(y) {
  let url = `${API_BASE}/year`;
  if (y) {
    url += `?${new URLSearchParams({ y }).toString()}`;
  }

  let res;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch category trends across a window of recent months from the backend.
 * @param {number|undefined} months  Optional window size (1-24). Omitted -> backend default (6).
 * @param {string|undefined} end     Optional 'YYYY-MM' window end. Omitted -> latest month.
 * @returns {Promise<object>}        Parsed JSON trends object.
 * @throws {ApiError}                On network failure or non-2xx response.
 */
export async function fetchTrends(months, end) {
  const params = new URLSearchParams();
  if (months !== undefined && months !== null) params.set('months', String(months));
  if (end) params.set('end', end);

  let url = `${API_BASE}/trends`;
  const qs = params.toString();
  if (qs) url += `?${qs}`;

  let res;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the monthly closing-balance series (net position) from the backend.
 * Reads the owner's own local backend only; balances never leave the machine.
 * @returns {Promise<object>}  Parsed JSON balance-series object.
 * @throws {ApiError}          On network failure or non-2xx response.
 */
export async function fetchBalances() {
  let res;
  try {
    res = await fetch(`${API_BASE}/balances`, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch one category's transactions for a month (dashboard drill-down).
 * Reads the owner's own local backend only; descriptions never go off-machine.
 * @param {string} category  Canonical category name, or 'Uncategorised'.
 * @param {string|undefined} month  Optional 'YYYY-MM'. Omitted → latest month.
 * @returns {Promise<{category: string, month: string|null, total: string, count: number, transactions: Array<{date: string, description: string, amount: string, bank: string}>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function fetchCategoryTransactions(category, month) {
  const params = new URLSearchParams({ category });
  if (month) params.set('month', month);
  const url = `${API_BASE}/category-transactions?${params.toString()}`;

  let res;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Full-text search over the owner's own transactions (local-only, read-only).
 * Reads the owner's own local backend only; descriptions never go off-machine.
 * @param {string} q  Free-text query.
 * @param {string|undefined} month  Optional 'YYYY-MM' filter. Omitted → all months.
 * @returns {Promise<{query: string, month: string|null, total: string, count: number, transactions: Array<{id: number, date: string, description: string, amount: string, bank: string, category: string|null}>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function fetchSearch(q, month) {
  const params = new URLSearchParams({ q });
  if (month) params.set('month', month);
  const url = `${API_BASE}/search?${params.toString()}`;

  let res;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * List internal cross-bank transfer pairs the backend has netted out of spending.
 * Reads the owner's own local backend only; descriptions never go off-machine.
 * @returns {Promise<{count: number, pairs: Array<{id: number, amount: string, created_at: string, out: object, in: object}>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function fetchTransfers() {
  let res;
  try {
    res = await fetch(`${API_BASE}/transfers`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Undo one transfer match ("Not a transfer"), restoring each leg's category.
 * Edits the owner's own local backend only.
 * @param {number} pairId  Transfer pair id (from the Transfers view).
 * @returns {Promise<{ok: boolean, pair_id: number, restored: number}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function postTransferUntag(pairId) {
  let res;
  try {
    res = await fetch(`${API_BASE}/transfers/${encodeURIComponent(pairId)}/untag`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Mark the Transfers view as seen on the owner's own local backend, clearing the
 * unseen-count nav badge. Carries no body and no transaction data.
 * @returns {Promise<{ok: boolean, last_viewed_at: string, transfers_unseen: number}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function postTransfersSeen() {
  let res;
  try {
    res = await fetch(`${API_BASE}/transfers/seen`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Apply or revert the small-fuel-stop 'Dining Out' rule for a month.
 * Edits the owner's own local backend only; returns the updated summary.
 * @param {boolean} enabled       true = apply the rule, false = revert it.
 * @param {string|undefined} month Optional 'YYYY-MM'. Omitted → latest month.
 * @returns {Promise<object>}      Updated summary object.
 * @throws {ApiError}              On network failure or non-2xx response.
 */
export async function postReclassify(enabled, month) {
  const params = new URLSearchParams({ enabled: String(Boolean(enabled)) });
  if (month) params.set('month', month);
  const url = `${API_BASE}/reclassify?${params.toString()}`;

  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Override one transaction's category (manual correction).
 * Edits the owner's own local backend only; the request carries only the row id
 * and the chosen canonical category label — no transaction description leaves the
 * client. Returns the updated month summary (same shape as GET /summary) so the
 * dashboard can re-render.
 * @param {number} id        Transaction row id (from the drill-down view).
 * @param {string} category  A canonical taxonomy label (not 'Uncategorised').
 * @returns {Promise<object>} Updated summary object.
 * @throws {ApiError}         On network failure or non-2xx response.
 */
export async function postCategoryOverride(id, category) {
  let res;
  try {
    res = await fetch(`${API_BASE}/category-override`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, category }),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the category-context screen's 9 canonical categories + stored hints.
 * @returns {Promise<{categories: Array<{name: string, color: string, hints: string, position: number}>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function fetchCategoryContext() {
  let res;
  try {
    res = await fetch(`${API_BASE}/category-context`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Replace-all of the 9 canonical categories' hints.
 * Edits the owner's own local backend only; returns the freshly-stored list.
 * Only {name, hints} travel in the request body — color/position are dropped
 * here (the backend always sources those from its own canonical seed).
 * @param {Array<{name: string, hints: string}>} categories
 * @returns {Promise<object>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function saveCategoryContext(categories) {
  const body = { categories: categories.map((c) => ({ name: c.name, hints: c.hints })) };

  let res;
  try {
    res = await fetch(`${API_BASE}/category-context`, {
      method: 'PUT',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Store the caller's own device Web Push subscription on the backend (local-only;
 * no off-machine call from this app — the backend stores it locally).
 * @param {object} subscription  PushSubscription.toJSON() shape: {endpoint, keys}.
 * @returns {Promise<object>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function postPushSubscribe(subscription) {
  let res;
  try {
    res = await fetch(`${API_BASE}/push/subscribe`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(subscription),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Remove a stored Web Push subscription on the backend by endpoint.
 * @param {string} endpoint
 * @returns {Promise<object>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function postPushUnsubscribe(endpoint) {
  let res;
  try {
    res = await fetch(`${API_BASE}/push/unsubscribe`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint }),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the owner's app settings (notification toggles + learned-corrections opt-in).
 * @returns {Promise<{corrections_enabled: boolean, notifications: Object<string, boolean>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function getSettings() {
  let res;
  try {
    res = await fetch(`${API_BASE}/settings`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Update the owner's app settings. Sends a partial patch; returns the full settings.
 * @param {{corrections_enabled?: boolean, notifications?: Object<string, boolean>}} partial
 * @returns {Promise<object>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function putSettings(partial) {
  let res;
  try {
    res = await fetch(`${API_BASE}/settings`, {
      method: 'PUT',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(partial ?? {}),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the owner's per-category monthly budgets (budgetable list + set amounts).
 * @returns {Promise<{categories: string[], budgets: Object<string, string>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function getBudgets() {
  let res;
  try {
    res = await fetch(`${API_BASE}/budgets`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the categoriser accuracy scorecard (monthly auto-categorised vs corrected).
 * LOCAL, read-only: categories + timestamps only, no transaction content.
 * @returns {Promise<{window: number, months: Array<{month: string,
 *   auto_categorised: number, corrected: number, accuracy_pct: number|null}>,
 *   current: {month: string, auto_categorised: number, corrected: number,
 *   accuracy_pct: number|null}}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function getScorecard() {
  let res;
  try {
    res = await fetch(`${API_BASE}/categoriser/scorecard`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the owner's detected recurring payments (subscriptions + regular deposits).
 * @returns {Promise<{count: number, subscriptions: Array<object>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function getSubscriptions() {
  let res;
  try {
    res = await fetch(`${API_BASE}/subscriptions`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Update the owner's monthly budgets. Sends a partial patch ({budgets: {cat: value}});
 * a null (or empty-string) value clears that category's budget. Returns the full
 * budgets in the GET shape.
 * @param {{budgets: Object<string, string|number|null>}} partial
 * @returns {Promise<{categories: string[], budgets: Object<string, string>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function putBudgets(partial) {
  let res;
  try {
    res = await fetch(`${API_BASE}/budgets`, {
      method: 'PUT',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(partial ?? {}),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the learned category corrections (the owner's own local notes).
 * @returns {Promise<{enabled: boolean, corrections: Array<{id: number, cleaned_description: string, category: string, created_at: string}>}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function getCorrections() {
  let res;
  try {
    res = await fetch(`${API_BASE}/corrections`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Remove one learned correction by id.
 * @param {number} id  Correction row id.
 * @returns {Promise<{ok: boolean, removed: number}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function deleteCorrection(id) {
  let res;
  try {
    res = await fetch(`${API_BASE}/corrections/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Fetch the categoriser health snapshot (configured flag + uncategorised count).
 * @returns {Promise<{configured: boolean, uncategorised_count: number}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function getCategoriserStatus() {
  let res;
  try {
    res = await fetch(`${API_BASE}/categoriser/status`, {
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Probe the OpenRouter categoriser (reachability / rate-limit check).
 * @returns {Promise<{configured: boolean, reachable: boolean, rate_limited: boolean, detail: string}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function postCategoriserTest() {
  let res;
  try {
    res = await fetch(`${API_BASE}/categoriser/test`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Ask the backend to retry categorising any still-uncategorised transactions.
 * @returns {Promise<{ok: boolean, categorised: number, remaining: number, detail?: string}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function postCategoriserRetry() {
  let res;
  try {
    res = await fetch(`${API_BASE}/categoriser/retry`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Wipe all stored data on the owner's own local backend. Requires the exact
 * confirmation string 'RESET'; the backend rejects anything else with a 400.
 * @param {string} confirm  Must be the literal 'RESET'.
 * @returns {Promise<{ok: boolean, cleared: object}>}
 * @throws {ApiError} On network failure or non-2xx response.
 */
export async function postReset(confirm) {
  let res;
  try {
    res = await fetch(`${API_BASE}/reset`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm }),
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('request failed', { status: res.status });
  }

  return res.json();
}

/**
 * Build the CSV backup download URL. No network call — just the URL an anchor
 * links to so the browser downloads the file directly from the local backend.
 * @returns {string}
 */
export function transactionsCsvUrl() {
  return `${API_BASE}/export/transactions.csv`;
}

/**
 * Fetch backend status (best-effort — returns null on any failure).
 * Never throws; caller can safely ignore the return value.
 * @returns {Promise<object|null>}
 */
export async function fetchStatus() {
  try {
    const res = await fetch(`${API_BASE}/status`, {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}
