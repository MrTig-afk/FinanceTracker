/**
 * transfersController.js — DOM wiring for the Transfers view (GET /transfers).
 *
 * Lists the internal cross-bank transfer pairs the backend has matched and excluded
 * from spending, each with a "Not a transfer" button that dismisses the match and
 * restores the two legs' categories (POST /transfers/{id}/untag).
 *
 * Opening the view also marks it seen (POST /transfers/seen, fire-and-forget) so the
 * unseen-count nav badge clears; an optimistic onSeen() clears the pill synchronously.
 *
 * PRIVACY: descriptions/amounts come from the owner's OWN local backend and are
 * rendered only in the owner's own client — nothing here is ever sent off-machine.
 * Rows are built via buildRowMain (textContent only), so a description can never
 * inject HTML. Mirrors the searchController.js idiom (injectable fns,
 * _on/_listeners/destroy, fixed safe error strings, stale-guard token on load).
 */

import { fetchTransfers, postTransferUntag, postTransfersSeen } from './api.js';
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
 *   #transfers-card     (card around the list; hidden while there are no pairs,
 *                        so no empty gray box sits under the banner)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: () => Promise<object>,
 *   untagFn?: (pairId: number) => Promise<object>,
 *   toastFn?: ((spec: {title?: string, body?: string, kind?: string}) => void)|null,
 *   seenFn?: () => Promise<object>,
 *   onSeen?: (() => void)|null,
 * }} options
 * @returns {{ load(): void, destroy(): void }}
 */
export function createTransfers({
  root = document,
  fetchFn = fetchTransfers,
  untagFn = postTransferUntag,
  toastFn = null,
  seenFn = postTransfersSeen,
  onSeen = null,
} = {}) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const messageEl = root.getElementById('transfers-message');
  const listEl = root.getElementById('transfers-list');
  const cardEl = root.getElementById('transfers-card');

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
    if (cardEl) cardEl.hidden = true;
  }

  function _bankLabel(bank) {
    return _BANK_LABEL[bank] ?? bank;
  }

  function _legLabel(text) {
    const label = doc.createElement('span');
    label.className = 'transfer-leg-label';
    label.textContent = text;
    return label;
  }

  // Where an untagged leg went, for the confirmation toast. null = the leg had no
  // category before it was tagged (it reappears as Uncategorised until the next run).
  function _restoredName(category) {
    return category ?? 'Uncategorised';
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
      card.appendChild(_legLabel(`From ${_bankLabel(pair.out.bank)}`));
      const outRow = doc.createElement('div');
      outRow.className = 'cat-drawer-row';
      outRow.appendChild(buildRowMain(doc, pair.out));
      card.appendChild(outRow);
    }
    if (pair.in) {
      card.appendChild(_legLabel(`To ${_bankLabel(pair.in.bank)}`));
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
      const result = await untagFn(pair.id);
      // Tell the owner where each leg went — an untagged pair vanishing from this
      // list with no explanation reads as data loss.
      if (typeof toastFn === 'function') {
        const restored = result && result.restored_to ? result.restored_to : {};
        const outName = _restoredName(restored.out);
        const inName = _restoredName(restored.in);
        const uncategorised = outName === 'Uncategorised' || inName === 'Uncategorised';
        const where = `${_bankLabel(pair.out?.bank)} leg -> ${outName} · ${_bankLabel(pair.in?.bank)} leg -> ${inName}.`;
        const note = uncategorised
          ? ' Uncategorised rows are sorted on the next run or via Settings -> retry.'
          : '';
        toastFn({ title: 'Not a transfer', body: `${where}${note}`, kind: 'info' });
      }
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
    if (cardEl) cardEl.hidden = false;
    for (const pair of data.pairs ?? []) {
      listEl.appendChild(_renderPair(pair));
    }
    const count = data.count ?? (data.pairs ? data.pairs.length : 0);
    const noun = count === 1 ? 'pair' : 'pairs';
    _setMessage(`${count} matched ${noun} excluded from spending`);
  }

  /** Fetch and render the current transfer pairs. */
  function load() {
    // Mark the view seen: optimistic badge clear now, fire-and-forget POST after.
    // A seen-marker failure must never affect rendering — the badge resyncs from the
    // next /summary fetch. The Promise wrapper also swallows a synchronously-throwing
    // injected seenFn.
    if (typeof onSeen === 'function') onSeen();
    Promise.resolve().then(() => seenFn()).catch(() => {});
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
