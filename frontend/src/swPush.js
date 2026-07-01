/**
 * swPush.js — pure, SW-globals-free push-notification constants/helpers.
 * Unit-tested by swPush.test.js (no service-worker environment needed).
 * public/sw.js mirrors the same constants/handlers inline (kept in sync manually,
 * same convention as swRouting.js / routeRequest()).
 *
 * PRIVACY: the push handler in public/sw.js deliberately ignores any server-sent
 * event.data and always shows these FIXED generic strings, so no financial data
 * (amounts, balances, descriptions, categories, accounts) can ever appear in a
 * notification, even if a future payload were mis-populated.
 */

export const PUSH_TITLE = 'FinanceTracker';
export const PUSH_BODY = 'Your statement was processed';

/**
 * Notification options for the 'processed' notification. Pure — no SW globals.
 * @returns {{ body: string, icon: string, badge: string, tag: string }}
 */
export function notificationOptions() {
  return {
    body: PUSH_BODY,
    icon: '/icon.svg',
    badge: '/icon.svg',
    tag: 'financetracker-processed',
  };
}

/**
 * Pick the first focusable client from a notificationclick clientList, or null.
 * @param {Array<{focus?: () => void}>} clientList
 * @returns {{focus: () => void}|null}
 */
export function pickClientToFocus(clientList) {
  for (const client of clientList) {
    if (client && 'focus' in client) return client;
  }
  return null;
}
