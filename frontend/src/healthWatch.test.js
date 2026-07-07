/**
 * healthWatch.test.js — unit tests for healthWatch.js.
 * No real service worker: nav.serviceWorker.ready is faked with stub
 * registration objects. Asserts the progressive-enhancement contract —
 * register when supported, silent no-op ('unsupported'/'failed') everywhere
 * else, and NEVER a thrown error.
 */

import { describe, it, expect, vi } from 'vitest';
import { HEALTH_MIN_INTERVAL_MS, initHealthWatch } from './healthWatch.js';
import { PERIODIC_TAG } from './swHealth.js';

function navWith(registration, permissions) {
  return {
    serviceWorker: { ready: Promise.resolve(registration) },
    permissions,
  };
}

describe('initHealthWatch', () => {
  it('registers the periodic sync with the health tag and min interval', async () => {
    const register = vi.fn().mockResolvedValue(undefined);
    const nav = navWith({ periodicSync: { register } });

    const result = await initHealthWatch({ nav });

    expect(result).toBe('registered');
    expect(register).toHaveBeenCalledExactlyOnceWith(PERIODIC_TAG, {
      minInterval: HEALTH_MIN_INTERVAL_MS,
    });
  });

  it('honours a custom minIntervalMs', async () => {
    const register = vi.fn().mockResolvedValue(undefined);
    const nav = navWith({ periodicSync: { register } });

    await initHealthWatch({ nav, minIntervalMs: 1234 });

    expect(register).toHaveBeenCalledWith(PERIODIC_TAG, { minInterval: 1234 });
  });

  it('returns "unsupported" when the registration has no periodicSync', async () => {
    const result = await initHealthWatch({ nav: navWith({}) });
    expect(result).toBe('unsupported');
  });

  it('returns "unsupported" when serviceWorker itself is missing', async () => {
    expect(await initHealthWatch({ nav: {} })).toBe('unsupported');
    expect(await initHealthWatch({ nav: undefined })).toBe('unsupported');
  });

  it('returns "failed" (never throws) when register() rejects', async () => {
    const register = vi.fn().mockRejectedValue(new Error('denied'));
    const nav = navWith({ periodicSync: { register } });

    await expect(initHealthWatch({ nav })).resolves.toBe('failed');
  });

  it('returns "failed" when serviceWorker.ready rejects', async () => {
    const nav = { serviceWorker: { ready: Promise.reject(new Error('no sw')) } };
    await expect(initHealthWatch({ nav })).resolves.toBe('failed');
  });

  it('returns "failed" without registering when permission is denied', async () => {
    const register = vi.fn();
    const nav = navWith(
      { periodicSync: { register } },
      { query: vi.fn().mockResolvedValue({ state: 'denied' }) },
    );

    expect(await initHealthWatch({ nav })).toBe('failed');
    expect(register).not.toHaveBeenCalled();
  });

  it('still registers when the permission query itself throws', async () => {
    const register = vi.fn().mockResolvedValue(undefined);
    const nav = navWith(
      { periodicSync: { register } },
      { query: vi.fn().mockRejectedValue(new TypeError('unknown name')) },
    );

    expect(await initHealthWatch({ nav })).toBe('registered');
  });
});
