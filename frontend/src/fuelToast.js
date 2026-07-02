/**
 * fuelToast.js — top-right slide-in toast confirming the fuel-rule toggle.
 *
 * Replaces the old static bottom note. A single toast lives at a time: each
 * show() replaces any current one, slides in from the top-right, runs a ~5s
 * countdown bar, then fades out and removes itself. Fires only on the user's
 * toggle action (wired in main.js) — never on initial render/page-load.
 *
 * No IO, no network. DOM-only, so it is testable against jsdom.
 */

import { formatCurrency } from './summary.js';

const DEFAULT_TIMEOUT_MS = 5000; // matches the 5s CSS countdown bar
const DEFAULT_FADE_MS = 450; // matches the toast fade-out transition

/**
 * Create the fuel-rule toast controller bound to the given DOM root.
 * Lazily creates a fixed top-right region if one is not already present.
 *
 * @param {Document} [root]
 * @param {{ timeoutMs?: number, fadeMs?: number }} [opts]
 * @returns {{ show(on: boolean, meta?: { count?: number|string, amount?: number|string }): (HTMLElement|null), destroy(): void }}
 */
export function createFuelToast(
  root = document,
  { timeoutMs = DEFAULT_TIMEOUT_MS, fadeMs = DEFAULT_FADE_MS } = {},
) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  let region = root.getElementById('fuel-toast-region');
  if (!region) {
    region = doc.createElement('div');
    region.id = 'fuel-toast-region';
    region.className = 'toast-region';
    region.setAttribute('aria-live', 'polite');
    (doc.body ?? doc.documentElement).appendChild(region);
  }

  /** @type {HTMLElement|null} */
  let current = null;
  let dismissTimer = null;
  let removeTimer = null;

  function _clearTimers() {
    if (dismissTimer !== null) {
      clearTimeout(dismissTimer);
      dismissTimer = null;
    }
    if (removeTimer !== null) {
      clearTimeout(removeTimer);
      removeTimer = null;
    }
  }

  function _removeCurrent() {
    if (current && current.parentNode) current.remove();
    current = null;
  }

  /**
   * Show (or replace) the toast.
   * @param {boolean} on  true = rule applied (Dining Out), false = reverted (Transport)
   * @param {{ count?: number|string, amount?: number|string }} [meta]
   */
  function show(on, { count = 0, amount = '0.00' } = {}) {
    // Single instance: drop any current toast and its pending timers first.
    _clearTimers();
    _removeCurrent();

    const n = Number(count) || 0;
    const noun = n === 1 ? 'purchase' : 'purchases';

    const el = doc.createElement('div');
    el.className = on ? 'toast' : 'toast toast--off';

    const accent = doc.createElement('span');
    accent.className = 'toast-accent';

    const body = doc.createElement('div');
    body.className = 'toast-body';

    const title = doc.createElement('div');
    title.className = 'toast-title';
    const dot = doc.createElement('span');
    dot.className = 'dot';
    title.appendChild(dot);
    title.appendChild(
      doc.createTextNode(on ? 'Moved to Dining Out' : 'Kept under Transport'),
    );

    const text = doc.createElement('div');
    text.className = 'toast-text';
    const strong = doc.createElement('strong');
    strong.textContent = `${n} small servo ${noun}`;
    text.appendChild(strong);
    if (on) {
      const money = formatCurrency(Math.abs(Number(amount) || 0));
      text.appendChild(
        doc.createTextNode(` (${money}) moved to Dining Out and saved to your ledger.`),
      );
    } else {
      text.appendChild(doc.createTextNode(' stay under Transport.'));
    }

    body.appendChild(title);
    body.appendChild(text);

    const timer = doc.createElement('span');
    timer.className = 'toast-timer';

    el.appendChild(accent);
    el.appendChild(body);
    el.appendChild(timer);

    region.appendChild(el);
    current = el;

    // Enter on the next frame so the CSS transition from translateX(24px)+
    // opacity 0 actually runs.
    requestAnimationFrame(() => {
      el.classList.add('show');
    });

    dismissTimer = setTimeout(() => {
      dismissTimer = null;
      el.classList.remove('show');
      removeTimer = setTimeout(() => {
        removeTimer = null;
        if (el.parentNode) el.remove();
        if (current === el) current = null;
      }, fadeMs);
    }, timeoutMs);

    return el;
  }

  /** Cancel any pending timers and remove the toast + region. */
  function destroy() {
    _clearTimers();
    _removeCurrent();
    if (region.parentNode) region.remove();
  }

  return { show, destroy };
}
