/**
 * navBadge.test.js — DOM wiring tests for navBadge.js (v7 feature 2).
 *
 * The unseen-count nav badge is DOM-only: no network, no transaction data. Tests
 * assert the fail-closed rendering (only a finite positive number shows the pill),
 * the 99+ cap, textContent-only rendering, and safe no-ops on a missing element.
 * All fixtures SYNTHETIC. No real data anywhere.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

import { createNavBadge } from './navBadge.js';

// Mirrors the badge markup nested in the Transfers nav anchor in index.html.
const BADGE_HTML = `
  <a href="#" class="nav-item" data-view="transfers">
    <span class="nav-dot"></span>Transfers
    <span id="nav-badge-transfers" class="nav-badge" hidden aria-label="new transfers"></span>
  </a>
`;

const $ = () => document.getElementById('nav-badge-transfers');

beforeEach(() => {
  document.body.innerHTML = BADGE_HTML;
});

describe('createNavBadge — set() happy path', () => {
  it('set(3) shows the pill with the number', () => {
    const badge = createNavBadge({ root: document });
    badge.set(3);
    expect($().hidden).toBe(false);
    expect($().textContent).toBe('3');
  });

  it('set(1) shows a single-digit count', () => {
    const badge = createNavBadge({ root: document });
    badge.set(1);
    expect($().hidden).toBe(false);
    expect($().textContent).toBe('1');
  });
});

describe('createNavBadge — fail-closed hiding', () => {
  it.each([
    ['zero', 0],
    ['negative', -1],
    ['NaN', NaN],
    ['Infinity', Infinity],
    ['undefined', undefined],
    ['null', null],
    ['numeric string', '5'],
    ['object', {}],
  ])('set(%s) hides the pill', (_label, value) => {
    const badge = createNavBadge({ root: document });
    // Start visible so we prove the value actually hides it.
    badge.set(3);
    expect($().hidden).toBe(false);

    badge.set(value);
    expect($().hidden).toBe(true);
    expect($().textContent).toBe('');
  });
});

describe('createNavBadge — 99+ cap', () => {
  it('set(120) caps the display at 99+', () => {
    const badge = createNavBadge({ root: document });
    badge.set(120);
    expect($().hidden).toBe(false);
    expect($().textContent).toBe('99+');
  });

  it('set(99) shows the exact number (boundary, not capped)', () => {
    const badge = createNavBadge({ root: document });
    badge.set(99);
    expect($().textContent).toBe('99');
  });

  it('set(100) is the first capped value', () => {
    const badge = createNavBadge({ root: document });
    badge.set(100);
    expect($().textContent).toBe('99+');
  });
});

describe('createNavBadge — clear() and destroy()', () => {
  it('clear() after set(3) hides the pill and empties the text', () => {
    const badge = createNavBadge({ root: document });
    badge.set(3);
    badge.clear();
    expect($().hidden).toBe(true);
    expect($().textContent).toBe('');
  });

  it('destroy() does not throw', () => {
    const badge = createNavBadge({ root: document });
    badge.set(3);
    expect(() => badge.destroy()).not.toThrow();
  });
});

describe('createNavBadge — textContent-only rendering', () => {
  it('never injects markup even if a count somehow stringified with tags', () => {
    const badge = createNavBadge({ root: document });
    badge.set(5);
    // Rendered as inert text; the pill has no child elements.
    expect($().children.length).toBe(0);
    expect($().innerHTML).toBe('5');
  });
});

describe('createNavBadge — missing element', () => {
  it('set / clear / destroy are safe no-ops when the element is absent', () => {
    document.body.innerHTML = '<div>no badge here</div>';
    const badge = createNavBadge({ root: document });
    expect(() => {
      badge.set(3);
      badge.set(0);
      badge.clear();
      badge.destroy();
    }).not.toThrow();
  });

  it('does not create the missing element as a side effect', () => {
    document.body.innerHTML = '<div>no badge here</div>';
    const badge = createNavBadge({ root: document });
    badge.set(7);
    expect(document.getElementById('nav-badge-transfers')).toBeNull();
  });
});
