/**
 * fuelToast.test.js — controller tests for the fuel-rule confirmation toast.
 * jsdom environment. requestAnimationFrame is stubbed to fire immediately so
 * the `.show` (enter) class lands synchronously; setTimeout is faked only where
 * the auto-dismiss lifecycle is exercised. All data is synthetic.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createFuelToast } from './fuelToast.js';

let toast;

beforeEach(() => {
  document.body.innerHTML = '';
  // Fire rAF immediately so the enter (.show) transition class is applied
  // synchronously in tests.
  vi.stubGlobal('requestAnimationFrame', (cb) => {
    cb(0);
    return 1;
  });
});

afterEach(() => {
  if (toast) toast.destroy();
  toast = null;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('createFuelToast', () => {
  it('lazily creates a single .toast-region in the document', () => {
    toast = createFuelToast(document);
    const regions = document.querySelectorAll('.toast-region');
    expect(regions.length).toBe(1);
    expect(regions[0].getAttribute('aria-live')).toBe('polite');
  });

  it('reuses an existing #fuel-toast-region rather than adding another', () => {
    const pre = document.createElement('div');
    pre.id = 'fuel-toast-region';
    pre.className = 'toast-region';
    document.body.appendChild(pre);

    toast = createFuelToast(document);
    expect(document.querySelectorAll('.toast-region').length).toBe(1);
  });
});

describe('show()', () => {
  beforeEach(() => {
    toast = createFuelToast(document);
  });

  it('renders a toast and applies the enter (.show) class', () => {
    toast.show(true, { count: 3, amount: '-24.10' });
    const el = document.querySelector('.toast');
    expect(el).not.toBeNull();
    expect(el.classList.contains('show')).toBe(true);
  });

  it('ON copy: "Moved to Dining Out" with the eligible count and amount', () => {
    toast.show(true, { count: 3, amount: '-24.10' });
    const el = document.querySelector('.toast');
    expect(el.classList.contains('toast--off')).toBe(false);
    expect(el.querySelector('.toast-title').textContent).toContain('Moved to Dining Out');
    const text = el.querySelector('.toast-text').textContent;
    expect(text).toContain('3 small servo purchases');
    expect(text).toContain('moved to Dining Out');
    expect(text).toContain('24.10');
  });

  it('OFF copy: "Kept under Transport" and "stay under Transport"', () => {
    toast.show(false, { count: 2, amount: '-12.50' });
    const el = document.querySelector('.toast');
    expect(el.classList.contains('toast--off')).toBe(true);
    expect(el.querySelector('.toast-title').textContent).toContain('Kept under Transport');
    expect(el.querySelector('.toast-text').textContent).toContain('stay under Transport');
  });

  it('uses singular "purchase" when count === 1', () => {
    toast.show(true, { count: 1, amount: '-8.00' });
    const text = document.querySelector('.toast-text').textContent;
    expect(text).toContain('1 small servo purchase ');
    expect(text).not.toContain('1 small servo purchases');
  });

  it('ON with zero eligible: standby copy, never "0 moved"', () => {
    toast.show(true, { count: 0, amount: '0.00' });
    const el = document.querySelector('.toast');
    expect(el.classList.contains('toast--off')).toBe(false);
    expect(el.querySelector('.toast-title').textContent).toContain('Fuel-stop rule on');
    const text = el.querySelector('.toast-text').textContent;
    expect(text).toContain('No small fuel stops this month yet');
    expect(text).not.toContain('moved to Dining Out');
    expect(text).not.toContain('0 small servo');
  });

  it('keeps only one toast at a time (replaces the current one)', () => {
    toast.show(true, { count: 3, amount: '-24.10' });
    toast.show(false, { count: 3, amount: '-24.10' });
    const toasts = document.querySelectorAll('.toast');
    expect(toasts.length).toBe(1);
    // The surviving toast is the most recent (OFF).
    expect(toasts[0].classList.contains('toast--off')).toBe(true);
  });

  it('includes a countdown timer bar', () => {
    toast.show(true, { count: 3, amount: '-24.10' });
    expect(document.querySelector('.toast .toast-timer')).not.toBeNull();
  });
});

describe('auto-dismiss lifecycle', () => {
  it('drops .show after the timeout, then removes the node after the fade', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    toast = createFuelToast(document, { timeoutMs: 5000, fadeMs: 450 });

    toast.show(true, { count: 3, amount: '-24.10' });
    const el = document.querySelector('.toast');
    expect(el.classList.contains('show')).toBe(true);

    vi.advanceTimersByTime(5000);
    expect(el.classList.contains('show')).toBe(false);
    // Still in the DOM during the fade window.
    expect(document.querySelector('.toast')).not.toBeNull();

    vi.advanceTimersByTime(450);
    expect(document.querySelector('.toast')).toBeNull();
  });

  it('replacing a toast cancels the previous auto-dismiss timer', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    toast = createFuelToast(document, { timeoutMs: 5000, fadeMs: 450 });

    toast.show(true, { count: 3, amount: '-24.10' });
    // Replace before the first would dismiss.
    vi.advanceTimersByTime(3000);
    toast.show(false, { count: 3, amount: '-24.10' });

    // Past the first toast's original deadline: the replacement is unaffected.
    vi.advanceTimersByTime(2500);
    const el = document.querySelector('.toast');
    expect(el).not.toBeNull();
    expect(el.classList.contains('show')).toBe(true);
  });
});
