/**
 * swPush.test.js — unit tests for swPush.js (v2 Pass 3 — SCAFFOLD).
 * Pure module, no SW globals required — no real service-worker environment,
 * no real push data, no network. All client objects are synthetic stubs.
 */

import { describe, it, expect } from 'vitest';
import { PUSH_TITLE, PUSH_BODY, notificationOptions, pickClientToFocus } from './swPush.js';

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
