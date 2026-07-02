/**
 * categoryDrawer.test.js — DOM tests for the category drill-down drawer.
 * jsdom provides the DOM; fetchFn is injected (no real network).
 * All fixtures are synthetic. No real transaction data.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { createCategoryDrawer } from './categoryDrawer.js';

const CANNED = {
  category: 'Subscriptions',
  month: '2026-06',
  total: '-170.01',
  count: 1,
  transactions: [
    { date: '2026-06-12', description: 'SYNTH SUB', amount: '-170.01', bank: 'commbank' },
  ],
};

let drawer;

afterEach(() => {
  if (drawer) {
    drawer.destroy();
    drawer = null;
  }
  document.body.innerHTML = '';
});

const panel = () => document.querySelector('.cat-drawer');
const backdrop = () => document.querySelector('.cat-drawer-backdrop');

describe('createCategoryDrawer', () => {
  it('appends a closed drawer + backdrop to the body', () => {
    drawer = createCategoryDrawer({ root: document, fetchFn: vi.fn() });
    expect(panel()).not.toBeNull();
    expect(backdrop()).not.toBeNull();
    expect(panel().classList.contains('is-open')).toBe(false);
    expect(panel().getAttribute('aria-hidden')).toBe('true');
    expect(drawer.isOpen).toBe(false);
  });

  it('open() shows the drawer and renders fetched transactions', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    drawer = createCategoryDrawer({ root: document, fetchFn });

    await drawer.open('Subscriptions', { month: '2026-06', color: '#6f6bd8' });

    expect(fetchFn).toHaveBeenCalledWith('Subscriptions', '2026-06');
    expect(panel().classList.contains('is-open')).toBe(true);
    expect(document.querySelector('.cat-drawer-title').textContent).toBe('Subscriptions');
    expect(document.querySelectorAll('.cat-drawer-row').length).toBe(1);
    expect(document.querySelector('.cat-drawer-desc').textContent).toBe('SYNTH SUB');
    expect(document.querySelector('.cat-drawer-sub').textContent).toContain('1 transaction');
  });

  it('renders an empty state when the category has no transactions', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ...CANNED, count: 0, transactions: [] });
    drawer = createCategoryDrawer({ root: document, fetchFn });

    await drawer.open('Rent', { month: '2026-06' });

    expect(document.querySelector('.cat-drawer-empty')).not.toBeNull();
    expect(document.querySelectorAll('.cat-drawer-row').length).toBe(0);
  });

  it('close() hides the drawer', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    drawer = createCategoryDrawer({ root: document, fetchFn });
    await drawer.open('Subscriptions', {});

    drawer.close();

    expect(panel().classList.contains('is-open')).toBe(false);
    expect(panel().getAttribute('aria-hidden')).toBe('true');
    expect(drawer.isOpen).toBe(false);
  });

  it('closes on the Escape key', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    drawer = createCategoryDrawer({ root: document, fetchFn });
    await drawer.open('Subscriptions', {});

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(drawer.isOpen).toBe(false);
  });

  it('closes when the backdrop is clicked', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    drawer = createCategoryDrawer({ root: document, fetchFn });
    await drawer.open('Subscriptions', {});

    backdrop().dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(drawer.isOpen).toBe(false);
  });

  it('closes when the close button is clicked', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    drawer = createCategoryDrawer({ root: document, fetchFn });
    await drawer.open('Subscriptions', {});

    document
      .querySelector('.cat-drawer-close')
      .dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(drawer.isOpen).toBe(false);
  });

  it('shows an error message when the fetch fails', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'));
    drawer = createCategoryDrawer({ root: document, fetchFn });

    await drawer.open('Subscriptions', {});

    expect(document.querySelector('.cat-drawer-sub').textContent).toContain('Could not load');
  });
});
