/**
 * api.js — network layer.
 * Reads the owner's own backend only (/summary, /status).
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
