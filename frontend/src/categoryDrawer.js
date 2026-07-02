/**
 * categoryDrawer.js — right slide-in drawer listing one category's transactions.
 *
 * PRIVACY: transaction descriptions come from the owner's OWN backend and are
 * rendered only in the owner's own client. Nothing here is sent off-machine.
 *
 * The drawer builds its own DOM (backdrop + panel) once and appends it to
 * <body>, so index.html needs no dedicated markup. Visibility is driven purely
 * by the `.is-open` class (CSS handles the slide + fade, both directions).
 */

import { fetchCategoryTransactions } from './api.js';
import { formatCurrency } from './summary.js';

/**
 * @param {{
 *   root?: Document,
 *   fetchFn?: (category: string, month?: string) => Promise<object>,
 * }} options
 * @returns {{ open(category: string, opts?: {month?: string, color?: string}): Promise<void>,
 *             close(): void, destroy(): void, readonly isOpen: boolean }}
 */
export function createCategoryDrawer({
  root = document,
  fetchFn = fetchCategoryTransactions,
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

  // --- Rendering ------------------------------------------------------------
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
      const row = doc.createElement('div');
      row.className = 'cat-drawer-row';

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

      row.appendChild(date);
      row.appendChild(desc);
      row.appendChild(amount);
      listWrap.appendChild(row);
    }
  }

  function _onKeydown(e) {
    if (e.key === 'Escape') close();
  }

  // --- Public API -----------------------------------------------------------
  function open(category, { month, color } = {}) {
    lastFocused = doc.activeElement;

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
