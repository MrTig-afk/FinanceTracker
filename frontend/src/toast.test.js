/**
 * toast.test.js — controller tests for the reusable createToast() toast.
 * jsdom environment. requestAnimationFrame is stubbed to fire immediately so
 * the `.show` (enter) class lands synchronously; setTimeout is faked only where
 * the auto-dismiss lifecycle is exercised. All data is synthetic.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createToast } from './toast.js';

let toast;

beforeEach(() => {
  document.body.innerHTML = '';
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

describe('region creation', () => {
  it('lazily creates a single .toast-region with the given id and aria-live', () => {
    toast = createToast(document, { regionId: 'notify-toast-region' });
    const regions = document.querySelectorAll('.toast-region');
    expect(regions.length).toBe(1);
    expect(regions[0].id).toBe('notify-toast-region');
    expect(regions[0].getAttribute('aria-live')).toBe('polite');
  });

  it('reuses an existing region with the same id', () => {
    const pre = document.createElement('div');
    pre.id = 'notify-toast-region';
    pre.className = 'toast-region';
    document.body.appendChild(pre);

    toast = createToast(document, { regionId: 'notify-toast-region' });
    expect(document.querySelectorAll('.toast-region').length).toBe(1);
  });

  it('defaults the region id to "toast-region"', () => {
    toast = createToast(document);
    expect(document.getElementById('toast-region')).not.toBeNull();
  });
});

describe('show()', () => {
  beforeEach(() => {
    toast = createToast(document, { regionId: 'notify-toast-region' });
  });

  it('renders a toast with title + body text and the enter (.show) class', () => {
    toast.show({ title: 'Statement ready', body: '5 transactions processed' });
    const el = document.querySelector('.toast');
    expect(el).not.toBeNull();
    expect(el.classList.contains('show')).toBe(true);
    expect(el.querySelector('.toast-title').textContent).toContain('Statement ready');
    expect(el.querySelector('.toast-text').textContent).toBe('5 transactions processed');
  });

  it('maps kind -> toast--<kind> modifier class', () => {
    toast.show({ title: 'Oops', body: 'Could not read the file', kind: 'error' });
    const el = document.querySelector('.toast');
    expect(el.classList.contains('toast--error')).toBe(true);
  });

  it('a raw modifier takes precedence over kind', () => {
    toast.show({ title: 't', body: 'b', kind: 'error', modifier: 'toast--off' });
    const el = document.querySelector('.toast');
    expect(el.classList.contains('toast--off')).toBe(true);
    expect(el.classList.contains('toast--error')).toBe(false);
  });

  it('empty modifier yields the base .toast class only', () => {
    toast.show({ title: 't', body: 'b', modifier: '' });
    const el = document.querySelector('.toast');
    expect(el.className).toBe('toast show');
  });

  it('supports a buildBody callback for rich content', () => {
    toast.show({
      title: 't',
      buildBody: (textEl, doc) => {
        const strong = doc.createElement('strong');
        strong.textContent = 'bold';
        textEl.appendChild(strong);
        textEl.appendChild(doc.createTextNode(' plain'));
      },
    });
    const text = document.querySelector('.toast-text');
    expect(text.querySelector('strong').textContent).toBe('bold');
    expect(text.textContent).toBe('bold plain');
  });

  it('always includes accent, dot and timer sub-elements', () => {
    toast.show({ title: 't', body: 'b' });
    expect(document.querySelector('.toast .toast-accent')).not.toBeNull();
    expect(document.querySelector('.toast .toast-title .dot')).not.toBeNull();
    expect(document.querySelector('.toast .toast-timer')).not.toBeNull();
  });

  it('keeps only one toast at a time (replaces the current one)', () => {
    toast.show({ title: 'first', body: 'a' });
    toast.show({ title: 'second', body: 'b', kind: 'info' });
    const toasts = document.querySelectorAll('.toast');
    expect(toasts.length).toBe(1);
    expect(toasts[0].classList.contains('toast--info')).toBe(true);
  });
});

describe('auto-dismiss lifecycle', () => {
  it('drops .show after the timeout, then removes the node after the fade', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    toast = createToast(document, { regionId: 'notify-toast-region', timeoutMs: 5000, fadeMs: 450 });

    toast.show({ title: 't', body: 'b' });
    const el = document.querySelector('.toast');
    expect(el.classList.contains('show')).toBe(true);

    vi.advanceTimersByTime(5000);
    expect(el.classList.contains('show')).toBe(false);
    expect(document.querySelector('.toast')).not.toBeNull();

    vi.advanceTimersByTime(450);
    expect(document.querySelector('.toast')).toBeNull();
  });

  it('replacing a toast cancels the previous auto-dismiss timer', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    toast = createToast(document, { regionId: 'notify-toast-region', timeoutMs: 5000, fadeMs: 450 });

    toast.show({ title: 'first', body: 'a' });
    vi.advanceTimersByTime(3000);
    toast.show({ title: 'second', body: 'b' });

    vi.advanceTimersByTime(2500);
    const el = document.querySelector('.toast');
    expect(el).not.toBeNull();
    expect(el.classList.contains('show')).toBe(true);
  });
});

describe('destroy()', () => {
  it('removes the region and any current toast', () => {
    toast = createToast(document, { regionId: 'notify-toast-region' });
    toast.show({ title: 't', body: 'b' });
    toast.destroy();
    toast = null;
    expect(document.querySelector('.toast-region')).toBeNull();
    expect(document.querySelector('.toast')).toBeNull();
  });
});
