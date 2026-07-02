/**
 * swPush.test.js — unit tests for swPush.js (v2 Pass 3 — SCAFFOLD).
 * Pure module, no SW globals required — no real service-worker environment,
 * no real push data, no network. All client objects are synthetic stubs.
 */

import { describe, it, expect } from 'vitest';
import {
  PUSH_TITLE,
  PUSH_BODY,
  PUSH_MESSAGE_SOURCE,
  notificationOptions,
  pickClientToFocus,
  normalizePushPayload,
  isClientFocused,
  findFocusedClient,
  notificationArgs,
  routePush,
} from './swPush.js';

// ---------------------------------------------------------------------------
// PUSH_TITLE / PUSH_BODY — fixed generic strings, no financial data
// ---------------------------------------------------------------------------

describe('PUSH_TITLE / PUSH_BODY', () => {
  it('PUSH_TITLE is the exact fixed generic string', () => {
    expect(PUSH_TITLE).toBe('FinanceTracker');
  });

  it('PUSH_BODY is the exact fixed generic string', () => {
    expect(PUSH_BODY).toBe('Your statement was processed');
  });

  it('PUSH_TITLE contains no digits', () => {
    expect(/\d/.test(PUSH_TITLE)).toBe(false);
  });

  it('PUSH_BODY contains no digits (no amounts/counts)', () => {
    expect(/\d/.test(PUSH_BODY)).toBe(false);
  });

  it('PUSH_BODY contains none of the forbidden financial-data tokens', () => {
    const lowered = PUSH_BODY.toLowerCase();
    for (const token of ['$', 'amount', 'balance', 'category', 'description', 'account']) {
      expect(lowered).not.toContain(token);
    }
  });

  it('neither string contains an em-dash (U+2014)', () => {
    expect(PUSH_TITLE).not.toContain('—');
    expect(PUSH_BODY).not.toContain('—');
  });
});

// ---------------------------------------------------------------------------
// notificationOptions()
// ---------------------------------------------------------------------------

describe('notificationOptions', () => {
  it('returns the exact shape {body, icon, badge, tag}', () => {
    const opts = notificationOptions();
    expect(Object.keys(opts).sort()).toEqual(['badge', 'body', 'icon', 'tag']);
  });

  it('body equals PUSH_BODY', () => {
    expect(notificationOptions().body).toBe(PUSH_BODY);
  });

  it('tag is the fixed "financetracker-processed" value', () => {
    expect(notificationOptions().tag).toBe('financetracker-processed');
  });

  it('icon and badge point at /icon.svg', () => {
    const opts = notificationOptions();
    expect(opts.icon).toBe('/icon.svg');
    expect(opts.badge).toBe('/icon.svg');
  });

  it('is a pure function — calling it twice returns equal (non-identical) objects', () => {
    const a = notificationOptions();
    const b = notificationOptions();
    expect(a).toEqual(b);
    expect(a).not.toBe(b);
  });
});

// ---------------------------------------------------------------------------
// pickClientToFocus(clientList)
// ---------------------------------------------------------------------------

describe('pickClientToFocus', () => {
  it('returns null for an empty client list', () => {
    expect(pickClientToFocus([])).toBeNull();
  });

  it('picks the first client that has a focus() method', () => {
    const noFocus = { url: 'https://host/other' };
    const focusable = { url: 'https://host/', focus: () => {} };
    expect(pickClientToFocus([noFocus, focusable])).toBe(focusable);
  });

  it('skips clients without focus and returns the next focusable one', () => {
    const noFocus1 = {};
    const noFocus2 = {};
    const focusable = { focus: () => {} };
    expect(pickClientToFocus([noFocus1, noFocus2, focusable])).toBe(focusable);
  });

  it('returns null when no client in the list is focusable', () => {
    expect(pickClientToFocus([{}, {}, { notFocus: true }])).toBeNull();
  });

  it('returns the first client when multiple are focusable (first-match wins)', () => {
    const first = { id: 'first', focus: () => {} };
    const second = { id: 'second', focus: () => {} };
    expect(pickClientToFocus([first, second])).toBe(first);
  });
});

// ---------------------------------------------------------------------------
// normalizePushPayload — safe {type,title,body} with fixed fallbacks
// ---------------------------------------------------------------------------

describe('normalizePushPayload', () => {
  it('passes through a complete payload unchanged', () => {
    const raw = { type: 'processed', title: 'Statement ready', body: '12 transactions processed' };
    expect(normalizePushPayload(raw)).toEqual(raw);
  });

  it('falls back to the fixed generic strings for a null/undefined payload', () => {
    expect(normalizePushPayload(null)).toEqual({
      type: 'generic',
      title: PUSH_TITLE,
      body: PUSH_BODY,
    });
    expect(normalizePushPayload(undefined)).toEqual({
      type: 'generic',
      title: PUSH_TITLE,
      body: PUSH_BODY,
    });
  });

  it('fills missing fields individually', () => {
    expect(normalizePushPayload({ type: 'parse_error' })).toEqual({
      type: 'parse_error',
      title: PUSH_TITLE,
      body: PUSH_BODY,
    });
  });

  it('ignores non-string / blank fields (falls back)', () => {
    expect(normalizePushPayload({ type: 5, title: '   ', body: {} })).toEqual({
      type: 'generic',
      title: PUSH_TITLE,
      body: PUSH_BODY,
    });
  });
});

// ---------------------------------------------------------------------------
// isClientFocused / findFocusedClient
// ---------------------------------------------------------------------------

describe('isClientFocused', () => {
  it('is true when focused === true', () => {
    expect(isClientFocused({ focused: true })).toBe(true);
  });
  it('is true when visibilityState === "visible"', () => {
    expect(isClientFocused({ visibilityState: 'visible' })).toBe(true);
  });
  it('is false for an unfocused/hidden client', () => {
    expect(isClientFocused({ focused: false, visibilityState: 'hidden' })).toBe(false);
  });
  it('is false for null/undefined', () => {
    expect(isClientFocused(null)).toBe(false);
    expect(isClientFocused(undefined)).toBe(false);
  });
});

describe('findFocusedClient', () => {
  it('returns the first focused/visible client', () => {
    const hidden = { visibilityState: 'hidden' };
    const focused = { visibilityState: 'visible', id: 'x' };
    expect(findFocusedClient([hidden, focused])).toBe(focused);
  });
  it('returns null when none are focused', () => {
    expect(findFocusedClient([{ focused: false }, { visibilityState: 'hidden' }])).toBeNull();
  });
  it('returns null for an empty or missing list', () => {
    expect(findFocusedClient([])).toBeNull();
    expect(findFocusedClient(undefined)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// notificationArgs — showNotification(title, options) pair
// ---------------------------------------------------------------------------

describe('notificationArgs', () => {
  it('derives a per-type tag and carries the payload in data', () => {
    const { title, options } = notificationArgs({
      type: 'parse_error',
      title: 'Could not read the file',
      body: 'Please re-export and try again',
    });
    expect(title).toBe('Could not read the file');
    expect(options.body).toBe('Please re-export and try again');
    expect(options.tag).toBe('financetracker-parse_error');
    expect(options.icon).toBe('/icon.svg');
    expect(options.badge).toBe('/icon.svg');
    expect(options.data).toEqual({
      type: 'parse_error',
      title: 'Could not read the file',
      body: 'Please re-export and try again',
    });
  });

  it('falls back to generic strings + generic tag for an empty payload', () => {
    const { title, options } = notificationArgs(null);
    expect(title).toBe(PUSH_TITLE);
    expect(options.body).toBe(PUSH_BODY);
    expect(options.tag).toBe('financetracker-generic');
  });
});

// ---------------------------------------------------------------------------
// routePush — THE pure focus-routing decision
// ---------------------------------------------------------------------------

describe('routePush', () => {
  const payload = { type: 'processed', title: 'Done', body: '3 transactions processed' };

  it('a FOCUSED client -> action "message" carrying the marked payload (no OS notification)', () => {
    const clients = [{ visibilityState: 'hidden' }, { focused: true }];
    const decision = routePush(clients, payload);
    expect(decision.action).toBe('message');
    expect(decision.message).toEqual({
      source: PUSH_MESSAGE_SOURCE,
      type: 'processed',
      title: 'Done',
      body: '3 transactions processed',
    });
    expect(decision.options).toBeUndefined();
  });

  it('NO focused client -> action "notify" with showNotification args', () => {
    const clients = [{ focused: false, visibilityState: 'hidden' }];
    const decision = routePush(clients, payload);
    expect(decision.action).toBe('notify');
    expect(decision.title).toBe('Done');
    expect(decision.options.body).toBe('3 transactions processed');
    expect(decision.options.tag).toBe('financetracker-processed');
    expect(decision.message).toBeUndefined();
  });

  it('empty client list -> notify (backgrounded / no windows)', () => {
    expect(routePush([], payload).action).toBe('notify');
  });

  it('malformed payload with a focused client still routes as message, using fallback strings', () => {
    const decision = routePush([{ focused: true }], null);
    expect(decision.action).toBe('message');
    expect(decision.message.title).toBe(PUSH_TITLE);
    expect(decision.message.body).toBe(PUSH_BODY);
    expect(decision.message.type).toBe('generic');
  });
});
