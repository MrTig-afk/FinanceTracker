/**
 * transactionRow.js — shared, pure builder for one transaction's inner row DOM.
 *
 * Extracted from categoryDrawer.js so the category drawer AND the Search view can
 * render an identical row without duplicating markup. Pure: no network, no state.
 *
 * PRIVACY: descriptions are the owner's OWN data, rendered only in the owner's own
 * client. All text is set via `textContent` (never innerHTML), so a description can
 * never inject HTML.
 */

import { formatCurrency } from './summary.js';

/**
 * Build the `.cat-drawer-row-main` element (date + description + amount) for one
 * transaction. Class names and the is-negative/is-positive amount toggle match the
 * drawer's original inline construction byte-for-byte.
 *
 * @param {Document} doc  The owning document (so it works under jsdom / any root).
 * @param {{date: string, description: string, amount: string|number}} t
 * @returns {HTMLDivElement} the `.cat-drawer-row-main` element.
 */
export function buildRowMain(doc, t) {
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

  return main;
}
