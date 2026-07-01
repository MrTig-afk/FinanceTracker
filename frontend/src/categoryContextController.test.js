/**
 * categoryContextController.test.js — DOM wiring tests for categoryContextController.js.
 * jsdom provides the DOM. No real network — fetchFn/saveFn are injected.
 * All fixtures are SYNTHETIC category names/hints (D1's 9 fixed categories with
 * invented hint text) — never real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createCategoryContext } from './categoryContextController.js';

// ---------------------------------------------------------------------------
// Minimal context-view HTML (mirrors the contract in index.html — no
// generated-prompt preview panel).
// ---------------------------------------------------------------------------

const CONTEXT_HTML = `
  <button id="save-context" type="button">Save context</button>
  <div id="category-cards"></div>
  <p id="context-status" role="status"></p>
`;

// SYNTHETIC 9-category fixture — mirrors D1's fixed taxonomy shape but with
// invented hint text (never the real D2 defaults, never transaction data).
const SYNTH_NAMES = [
  'Groceries', 'Utilities', 'Rent', 'Dining Out', 'Transport',
  'Entertainment', 'Subscriptions', 'Income', 'Other',
];

function synthCategories() {
  return SYNTH_NAMES.map((name, i) => ({
    name,
    color: `#${(i + 1).toString(16).padStart(6, '0')}`,
    hints: `SYNTH HINT ${i}`,
    position: i,
  }));
}

let controller;

beforeEach(() => {
  document.body.innerHTML = CONTEXT_HTML;
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// load() — renders exactly the 9 fixed cards, no add/remove buttons, no
// generated-prompt preview panel
// ---------------------------------------------------------------------------

describe('load()', () => {
  it('renders exactly 9 category cards', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });

    await controller.load();

    expect(document.querySelectorAll('.category-card').length).toBe(9);
  });

  it('renders no remove (×) buttons', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });

    await controller.load();

    const buttons = Array.from(document.querySelectorAll('.category-card button'));
    expect(buttons.length).toBe(0);
  });

  it('renders no "+ Add category" control', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });

    await controller.load();

    expect(document.body.textContent).not.toContain('Add category');
  });

  it('renders the category name as a non-editable label (not an input)', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });

    await controller.load();

    const nameEls = document.querySelectorAll('.category-card-name');
    expect(nameEls.length).toBe(9);
    for (const el of nameEls) {
      expect(el.tagName).not.toBe('INPUT');
    }
    expect(nameEls[0].textContent).toBe('Groceries');
  });

  it('pre-fills each textarea with the stored hints', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });

    await controller.load();

    const textareas = document.querySelectorAll('.category-card-hints');
    expect(textareas[0].value).toBe('SYNTH HINT 0');
  });

  it('does not render the generated-prompt preview panel', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });

    await controller.load();

    expect(document.getElementById('prompt-preview')).toBeNull();
    expect(document.getElementById('copy-prompt')).toBeNull();
    expect(document.getElementById('prompt-chars')).toBeNull();
    // Cards + Save still render alongside the removed preview panel.
    expect(document.querySelectorAll('.category-card').length).toBe(9);
    expect(document.getElementById('save-context')).not.toBeNull();
  });

  it('shows a safe status message on fetch failure', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('network error'));
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });

    await controller.load();

    expect(document.getElementById('context-status').textContent).not.toBe('');
    expect(document.querySelectorAll('.category-card').length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Editing a hints textarea updates the in-memory state used on save
// ---------------------------------------------------------------------------

describe('editing hints', () => {
  it('updates the in-memory hints on textarea input', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    const saveFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn });
    await controller.load();

    const textarea = document.querySelectorAll('.category-card-hints')[0];
    textarea.value = 'SYNTH EDITED HINT';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));

    document.getElementById('save-context').click();
    await new Promise((r) => setTimeout(r, 0));

    const sent = saveFn.mock.calls[0][0];
    expect(sent[0].hints).toBe('SYNTH EDITED HINT');
  });

  it('does not touch other categories when one textarea is edited', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });
    await controller.load();

    const textareas = document.querySelectorAll('.category-card-hints');
    textareas[0].value = 'SYNTH EDITED HINT';
    textareas[0].dispatchEvent(new Event('input', { bubbles: true }));

    expect(textareas[1].value).toBe('SYNTH HINT 1');
  });
});

// ---------------------------------------------------------------------------
// #save-context
// ---------------------------------------------------------------------------

describe('#save-context', () => {
  it('calls saveFn with the 9 {name, hints} items', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    const saveFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn });
    await controller.load();

    document.getElementById('save-context').click();
    await new Promise((r) => setTimeout(r, 0));

    expect(saveFn).toHaveBeenCalledOnce();
    const sent = saveFn.mock.calls[0][0];
    expect(sent.length).toBe(9);
    for (const item of sent) {
      expect(Object.keys(item).sort()).toEqual(['hints', 'name']);
    }
  });

  it('shows "Saved ✓" on success', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    const saveFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn });
    await controller.load();

    document.getElementById('save-context').click();
    await new Promise((r) => setTimeout(r, 0));

    expect(document.getElementById('save-context').textContent).toContain('Saved');
  });

  it('shows a safe inline message on failure (never echoes response body)', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    const saveFn = vi.fn().mockRejectedValue(new Error('SYNTH_SERVER_DETAIL_MUST_NOT_LEAK'));
    controller = createCategoryContext({ root: document, fetchFn, saveFn });
    await controller.load();

    document.getElementById('save-context').click();
    await new Promise((r) => setTimeout(r, 0));

    const status = document.getElementById('context-status').textContent;
    expect(status).not.toBe('');
    expect(status).not.toContain('SYNTH_SERVER_DETAIL_MUST_NOT_LEAK');
  });

  it('sends the edited (unsaved) hints, reflecting current in-memory state', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    const saveFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn });
    await controller.load();

    const textarea = document.querySelectorAll('.category-card-hints')[0];
    textarea.value = 'SYNTH EDITED HINT';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));

    document.getElementById('save-context').click();
    await new Promise((r) => setTimeout(r, 0));

    const sent = saveFn.mock.calls[0][0];
    expect(sent[0].hints).toBe('SYNTH EDITED HINT');
  });
});

// ---------------------------------------------------------------------------
// destroy
// ---------------------------------------------------------------------------

describe('destroy', () => {
  it('is callable without throwing', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ categories: synthCategories() });
    controller = createCategoryContext({ root: document, fetchFn, saveFn: vi.fn() });
    await controller.load();

    expect(() => controller.destroy()).not.toThrow();
    controller = null;
  });
});
