/**
 * swHealth.test.js — unit tests for swHealth.js.
 * Pure module, no SW environment needed. Privacy-critical assertion: the
 * device-local "can't reach the laptop" alert copy is FIXED status text —
 * nothing dynamic (dates, counts, amounts) can ever appear in it.
 */

import { describe, it, expect } from 'vitest';
import {
  ALERT_MIN_INTERVAL_MS,
  HEALTH_PATH,
  PERIODIC_TAG,
  parseHealthState,
  serializeHealthState,
  shouldAlert,
  staleAlertArgs,
} from './swHealth.js';

const NOW = 1_750_000_000_000; // synthetic fixed "now" (ms)

// ---------------------------------------------------------------------------
// shouldAlert — 24h dedupe boundaries
// ---------------------------------------------------------------------------

describe('shouldAlert', () => {
  it('alerts when there is no previous alert (null)', () => {
    expect(shouldAlert(null, NOW)).toBe(true);
  });

  it('alerts on a garbage previous timestamp (string, NaN, Infinity)', () => {
    expect(shouldAlert('yesterday', NOW)).toBe(true);
    expect(shouldAlert(NaN, NOW)).toBe(true);
    expect(shouldAlert(Infinity, NOW)).toBe(true);
  });

  it('does NOT alert again within the 24h window', () => {
    expect(shouldAlert(NOW - ALERT_MIN_INTERVAL_MS + 1, NOW)).toBe(false);
    expect(shouldAlert(NOW - 1000, NOW)).toBe(false);
  });

  it('alerts again at exactly the 24h boundary and beyond', () => {
    expect(shouldAlert(NOW - ALERT_MIN_INTERVAL_MS, NOW)).toBe(true);
    expect(shouldAlert(NOW - ALERT_MIN_INTERVAL_MS - 1, NOW)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// staleAlertArgs — fixed status-only copy
// ---------------------------------------------------------------------------

describe('staleAlertArgs', () => {
  it('uses fixed copy with no digits (nothing dynamic can leak)', () => {
    const { title, options } = staleAlertArgs();
    expect(title).toBe("Can't reach the laptop");
    for (const text of [title, options.body]) {
      expect([...text].some((ch) => /\d/.test(ch))).toBe(false);
    }
  });

  it('tags the notification with the periodic tag so repeats coalesce', () => {
    expect(staleAlertArgs().options.tag).toBe(PERIODIC_TAG);
  });

  it('is stable across calls (pure)', () => {
    expect(staleAlertArgs()).toEqual(staleAlertArgs());
  });
});

// ---------------------------------------------------------------------------
// health state round-trip
// ---------------------------------------------------------------------------

describe('health state serialize/parse', () => {
  it('round-trips a timestamp', () => {
    expect(parseHealthState(serializeHealthState(NOW))).toBe(NOW);
  });

  it('tolerates missing/corrupt/wrong-shape input as "never alerted"', () => {
    expect(parseHealthState(null)).toBeNull();
    expect(parseHealthState('')).toBeNull();
    expect(parseHealthState('{not json')).toBeNull();
    expect(parseHealthState(JSON.stringify({ lastAlertTs: 'soon' }))).toBeNull();
    expect(parseHealthState(JSON.stringify([NOW]))).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// constants — contract with the SW mirror and the never-cache list
// ---------------------------------------------------------------------------

describe('constants', () => {
  it('probes /health (which swRouting keeps network-only)', () => {
    expect(HEALTH_PATH).toBe('/health');
  });

  it('uses the financetracker-health periodicSync tag', () => {
    expect(PERIODIC_TAG).toBe('financetracker-health');
  });
});
