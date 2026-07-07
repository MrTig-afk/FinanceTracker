/**
 * healthWatch.js — page-side registration of the SW periodic health check.
 *
 * Best-effort progressive enhancement: periodicSync exists only on Chromium
 * as an installed PWA, and the browser (not us) decides the real cadence
 * (~12h floor, engagement-gated). Everywhere else — iOS, desktop tab, denied
 * permission — every step degrades to a silent no-op. No IO beyond the
 * registration call; no data is involved.
 */

import { PERIODIC_TAG } from './swHealth.js';

/** Requested minimum interval between health probes (browser may stretch it). */
export const HEALTH_MIN_INTERVAL_MS = 12 * 60 * 60 * 1000;

/**
 * Register the periodic health check on the active SW registration.
 *
 * @param {{
 *   nav?: { serviceWorker?: { ready: Promise<object> }, permissions?: object },
 *   minIntervalMs?: number,
 * }} [options]
 * @returns {Promise<'registered' | 'unsupported' | 'failed'>}
 */
export async function initHealthWatch({
  nav = typeof navigator !== 'undefined' ? navigator : undefined,
  minIntervalMs = HEALTH_MIN_INTERVAL_MS,
} = {}) {
  if (!nav || !nav.serviceWorker || !nav.serviceWorker.ready) {
    return 'unsupported';
  }

  let registration;
  try {
    registration = await nav.serviceWorker.ready;
  } catch {
    return 'failed';
  }
  if (!registration || !('periodicSync' in registration) || !registration.periodicSync) {
    return 'unsupported';
  }

  // Permission check is advisory only: some browsers expose the query name,
  // others throw on it — either way the register() call below is the truth.
  try {
    const status = await nav.permissions?.query?.({
      name: 'periodic-background-sync',
    });
    if (status && status.state === 'denied') return 'failed';
  } catch {
    // Unknown permission name — fall through and let register() decide.
  }

  try {
    await registration.periodicSync.register(PERIODIC_TAG, {
      minInterval: minIntervalMs,
    });
    return 'registered';
  } catch {
    return 'failed';
  }
}
