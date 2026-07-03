/**
 * transactionRow.test.js — unit tests for the shared row builder (transactionRow.js).
 * jsdom provides the DOM. All fixtures are synthetic; no real transaction data.
 */

import { describe, it, expect } from 'vitest';
import { buildRowMain } from './transactionRow.js';
import { formatCurrency } from './summary.js';

const SYNTH = { id: 1, date: '2026-06-12', description: 'SYNTH MERCHANT', amount: '-12.34' };

describe('buildRowMain', () => {
  it('returns a .cat-drawer-row-main element', () => {
    const el = buildRowMain(document, SYNTH);
    expect(el.tagName).toBe('DIV');
    expect(el.className).toBe('cat-drawer-row-main');
  });

  it('contains .cat-drawer-date / -desc / -amount children', () => {
    const el = buildRowMain(document, SYNTH);
    expect(el.querySelector('.cat-drawer-date').textContent).toBe('2026-06-12');
    expect(el.querySelector('.cat-drawer-desc').textContent).toBe('SYNTH MERCHANT');
    expect(el.querySelector('.cat-drawer-amount').textContent).toBe(formatCurrency('-12.34'));
  });

  it('orders children as date, desc, amount', () => {
    const el = buildRowMain(document, SYNTH);
    // First class of each child (the amount span also carries an is-* toggle).
    const classes = [...el.children].map((c) => c.classList[0]);
    expect(classes).toEqual(['cat-drawer-date', 'cat-drawer-desc', 'cat-drawer-amount']);
  });

  it('flags a negative amount with is-negative (not is-positive)', () => {
    const el = buildRowMain(document, { ...SYNTH, amount: '-5.00' });
    const amount = el.querySelector('.cat-drawer-amount');
    expect(amount.classList.contains('is-negative')).toBe(true);
    expect(amount.classList.contains('is-positive')).toBe(false);
  });

  it('flags a non-negative amount with is-positive (not is-negative)', () => {
    const el = buildRowMain(document, { ...SYNTH, amount: '5.00' });
    const amount = el.querySelector('.cat-drawer-amount');
    expect(amount.classList.contains('is-positive')).toBe(true);
    expect(amount.classList.contains('is-negative')).toBe(false);
  });

  it('treats exactly zero as non-negative (is-positive)', () => {
    const el = buildRowMain(document, { ...SYNTH, amount: '0.00' });
    const amount = el.querySelector('.cat-drawer-amount');
    expect(amount.classList.contains('is-positive')).toBe(true);
    expect(amount.classList.contains('is-negative')).toBe(false);
  });

  it('accepts a numeric amount as well as a string', () => {
    const el = buildRowMain(document, { ...SYNTH, amount: -12.34 });
    expect(el.querySelector('.cat-drawer-amount').classList.contains('is-negative')).toBe(true);
  });

  it('renders the description via textContent — no HTML is parsed (XSS-safe)', () => {
    const el = buildRowMain(document, { ...SYNTH, description: '<img onerror=alert(1)>' });
    const desc = el.querySelector('.cat-drawer-desc');
    // The raw string is the text; no <img> child element is created.
    expect(desc.textContent).toBe('<img onerror=alert(1)>');
    expect(desc.querySelector('img')).toBeNull();
    expect(desc.children.length).toBe(0);
  });
});
