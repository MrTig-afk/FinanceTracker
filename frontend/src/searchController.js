/**
 * searchController.js — DOM wiring for the Search view (GET /search).
 *
 * A single text input runs a debounced full-text search over the owner's own
 * transactions and renders read-only rows (reusing the drawer's shared row DOM).
 *
 * PRIVACY: descriptions come from the owner's OWN local backend and are rendered
 * only in the owner's own client — nothing here is ever sent off-machine. Rows are
 * built via buildRowMain (textContent only), so a description can never inject HTML.
 * Mirrors the trendsController.js idiom (injectable fetchFn, _on/_listeners/destroy,
 * fixed safe error strings — raw errors/stacks are never surfaced).
 */

import { fetchSearch } from './api.js';
import { formatCurrency } from './summary.js';
import { buildRowMain } from './transactionRow.js';

const _HINT = 'Type to search your transactions.';
const _NO_RESULTS = 'No transactions match that search.';
const _ERROR = 'Could not run search.';

/**
 * Wire the Search view.
 *
 * Requires the following elements to be present in `root`:
 *   #search-input    (text/search input)
 *   #search-message  (hint / count / error banner)
 *   #search-results  (results container)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: (q: string, month?: string) => Promise<object>,
 *   debounceMs?: number,
 * }} options
 * @returns {{ load(): void, destroy(): void }}
 */
export function createSearch({ root = document, fetchFn = fetchSearch, debounceMs = 250 } = {}) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const inputEl = root.getElementById('search-input');
  const messageEl = root.getElementById('search-message');
  const resultsEl = root.getElementById('search-results');

  let _timer = null;
  // Monotonic token so a slow/out-of-order response can be discarded if the input
  // value moved on since the request was issued (stale-guard).
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

  function _clearResults() {
    if (resultsEl) resultsEl.textContent = '';
  }

  function _renderResults(data) {
    _clearResults();
    if (!resultsEl) return;
    for (const t of data.transactions ?? []) {
      const row = doc.createElement('div');
      row.className = 'cat-drawer-row';
      row.appendChild(buildRowMain(doc, t)); // read-only: no picker
      resultsEl.appendChild(row);
    }
    const count = data.count ?? (data.transactions ? data.transactions.length : 0);
    const noun = count === 1 ? 'transaction' : 'transactions';
    _setMessage(`${count} ${noun} · ${formatCurrency(data.total ?? '0')}`);
  }

  async function _run(query) {
    const token = ++_token;
    try {
      const data = await fetchFn(query);
      // Discard if the input changed since this request was issued.
      if (token !== _token) return;
      if ((data.transactions ?? []).length === 0) {
        _clearResults();
        _setMessage(_NO_RESULTS);
        return;
      }
      _renderResults(data);
    } catch {
      if (token !== _token) return;
      // Never expose raw error/stack — fixed safe message only.
      _clearResults();
      _setMessage(_ERROR);
    }
  }

  function _onInput() {
    if (_timer !== null) {
      clearTimeout(_timer);
      _timer = null;
    }
    const value = inputEl ? inputEl.value.trim() : '';
    if (!value) {
      // Blank input: no fetch, back to the hint state.
      _token++; // invalidate any in-flight response
      _clearResults();
      _setMessage(_HINT);
      return;
    }
    _timer = setTimeout(() => {
      _timer = null;
      _run(value);
    }, debounceMs);
  }

  _on(inputEl, 'input', _onInput);

  /** Reset to the initial hint state (called when the view is shown). */
  function load() {
    if (_timer !== null) {
      clearTimeout(_timer);
      _timer = null;
    }
    _token++; // invalidate any in-flight response
    if (inputEl) inputEl.value = '';
    _clearResults();
    _setMessage(_HINT);
  }

  function destroy() {
    if (_timer !== null) {
      clearTimeout(_timer);
      _timer = null;
    }
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { load, destroy };
}
