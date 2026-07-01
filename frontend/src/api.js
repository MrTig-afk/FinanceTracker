/**
 * api.js — network layer.
 * Talks to the owner's own backend only (/summary, /status, /reclassify,
 * /category-context). No secrets here. VITE_API_BASE is a non-secret URL
 * (localhost / Tailscale).
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
 * Fetch the monthly summary from the backend.
 * @param {string|undefined} month  Optional 'YYYY-MM'. Omitted → backend returns latest.
 * @returns {Promise<object>}       Parsed JSON summary object.
 * @throws {ApiError}               On network failure or non-2xx response.
 */
export async function fetchSummary(month) {
  let url = `${API_BASE}/summary`;
  if (month) {
    url += `?${new URLSearchParams({ month }).toString()}`;
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
