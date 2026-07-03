/**
 * transfersController.js — DOM wiring for the Transfers view (GET /transfers).
 *
 * Lists the internal cross-bank transfer pairs the backend has matched and excluded
 * from spending, each with a "Not a transfer" button that dismisses the match and
 * restores the two legs' categories (POST /transfers/{id}/untag).
 *
 * PRIVACY: descriptions/amounts come from the owner's OWN local backend and are
 * rendered only in the owner's own client — nothing here is ever sent off-machine.
 * Rows are built via buildRowMain (textContent only), so a description can never
 * inject HTML. Mirrors the searchController.js idiom (injectable fns,
 * _on/_listeners/destroy, fixed safe error strings, stale-guard token on load).
 */

import { fetchTransfers, postTransferUntag } from './api.js';
import { formatCurrency } from './summary.js';
import { buildRowMain } from './transactionRow.js';

const _EMPTY = 'No transfers detected between your accounts.';
const _LOAD_ERROR = 'Could not load transfers.';
const _UNTAG_ERROR = 'Could not untag this pair.';

// Bank.value -> display name for the pair caption. Local, no transaction data.
const _BANK_LABEL = { commbank: 'CommBank', westpac: 'Westpac' };

/**
 * Wire the Transfers view.
 *
 * Requires the following elements to be present in `root`:
 *   #transfers-message  (empty / count / error banner)
 *   #transfers-list     (pairs container)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: () => Promise<object>,
 *   untagFn?: (pairId: number) => Promise<object>,
 * }} options
 * @returns {{ load(): void, destroy(): void }}
 */
export function createTransfers({
  root = document,
  fetchFn = fetchTransfers,
  untagFn = postTransferUntag,
} = {}) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const messageEl = root.getElementById('transfers-message');
  const listEl = root.getElementById('transfers-list');

  // Monotonic token so a slow/out-of-order response is discarded if load() ran again.
  let _token = 0;

  const _listeners = [];
  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

  function _setMessage(text) {
    if (messageEl) messageEl.textContent = text;
  }

  function _clearList() {
    if (listEl) listEl.textContent = '';
  }

  function _bankLabel(bank) {
    return _BANK_LABEL[bank] ?? bank;
  }

  function _renderPair(pair) {
    const card = doc.createElement('div');
    card.className = 'transfer-pair';

    const caption = doc.createElement('div');
    caption.className = 'transfer-pair-caption';
    const outBank = _bankLabel(pair.out ? pair.out.bank : '');
    const inBank = _bankLabel(pair.in ? pair.in.bank : '');
    // Plain '->' arrow, no emojis; formatCurrency handles the amount.
    caption.textContent = `${outBank} -> ${inBank} · ${formatCurrency(pair.amount)}`;
    card.appendChild(caption);

    if (pair.out) {
      const outRow = doc.createElement('div');
      outRow.className = 'cat-drawer-row';
      outRow.appendChild(buildRowMain(doc, pair.out));
      card.appendChild(outRow);
    }
    if (pair.in) {
      const inRow = doc.createElement('div');
      inRow.className = 'cat-drawer-row';
      inRow.appendChild(buildRowMain(doc, pair.in));
      card.appendChild(inRow);
    }

    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'transfer-untag';
    button.textContent = 'Not a transfer';
    _on(button, 'click', () => _untag(pair, button, caption));
    card.appendChild(button);

    return card;
  }

  async function _untag(pair, button, caption) {
    button.disabled = true;
    try {
      await untagFn(pair.id);
      load();
    } catch {
      // Never expose raw error/stack — fixed safe message only. Re-enable so the
      // owner can retry.
      button.disabled = false;
      const err = doc.createElement('span');
      err.className = 'cat-drawer-row-error';
      err.setAttribute('role', 'alert');
      err.textContent = _UNTAG_ERROR;
      caption.appendChild(err);
    }
  }

  function _render(data) {
    _clearList();
    if (!listEl) return;
    for (const pair of data.pairs ?? []) {
      listEl.appendChild(_renderPair(pair));
    }
    const count = data.count ?? (data.pairs ? data.pairs.length : 0);
    const noun = count === 1 ? 'pair' : 'pairs';
    _setMessage(`${count} matched ${noun} excluded from spending`);
  }

  /** Fetch and render the current transfer pairs. */
  function load() {
    const token = ++_token;
    fetchFn()
      .then((data) => {
        if (token !== _token) return;
        if ((data.pairs ?? []).length === 0) {
          _clearList();
          _setMessage(_EMPTY);
          return;
        }
        _render(data);
      })
      .catch(() => {
        if (token !== _token) return;
        _clearList();
        _setMessage(_LOAD_ERROR);
      });
  }

  function destroy() {
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { load, destroy };
}
