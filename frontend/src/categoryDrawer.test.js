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
    { id: 7, date: '2026-06-12', description: 'SYNTH SUB', amount: '-170.01', bank: 'commbank' },
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

// ---------------------------------------------------------------------------
// Category picker — manual override (SYNTHETIC data only)
// ---------------------------------------------------------------------------

const picker = () => document.querySelector('.cat-drawer-picker');

describe('createCategoryDrawer — category picker', () => {
  it('renders a picker per row with the current category pre-selected', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    drawer = createCategoryDrawer({ root: document, fetchFn, overrideFn: vi.fn() });

    await drawer.open('Subscriptions', { month: '2026-06' });

    expect(document.querySelectorAll('.cat-drawer-picker').length).toBe(1);
    expect(picker().value).toBe('Subscriptions');
    // The 8 canonical taxonomy labels are offered as options.
    const labels = [...picker().options].map((o) => o.value).filter(Boolean);
    expect(labels).toEqual([
      'Groceries', 'Housing', 'Dining Out', 'Transport',
      'Entertainment', 'Subscriptions', 'Income', 'Other',
    ]);
    expect(picker().getAttribute('aria-label')).toContain('category');
  });

  it('shows no pre-selected canonical option for the Uncategorised view', async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ...CANNED,
      category: 'Uncategorised',
      transactions: [
        { id: 9, date: '2026-06-01', description: 'SYNTH MYSTERY', amount: '-5.00', bank: 'westpac' },
      ],
    });
    drawer = createCategoryDrawer({ root: document, fetchFn, overrideFn: vi.fn() });

    await drawer.open('Uncategorised', { month: '2026-06' });

    // A disabled placeholder ('') is selected, not a real category.
    expect(picker().value).toBe('');
  });

  it('changing the picker calls overrideFn with (id, newCategory)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    const overrideFn = vi.fn().mockResolvedValue({ totals: {}, net: '0.00', count: 0 });
    drawer = createCategoryDrawer({ root: document, fetchFn, overrideFn });

    await drawer.open('Subscriptions', { month: '2026-06' });

    picker().value = 'Dining Out';
    picker().dispatchEvent(new Event('change'));
    await Promise.resolve();
    await Promise.resolve();

    expect(overrideFn).toHaveBeenCalledWith(7, 'Dining Out');
  });

  it('on success re-fetches the same category and fires onChanged(summary)', async () => {
    const UPDATED = { totals: { Groceries: '-1.00' }, net: '-1.00', count: 1 };
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    const overrideFn = vi.fn().mockResolvedValue(UPDATED);
    const onChanged = vi.fn();
    drawer = createCategoryDrawer({ root: document, fetchFn, overrideFn, onChanged });

    await drawer.open('Subscriptions', { month: '2026-06' });
    expect(fetchFn).toHaveBeenCalledTimes(1);

    picker().value = 'Dining Out';
    picker().dispatchEvent(new Event('change'));
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(onChanged).toHaveBeenCalledWith(UPDATED);
    // Re-fetched the SAME category so the corrected row drops out of the view.
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(fetchFn).toHaveBeenLastCalledWith('Subscriptions', '2026-06');
  });

  it('on failure shows an inline message and does not fire onChanged', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED);
    const overrideFn = vi.fn().mockRejectedValue(new Error('boom'));
    const onChanged = vi.fn();
    drawer = createCategoryDrawer({ root: document, fetchFn, overrideFn, onChanged });

    await drawer.open('Subscriptions', { month: '2026-06' });

    picker().value = 'Dining Out';
    picker().dispatchEvent(new Event('change'));
    await Promise.resolve();
    await Promise.resolve();

    const err = document.querySelector('.cat-drawer-row-error');
    expect(err).not.toBeNull();
    expect(err.hidden).toBe(false);
    expect(err.textContent).toContain('Could not update');
    expect(onChanged).not.toHaveBeenCalled();
    // The list was not re-fetched (override failed before the refresh).
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });
});
