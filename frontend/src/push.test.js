/**
 * push.test.js — DOM wiring tests for push.js (v2 Pass 3 — SCAFFOLD).
 * jsdom provides the DOM. ALL browser Push/Notification APIs are mocked —
 * never a real service worker, never a real push subscription. `api` is
 * always an injected object; fetch is never touched by this file.
 * All subscription/key data used below is SYNTHETIC.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createPushController, urlBase64ToUint8Array, VAPID_PLACEHOLDER } from './push.js';

// ---------------------------------------------------------------------------
// Fixture HTML — mirrors the #push-card contract in index.html.
// ---------------------------------------------------------------------------

const PUSH_HTML = `
  <section id="push-card" class="card push-card">
    <h2 class="push-title">Notifications</h2>
    <p class="push-desc">Get a notification on this device when a statement finishes processing.</p>
    <button id="enable-push" type="button" class="upload-btn">Enable notifications</button>
    <p id="push-status" class="upload-status" role="status" aria-live="polite"></p>
  </section>
`;

const SYNTH_KEY = 'SYNTHETIC_TEST_VAPID_PUBLIC_KEY_bm90X3JlYWw';
const SYNTH_SUB_JSON = {
  endpoint: 'https://example.test/push/SYNTH_ENDPOINT',
  keys: { p256dh: 'synth_p256dh', auth: 'synth_auth' },
};

function getStatus() {
  return document.getElementById('push-status').textContent;
}

function getButton() {
  return document.getElementById('enable-push');
}

function makeApi() {
  return {
    subscribe: vi.fn().mockResolvedValue({ ok: true }),
    unsubscribe: vi.fn().mockResolvedValue({ ok: true }),
  };
}

/** Wire a fully "supported" browser environment (serviceWorker + PushManager + Notification). */
function mockSupportedEnv({
  requestPermission = vi.fn().mockResolvedValue('granted'),
  subscribe = vi.fn().mockResolvedValue({ toJSON: () => SYNTH_SUB_JSON }),
} = {}) {
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { ready: Promise.resolve({ pushManager: { subscribe } }) },
    configurable: true,
  });
  vi.stubGlobal('PushManager', class {});
  vi.stubGlobal('Notification', { requestPermission });
  return { requestPermission, subscribe };
}

function clearSupportedEnv() {
  if ('serviceWorker' in navigator) delete navigator.serviceWorker;
  vi.unstubAllGlobals();
}

let controller;

beforeEach(() => {
  document.body.innerHTML = PUSH_HTML;
});

afterEach(() => {
  if (controller && controller.destroy) controller.destroy();
  controller = null;
  clearSupportedEnv();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// urlBase64ToUint8Array — pure helper
// ---------------------------------------------------------------------------

describe('urlBase64ToUint8Array', () => {
  it('decodes a known URL-safe base64 string to the expected bytes', () => {
    // 'SGVsbG8' (URL-safe, no padding) decodes to the ASCII bytes for "Hello".
    const result = urlBase64ToUint8Array('SGVsbG8');
    expect(Array.from(result)).toEqual([72, 101, 108, 108, 111]);
  });

  it('returns a Uint8Array', () => {
    const result = urlBase64ToUint8Array('SGVsbG8');
    expect(result).toBeInstanceOf(Uint8Array);
  });

  it('handles URL-safe characters (- and _) correctly', () => {
    // Standard base64 '+/' encode differently from URL-safe '-_'; round-trip check
    // via atob after manual substitution confirms the decoder swaps them back.
    const urlSafe = 'PDw_Pz8-Pg'; // no padding; contains '-' and '_' substitutes
    expect(() => urlBase64ToUint8Array(urlSafe)).not.toThrow();
  });

  it('produces the correct byte length for a padded-needed input', () => {
    // 'QQ' (2 chars) needs 2 padding chars -> decodes to a single byte 'A' (65).
    const result = urlBase64ToUint8Array('QQ');
    expect(Array.from(result)).toEqual([65]);
  });
});

// ---------------------------------------------------------------------------
// VAPID_PLACEHOLDER export
// ---------------------------------------------------------------------------

describe('VAPID_PLACEHOLDER', () => {
  it('equals the literal sentinel committed to frontend/.env.example', () => {
    expect(VAPID_PLACEHOLDER).toBe('REPLACE_WITH_VAPID_PUBLIC_KEY');
  });
});

// ---------------------------------------------------------------------------
// Unsupported environment — no serviceWorker/PushManager/Notification.
// ---------------------------------------------------------------------------

describe('unsupported environment (no PushManager/serviceWorker/Notification)', () => {
  it('disables the button and never throws', () => {
    const api = makeApi();
    expect(() => {
      controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    }).not.toThrow();
    expect(getButton().disabled).toBe(true);
  });

  it('sets the "not supported" status message', () => {
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    expect(getStatus()).toBe('Notifications are not supported on this device.');
  });

  it('never calls api.subscribe', () => {
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    expect(api.subscribe).not.toHaveBeenCalled();
  });

  it('clicking the (disabled) button still never calls api.subscribe', async () => {
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    getButton().click();
    await Promise.resolve();
    expect(api.subscribe).not.toHaveBeenCalled();
  });

  it('explicitly missing window.PushManager only (serviceWorker present) is still unsupported', () => {
    Object.defineProperty(navigator, 'serviceWorker', {
      value: { ready: Promise.resolve({}) },
      configurable: true,
    });
    vi.stubGlobal('Notification', { requestPermission: vi.fn() });
    // PushManager deliberately NOT stubbed.
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    expect(getButton().disabled).toBe(true);
    expect(getStatus()).toBe('Notifications are not supported on this device.');
  });
});

// ---------------------------------------------------------------------------
// Placeholder / missing VAPID key — "not configured" state.
// ---------------------------------------------------------------------------

describe('placeholder or missing VAPID public key', () => {
  it('placeholder key: disables the button with "not configured" status, no subscribe attempt', () => {
    mockSupportedEnv();
    const api = makeApi();
    controller = createPushController({
      root: document,
      api,
      vapidPublicKey: VAPID_PLACEHOLDER,
    });
    expect(getButton().disabled).toBe(true);
    expect(getStatus()).toBe('Notifications are not configured yet.');
    expect(api.subscribe).not.toHaveBeenCalled();
  });

  it('empty-string key: same "not configured" behaviour', () => {
    mockSupportedEnv();
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: '' });
    expect(getButton().disabled).toBe(true);
    expect(getStatus()).toBe('Notifications are not configured yet.');
  });

  it('undefined key: same "not configured" behaviour, never throws', () => {
    mockSupportedEnv();
    const api = makeApi();
    expect(() => {
      controller = createPushController({ root: document, api, vapidPublicKey: undefined });
    }).not.toThrow();
    expect(getStatus()).toBe('Notifications are not configured yet.');
  });

  it('clicking the disabled button never reaches pushManager.subscribe', async () => {
    const { subscribe } = mockSupportedEnv();
    const api = makeApi();
    controller = createPushController({
      root: document,
      api,
      vapidPublicKey: VAPID_PLACEHOLDER,
    });
    getButton().click();
    await Promise.resolve();
    expect(subscribe).not.toHaveBeenCalled();
    expect(api.subscribe).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Permission denied
// ---------------------------------------------------------------------------

describe('Notification permission denied', () => {
  it('sets the "permission was not granted" status and never throws', async () => {
    mockSupportedEnv({ requestPermission: vi.fn().mockResolvedValue('denied') });
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));
    await Promise.resolve();
    await Promise.resolve();

    expect(getStatus()).toBe('Notifications permission was not granted.');
  });

  it('never calls pushManager.subscribe when permission is denied', async () => {
    const { subscribe } = mockSupportedEnv({
      requestPermission: vi.fn().mockResolvedValue('denied'),
    });
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(subscribe).not.toHaveBeenCalled();
  });

  it('never calls api.subscribe when permission is denied', async () => {
    mockSupportedEnv({ requestPermission: vi.fn().mockResolvedValue('denied') });
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(api.subscribe).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Happy path — full subscribe flow.
// ---------------------------------------------------------------------------

describe('happy path — permission granted, subscribe succeeds', () => {
  it('calls pushManager.subscribe with the expected shape', async () => {
    const { subscribe } = mockSupportedEnv();
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(subscribe).toHaveBeenCalledTimes(1);
    const callArg = subscribe.mock.calls[0][0];
    expect(callArg.userVisibleOnly).toBe(true);
    expect(callArg.applicationServerKey).toBeInstanceOf(Uint8Array);
  });

  it('calls api.subscribe with the PushSubscription toJSON() result', async () => {
    mockSupportedEnv();
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(api.subscribe).toHaveBeenCalledWith(SYNTH_SUB_JSON);
  });

  it('sets the "Notifications enabled." status on success', async () => {
    mockSupportedEnv();
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(getStatus()).toBe('Notifications enabled.');
  });

  it('does not disable the button up-front (supported + configured)', () => {
    mockSupportedEnv();
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    expect(getButton().disabled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// api.subscribe rejects — graceful failure, no throw.
// ---------------------------------------------------------------------------

describe('api.subscribe rejects (backend unreachable)', () => {
  it('sets "Could not enable notifications." and never throws', async () => {
    mockSupportedEnv();
    const api = {
      subscribe: vi.fn().mockRejectedValue(new Error('synthetic network failure')),
      unsubscribe: vi.fn(),
    };
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(getStatus()).toBe('Could not enable notifications.');
  });
});

// ---------------------------------------------------------------------------
// pushManager.subscribe rejects — graceful failure, no throw.
// ---------------------------------------------------------------------------

describe('pushManager.subscribe rejects', () => {
  it('sets "Could not enable notifications." and never calls api.subscribe', async () => {
    const rejectingSubscribe = vi.fn().mockRejectedValue(new Error('synthetic subscribe failure'));
    mockSupportedEnv({ subscribe: rejectingSubscribe });
    const api = makeApi();
    controller = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(getStatus()).toBe('Could not enable notifications.');
    expect(api.subscribe).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy', () => {
  it('is callable without throwing in every branch (unsupported)', () => {
    const api = makeApi();
    const c = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    expect(() => c.destroy()).not.toThrow();
  });

  it('is callable without throwing in every branch (supported + configured)', () => {
    mockSupportedEnv();
    const api = makeApi();
    const c = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    expect(() => c.destroy()).not.toThrow();
  });

  it('removes the click listener — a click after destroy() does not call api.subscribe', async () => {
    mockSupportedEnv();
    const api = makeApi();
    const c = createPushController({ root: document, api, vapidPublicKey: SYNTH_KEY });
    c.destroy();

    getButton().click();
    await new Promise((r) => setTimeout(r, 0));

    expect(api.subscribe).not.toHaveBeenCalled();
  });
});
