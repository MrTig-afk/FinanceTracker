/**
 * fuelToast.js — top-right slide-in toast confirming the fuel-rule toggle.
 *
 * Thin wrapper over the reusable createToast() controller (toast.js): it owns
 * only the fuel-specific copy (ON = "Moved to Dining Out", OFF = "Kept under
 * Transport") and delegates all DOM/lifecycle work to the shared toast so the
 * fuel rule and push-notification toasts share one visual language.
 *
 * A single toast lives at a time: each show() replaces any current one, slides
 * in from the top-right, runs a ~5s countdown bar, then fades out and removes
 * itself. Fires only on the user's toggle action (wired in main.js) — never on
 * initial render/page-load.
 *
 * No IO, no network. DOM-only, so it is testable against jsdom.
 */

import { formatCurrency } from './summary.js';
import { createToast } from './toast.js';

/**
 * Create the fuel-rule toast controller bound to the given DOM root.
 * Lazily creates a fixed top-right region (#fuel-toast-region) if absent.
 *
 * @param {Document} [root]
 * @param {{ timeoutMs?: number, fadeMs?: number }} [opts]
 * @returns {{ show(on: boolean, meta?: { count?: number|string, amount?: number|string }): (HTMLElement|null), destroy(): void }}
 */
export function createFuelToast(root = document, opts = {}) {
  const toast = createToast(root, { regionId: 'fuel-toast-region', ...opts });

  /**
   * Show (or replace) the toast.
   * @param {boolean} on  true = rule applied (Dining Out), false = reverted (Transport)
   * @param {{ count?: number|string, amount?: number|string }} [meta]
   */
  function show(on, { count = 0, amount = '0.00' } = {}) {
    const n = Number(count) || 0;
    const noun = n === 1 ? 'purchase' : 'purchases';

    // ON but nothing qualifies this month: the toggle is a standing preference,
    // not a per-run action. Confirm the rule is on and will apply to future small
    // fuel stops - never say "0 moved", which reads like an error.
    if (on && n === 0) {
      return toast.show({
        title: 'Fuel-stop rule on',
        modifier: '',
        buildBody: (textEl, doc) => {
          textEl.appendChild(
            doc.createTextNode(
              'No small fuel stops this month yet. New ones under $10 will count as Dining Out.',
            ),
          );
        },
      });
    }

    return toast.show({
      title: on ? 'Moved to Dining Out' : 'Kept under Transport',
      // OFF keeps the original teal `toast--off` styling; ON uses the base toast.
      modifier: on ? '' : 'toast--off',
      buildBody: (textEl, doc) => {
        const strong = doc.createElement('strong');
        strong.textContent = `${n} small servo ${noun}`;
        textEl.appendChild(strong);
        if (on) {
          const money = formatCurrency(Math.abs(Number(amount) || 0));
          textEl.appendChild(
            doc.createTextNode(
              ` (${money}) moved to Dining Out and saved to your ledger.`,
            ),
          );
        } else {
          textEl.appendChild(doc.createTextNode(' stay under Transport.'));
        }
      },
    });
  }

  return { show, destroy: toast.destroy };
}
