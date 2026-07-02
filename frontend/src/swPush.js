/**
 * swPush.js — pure, SW-globals-free push-notification helpers.
 * Unit-tested by swPush.test.js (no service-worker environment needed).
 * public/sw.js mirrors the same constants/handlers inline (kept in sync manually,
 * same convention as swRouting.js / routeRequest()).
 *
 * PRIVACY: the backend notifier guarantees every push payload's `title`/`body`
 * is COUNTS/STATUS-ONLY copy (e.g. "N transactions processed") — never amounts,
 * balances, descriptions, categories, or account identifiers. Those safe strings
 * are the only server-sent text ever surfaced (as an in-app toast when the app is
 * focused, or an OS notification when it is not). If a payload field is missing
 * or malformed, we fall back to the FIXED generic strings below.
 */

export const PUSH_TITLE = 'FinanceTracker';
export const PUSH_BODY = 'Your statement was processed';

/**
 * Marker on messages the service worker posts to focused clients, so the client
 * bridge can distinguish our push relays from any other postMessage traffic.
 */
export const PUSH_MESSAGE_SOURCE = 'financetracker-push';

/**
 * Notification options for the default 'processed' notification. Pure — no SW
 * globals. Retained for back-compat; payload-driven callers use notificationArgs().
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

/**
 * Normalise a raw push payload ({type,title,body}, possibly partial/undefined)
 * into a complete, safe shape, falling back to the fixed generic strings.
 * @param {unknown} raw
 * @returns {{ type: string, title: string, body: string }}
 */
export function normalizePushPayload(raw) {
  const obj = raw && typeof raw === 'object' ? raw : {};
  const type = typeof obj.type === 'string' && obj.type ? obj.type : 'generic';
  const title =
    typeof obj.title === 'string' && obj.title.trim() ? obj.title : PUSH_TITLE;
  const body = typeof obj.body === 'string' && obj.body ? obj.body : PUSH_BODY;
  return { type, title, body };
}

/**
 * Is this window client currently focused / visible? Tolerant of stub shapes:
 * a real WindowClient exposes `focused` and `visibilityState`.
 * @param {{focused?: boolean, visibilityState?: string}} client
 * @returns {boolean}
 */
export function isClientFocused(client) {
  if (!client) return false;
  return client.focused === true || client.visibilityState === 'visible';
}

/**
 * First focused/visible window client in the list, or null.
 * @param {Array<{focused?: boolean, visibilityState?: string}>} clientList
 * @returns {object|null}
 */
export function findFocusedClient(clientList) {
  for (const client of clientList || []) {
    if (isClientFocused(client)) return client;
  }
  return null;
}

/**
 * Build the (title, options) pair for self.registration.showNotification().
 * The payload is carried in `data` so notificationclick can act on it.
 * @param {unknown} raw
 * @returns {{ title: string, options: { body: string, icon: string, badge: string, tag: string, data: object } }}
 */
export function notificationArgs(raw) {
  const p = normalizePushPayload(raw);
  return {
    title: p.title,
    options: {
      body: p.body,
      icon: '/icon.svg',
      badge: '/icon.svg',
      tag: `financetracker-${p.type}`,
      data: { type: p.type, title: p.title, body: p.body },
    },
  };
}

/**
 * PURE routing decision for a push event. Given the current window clients and
 * the raw payload, decide whether to relay an in-app toast (a client is focused)
 * or raise an OS notification (none focused).
 *
 * @param {Array<{focused?: boolean, visibilityState?: string}>} clientList
 * @param {unknown} raw  the parsed {type,title,body} payload
 * @returns {
 *   | { action: 'message', message: { source: string, type: string, title: string, body: string } }
 *   | { action: 'notify', title: string, options: object }
 * }
 */
export function routePush(clientList, raw) {
  const p = normalizePushPayload(raw);
  if (findFocusedClient(clientList)) {
    return {
      action: 'message',
      message: {
        source: PUSH_MESSAGE_SOURCE,
        type: p.type,
        title: p.title,
        body: p.body,
      },
    };
  }
  const { title, options } = notificationArgs(p);
  return { action: 'notify', title, options };
}
