/**
 * categoryContextController.js — DOM wiring for the Category context view.
 * PRIVACY: category names + hints are the owner's own local notes, never
 * transaction data. Persistence is the backend only — NO localStorage here.
 * D1 (fixed taxonomy): 9 fixed cards, static name label, hints textarea only —
 * no add/remove/rename handlers of any kind.
 */

import { fetchCategoryContext, saveCategoryContext } from './api.js';

const SAVE_LABEL = 'Save context';
const SAVE_DONE_LABEL = 'Saved ✓';

/**
 * Wire the Category context view.
 *
 * Requires the following elements to be present in `root`:
 *   #category-cards   (container for the 9 fixed cards)
 *   #save-context      (button)
 *   #context-status    (optional — inline error/status text)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: () => Promise<{categories: Array}>,
 *   saveFn?: (categories: Array) => Promise<object>,
 * }} options
 * @returns {{ load(): Promise<void>, destroy(): void }}
 */
export function createCategoryContext({
  root = document,
  fetchFn,
  saveFn,
} = {}) {
  const _fetchFn = fetchFn ?? fetchCategoryContext;
  const _saveFn = saveFn ?? saveCategoryContext;

  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const cardsEl = root.getElementById('category-cards');
  const saveBtn = root.getElementById('save-context');
  const statusEl = root.getElementById('context-status');

  /** In-memory working copy of the 9 categories: [{name, color, hints, position}] */
  let cats = [];
  let saveTimer = null;

  const _listeners = [];
  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

  function _setStatus(msg, isError = false) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.classList.toggle('context-status--error', isError);
  }

  function _renderCards() {
    if (!cardsEl) return;
    cardsEl.textContent = '';

    for (const cat of cats) {
      const card = doc.createElement('div');
      card.className = 'category-card';

      const head = doc.createElement('div');
      head.className = 'category-card-head';

      const dot = doc.createElement('span');
      dot.className = 'category-card-dot';
      dot.style.background = cat.color;

      const name = doc.createElement('span');
      name.className = 'category-card-name';
      name.textContent = cat.name;

      head.appendChild(dot);
      head.appendChild(name);

      const textarea = doc.createElement('textarea');
      textarea.className = 'category-card-hints';
      textarea.placeholder = 'Merchants and notes';
      textarea.value = cat.hints;
      textarea.addEventListener('input', () => {
        cat.hints = textarea.value;
      });
      textarea.addEventListener('change', () => {
        cat.hints = textarea.value;
      });

      card.appendChild(head);
      card.appendChild(textarea);
      cardsEl.appendChild(card);
    }
  }

  async function load() {
    _setStatus('');
    try {
      const data = await _fetchFn();
      cats = (data.categories ?? []).map((c) => ({ ...c }));
    } catch {
      cats = [];
      _setStatus('Could not load category context.', true);
    }
    _renderCards();
  }

  async function _handleSave() {
    try {
      await _saveFn(cats.map((c) => ({ name: c.name, hints: c.hints })));
      if (saveBtn) saveBtn.textContent = SAVE_DONE_LABEL;
      _setStatus('');
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        if (saveBtn) saveBtn.textContent = SAVE_LABEL;
      }, 1600);
    } catch {
      _setStatus('Save failed. Please try again.', true);
    }
  }

  _on(saveBtn, 'click', _handleSave);

  function destroy() {
    clearTimeout(saveTimer);
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { load, destroy };
}
