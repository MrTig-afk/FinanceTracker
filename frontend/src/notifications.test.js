/**
 * notifications.test.js — the client bridge that turns relayed service-worker
 * push messages into in-app toasts. jsdom environment. The service worker and
 * toast are synthetic stubs; no real SW, no network. All payloads are synthetic
 * counts/status-only strings.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { createNotificationBridge, kindForType } from './notifications.js';
import { PUSH_MESSAGE_SOURCE } from './swPush.js';

/** Minimal EventTarget-like serviceWorker stub. */
function makeSwStub() {
  const listeners = new Map();
  return {
    addEventListener: vi.fn((type, handler) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    }),
    removeEventListener: vi.fn((type, handler) => {
      if (listeners.has(type)) listeners.get(type).delete(handler);
    }),
    /** Fire a 'message' event at all registered handlers. */
    emit(type, event) {
      for (const h of listeners.get(type) || []) h(event);
    },
    _count(type) {
      return listeners.has(type) ? listeners.get(type).size : 0;
    },
  };
}

function makeToast() {
  return { show: vi.fn() };
}

let sw;
let toast;
let bridge;

beforeEach(() => {
  sw = makeSwStub();
  toast = makeToast();
  bridge = null;
});

// ---------------------------------------------------------------------------
// kindForType
// ---------------------------------------------------------------------------

describe('kindForType', () => {
  it('maps failure types to "error"', () => {
    for (const t of ['parse_error', 'categorisation_failed', 'drive_backup_failed', 'generic_error']) {
      expect(kindForType(t)).toBe('error');
    }
  });

  it('maps processed/recovered types to "success"', () => {
    for (const t of ['processed', 'processed_recovered', 'categorisation_recovered']) {
      expect(kindForType(t)).toBe('success');
    }
  });

  it('maps informational + unknown types to "info"', () => {
    for (const t of ['duplicate_noop', 'monthly_reminder', 'something_new', undefined]) {
      expect(kindForType(t)).toBe('info');
    }
  });
});

// ---------------------------------------------------------------------------
// createNotificationBridge — message -> toast
// ---------------------------------------------------------------------------

describe('createNotificationBridge', () => {
  it('registers a single "message" listener on the service worker', () => {
    bridge = createNotificationBridge({ toast, nav: { serviceWorker: sw } });
    expect(sw._count('message')).toBe(1);
  });

  it('shows a toast for a marked push payload, mapping type -> kind', () => {
    bridge = createNotificationBridge({ toast, nav: { serviceWorker: sw } });
    sw.emit('message', {
      data: {
        source: PUSH_MESSAGE_SOURCE,
        type: 'processed',
        title: 'Statement ready',
        body: '4 transactions processed',
      },
    });
    expect(toast.show).toHaveBeenCalledTimes(1);
    expect(toast.show).toHaveBeenCalledWith({
      title: 'Statement ready',
      body: '4 transactions processed',
      kind: 'success',
    });
  });

  it('maps an error type to the error kind', () => {
    bridge = createNotificationBridge({ toast, nav: { serviceWorker: sw } });
    sw.emit('message', {
      data: { source: PUSH_MESSAGE_SOURCE, type: 'parse_error', title: 'Oops', body: 'Could not read the file' },
    });
    expect(toast.show).toHaveBeenCalledWith({
      title: 'Oops',
      body: 'Could not read the file',
      kind: 'error',
    });
  });

  it('falls back to the app name / empty body when fields are missing', () => {
    bridge = createNotificationBridge({ toast, nav: { serviceWorker: sw } });
    sw.emit('message', { data: { source: PUSH_MESSAGE_SOURCE, type: 'monthly_reminder' } });
    expect(toast.show).toHaveBeenCalledWith({
      title: 'FinanceTracker',
      body: '',
      kind: 'info',
    });
  });

  it('ignores messages without our source marker', () => {
    bridge = createNotificationBridge({ toast, nav: { serviceWorker: sw } });
    sw.emit('message', { data: { source: 'some-other-lib', title: 'x', body: 'y' } });
    sw.emit('message', { data: { title: 'no source', body: 'y' } });
    expect(toast.show).not.toHaveBeenCalled();
  });

  it('ignores messages with no / non-object data', () => {
    bridge = createNotificationBridge({ toast, nav: { serviceWorker: sw } });
    sw.emit('message', { data: null });
    sw.emit('message', { data: 'a string' });
    sw.emit('message', {});
    expect(toast.show).not.toHaveBeenCalled();
  });

  it('destroy() removes the listener; later messages do not show a toast', () => {
    bridge = createNotificationBridge({ toast, nav: { serviceWorker: sw } });
    bridge.destroy();
    expect(sw._count('message')).toBe(0);
    sw.emit('message', {
      data: { source: PUSH_MESSAGE_SOURCE, type: 'processed', title: 't', body: 'b' },
    });
    expect(toast.show).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Graceful no-op when unsupported
// ---------------------------------------------------------------------------

describe('unsupported / missing dependencies', () => {
  it('returns a no-op controller when serviceWorker is absent', () => {
    expect(() => {
      bridge = createNotificationBridge({ toast, nav: {} });
    }).not.toThrow();
    expect(() => bridge.destroy()).not.toThrow();
    expect(toast.show).not.toHaveBeenCalled();
  });

  it('returns a no-op controller when no toast is provided', () => {
    bridge = createNotificationBridge({ nav: { serviceWorker: sw } });
    expect(sw._count('message')).toBe(0);
    expect(() => bridge.destroy()).not.toThrow();
  });
});
