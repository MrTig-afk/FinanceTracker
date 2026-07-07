/**
 * swHealth.js — pure, SW-globals-free logic for the periodic backend health
 * check ("is the laptop up?"). Unit-tested by swHealth.test.js; public/sw.js
 * mirrors these constants/helpers inline (kept in sync manually — same
 * convention as swRouting.js / swPush.js).
 *
 * PRIVACY: the probe target is GET /health, a fixed {"ok": true} body with no
 * store access. The device-local alert raised on failure is FIXED STATUS COPY
 * only — no dates, amounts, or any data leave or enter this path. The alert is
 * raised by the device itself (registration.showNotification), because a
 * laptop that is off cannot send a Web Push.
 */

/** periodicSync registration tag the SW listens for. */
export const PERIODIC_TAG = 'financetracker-health';

/** Reachability probe path (never-cached; see swRouting API_PATHS). */
export const HEALTH_PATH = '/health';

/** Probe deadline — same posture as the SW's short nav/asset deadlines. */
export const HEALTH_TIMEOUT_MS = 5000;

/** At most one "can't reach the laptop" alert per this interval. */
export const ALERT_MIN_INTERVAL_MS = 24 * 60 * 60 * 1000;

/**
 * Should a failure raise an alert, given the last alert timestamp (ms)?
 * Null/garbage last timestamps read as "never alerted" — alert now.
 *
 * @param {unknown} lastAlertTs
 * @param {number} now
 * @returns {boolean}
 */
export function shouldAlert(lastAlertTs, now) {
  const last = typeof lastAlertTs === 'number' && Number.isFinite(lastAlertTs)
    ? lastAlertTs
    : null;
  if (last === null) return true;
  return now - last >= ALERT_MIN_INTERVAL_MS;
}

/**
 * Arguments for registration.showNotification — fixed status-only copy.
 * The stable `tag` makes repeat alerts coalesce instead of stacking.
 *
 * @returns {{ title: string, options: object }}
 */
export function staleAlertArgs() {
  return {
    title: "Can't reach the laptop",
    options: {
      body:
        "FinanceTracker couldn't reach your laptop - data in the app may be stale.",
      icon: '/icon.svg',
      badge: '/icon.svg',
      tag: PERIODIC_TAG,
    },
  };
}

/**
 * Parse the persisted health state (JSON text) into a last-alert timestamp.
 * Tolerant: missing/corrupt/wrong-shape input -> null ("never alerted").
 *
 * @param {unknown} text
 * @returns {number | null}
 */
export function parseHealthState(text) {
  if (typeof text !== 'string' || !text) return null;
  try {
    const data = JSON.parse(text);
    const ts = data && typeof data === 'object' ? data.lastAlertTs : null;
    return typeof ts === 'number' && Number.isFinite(ts) ? ts : null;
  } catch {
    return null;
  }
}

/**
 * Serialise a last-alert timestamp for persistence.
 *
 * @param {number} ts
 * @returns {string}
 */
export function serializeHealthState(ts) {
  return JSON.stringify({ lastAlertTs: ts });
}
