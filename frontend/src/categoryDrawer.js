/**
 * categoryDrawer.js — right slide-in drawer listing one category's transactions.
 *
 * PRIVACY: transaction descriptions come from the owner's OWN backend and are
 * rendered only in the owner's own client. Nothing here is sent off-machine. A
 * category override sends only the row id + chosen canonical label (never the
 * description) to the owner's own local backend.
 *
 * The drawer builds its own DOM (backdrop + panel) once and appends it to
 * <body>, so index.html needs no dedicated markup. Visibility is driven purely
 * by the `.is-open` class (CSS handles the slide + fade, both directions).
 */

import { fetchCategoryTransactions, postCategoryOverride } from './api.js';
import { formatCurrency } from './summary.js';

/**
 * The 8 canonical taxonomy labels an owner can reassign a transaction to.
 * Mirrors backend/store/taxonomy.py TAXONOMY. 'Uncategorised' is deliberately
 * NOT here — it is a NULL-category view label, never a real override target.
 */
const TAXONOMY_LABELS = [
  'Groceries',
  'Housing',
  'Dining Out',
  'Transport',
  'Entertainment',
  'Subscriptions',
  'Income',
  'Other',
];

/**
 * @param {{
 *   root?: Document,
 *   fetchFn?: (category: string, month?: string) => Promise<object>,
 *   overrideFn?: (id: number, category: string) => Promise<object>,
 *   onChanged?: (summary: object) => void,
 * }} options
 * @returns {{ open(category: string, opts?: {month?: string, color?: string}): Promise<void>,
 *             close(): void, destroy(): void, readonly isOpen: boolean }}
 */
export function createCategoryDrawer({
  root = document,
  fetchFn = fetchCategoryTransactions,
  overrideFn = postCategoryOverride,
  onChanged = null,
} = {}) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;
  const host = root.body ?? doc.body;

  // --- Build DOM once -------------------------------------------------------
  const backdrop = doc.createElement('div');
  backdrop.className = 'cat-drawer-backdrop';

  const panel = doc.createElement('aside');
  panel.className = 'cat-drawer';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-label', 'Category transactions');
  panel.setAttribute('aria-hidden', 'true');

  const header = doc.createElement('div');
  header.className = 'cat-drawer-header';

  const dot = doc.createElement('span');
  dot.className = 'cat-drawer-dot';

  const titleWrap = doc.createElement('div');
  titleWrap.className = 'cat-drawer-titlewrap';
  const title = doc.createElement('h2');
  title.className = 'cat-drawer-title';
  const sub = doc.createElement('p');
  sub.className = 'cat-drawer-sub';
  titleWrap.appendChild(title);
  titleWrap.appendChild(sub);

  const closeBtn = doc.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'cat-drawer-close';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '×'; // ×

  header.appendChild(dot);
  header.appendChild(titleWrap);
  header.appendChild(closeBtn);

  const listWrap = doc.createElement('div');
  listWrap.className = 'cat-drawer-body';

  panel.appendChild(header);
  panel.appendChild(listWrap);
  host.appendChild(backdrop);
  host.appendChild(panel);

  let isOpen = false;
  let lastFocused = null;

  // The category/month the drawer is currently showing — needed so an override
  // can re-fetch the SAME view (dropping the corrected row) and re-render.
  let currentCategory = null;
  let currentMonth;
  let currentColor;

  // --- Rendering ------------------------------------------------------------
  function _renderRow(t) {
    const row = doc.createElement('div');
    row.className = 'cat-drawer-row';

    const main = doc.createElement('div');
    main.className = 'cat-drawer-row-main';

    const date = doc.createElement('span');
    date.className = 'cat-drawer-date';
    date.textContent = t.date;

    const desc = doc.createElement('span');
    desc.className = 'cat-drawer-desc';
    desc.textContent = t.description;

    const amount = doc.createElement('span');
    amount.className = 'cat-drawer-amount';
    amount.textContent = formatCurrency(t.amount);
    const n = Number(t.amount);
    amount.classList.toggle('is-negative', Number.isFinite(n) && n < 0);
    amount.classList.toggle('is-positive', Number.isFinite(n) && n >= 0);

    main.appendChild(date);
    main.appendChild(desc);
    main.appendChild(amount);

    // --- Category picker (manual correction) --------------------------------
    const picker = doc.createElement('select');
    picker.className = 'cat-drawer-picker';
    picker.setAttribute('aria-label', 'Change category for this transaction');

    const inTaxonomy = TAXONOMY_LABELS.includes(currentCategory);
    if (!inTaxonomy) {
      // e.g. the 'Uncategorised' view: no canonical option is pre-selected.
      const placeholder = doc.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Choose category';
      placeholder.disabled = true;
      placeholder.selected = true;
      picker.appendChild(placeholder);
    }
    for (const label of TAXONOMY_LABELS) {
      const opt = doc.createElement('option');
      opt.value = label;
      opt.textContent = label;
      picker.appendChild(opt);
    }
    if (inTaxonomy) picker.value = currentCategory;

    const error = doc.createElement('span');
    error.className = 'cat-drawer-row-error';
    error.setAttribute('role', 'alert');
    error.hidden = true;

    picker.addEventListener('change', async () => {
      const newCategory = picker.value;
      if (!newCategory || newCategory === currentCategory) return;

      picker.disabled = true;
      error.hidden = true;
      error.textContent = '';

      try {
        const summary = await overrideFn(t.id, newCategory);
        if (typeof onChanged === 'function') onChanged(summary);
        // Re-fetch the SAME category view so the corrected row drops out.
        await _load(currentCategory, currentMonth);
      } catch {
        picker.disabled = false;
        picker.value = inTaxonomy ? currentCategory : '';
        error.textContent = 'Could not update category.';
        error.hidden = false;
      }
    });

    main.appendChild(picker);
    row.appendChild(main);
    row.appendChild(error);
    return row;
  }

  function _renderList(txns) {
    listWrap.textContent = '';
    if (!txns || txns.length === 0) {
      const empty = doc.createElement('p');
      empty.className = 'cat-drawer-empty';
      empty.textContent = 'No transactions in this category.';
      listWrap.appendChild(empty);
      return;
    }
    for (const t of txns) {
      listWrap.appendChild(_renderRow(t));
    }
  }

  function _onKeydown(e) {
    if (e.key === 'Escape') close();
  }

  // Fetch + render one category/month view. Shared by open() and the post-
  // override refresh so both paths stay in sync.
  function _load(category, month) {
    return Promise.resolve()
      .then(() => fetchFn(category, month))
      .then((data) => {
        if (!isOpen) return; // user closed it while the request was in flight
        const count =
          data.count ?? (data.transactions ? data.transactions.length : 0);
        const noun = count === 1 ? 'transaction' : 'transactions';
        sub.textContent = `${formatCurrency(data.total ?? '0')} · ${count} ${noun}`;
        _renderList(data.transactions || []);
      })
      .catch(() => {
        if (isOpen) sub.textContent = 'Could not load transactions.';
      });
  }

  // --- Public API -----------------------------------------------------------
  function open(category, { month, color } = {}) {
    lastFocused = doc.activeElement;

    currentCategory = category;
    currentMonth = month;
    currentColor = color;

    // Immediate header state, then fetch.
    title.textContent = category;
    sub.textContent = 'Loading...';
    dot.style.background = color || 'var(--accent)';
    _renderList([]);

    isOpen = true;
    panel.classList.add('is-open');
    backdrop.classList.add('is-open');
    panel.setAttribute('aria-hidden', 'false');
    doc.addEventListener('keydown', _onKeydown);
    if (typeof closeBtn.focus === 'function') closeBtn.focus();

    return _load(category, month);
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    panel.classList.remove('is-open');
    backdrop.classList.remove('is-open');
    panel.setAttribute('aria-hidden', 'true');
    doc.removeEventListener('keydown', _onKeydown);
    if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
  }

  function destroy() {
    doc.removeEventListener('keydown', _onKeydown);
    backdrop.remove();
    panel.remove();
  }

  backdrop.addEventListener('click', close);
  closeBtn.addEventListener('click', close);

  return {
    open,
    close,
    destroy,
    get isOpen() {
      return isOpen;
    },
  };
}
