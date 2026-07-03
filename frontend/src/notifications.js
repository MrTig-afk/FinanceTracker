/**
 * notifications.js — client bridge: service-worker push relays -> in-app toast.
 *
 * When the app is FOCUSED, the service worker (public/sw.js) postMessages the
 * push payload to the page instead of raising an OS notification; this module
 * listens for those messages and shows a reusable in-app toast. When the app is
 * backgrounded the SW shows an OS notification instead, so this never fires.
 *
 * PRIVACY: payload title/body are counts/status-only copy (guaranteed by the
 * backend notifier). No IO, no network here — DOM/toast only.
 */

import { PUSH_MESSAGE_SOURCE } from './swPush.js';

/**
 * Map a push `type` to a toast visual kind (drives accent/dot colour in CSS).
 * @param {string} type
 * @returns {'success'|'error'|'info'}
 */
export function kindForType(type) {
  switch (type) {
    case 'parse_error':
    case 'categorisation_failed':
    case 'drive_backup_failed':
    case 'generic_error':
    case 'budget_exceeded':
      return 'error';
    case 'processed':
    case 'processed_recovered':
    case 'categorisation_recovered':
      return 'success';
    case 'duplicate_noop':
    case 'monthly_reminder':
    case 'budget_approaching':
    default:
      return 'info';
  }
}

/**
 * Wire a `navigator.serviceWorker` "message" listener that renders each relayed
 * push payload as an in-app toast. Degrades to a no-op controller when the
 * service worker or a toast is unavailable — never throws.
 *
 * @param {{
 *   toast?: { show(spec: object): unknown },
 *   nav?: { serviceWorker?: { addEventListener?: Function, removeEventListener?: Function } },
 * }} [options]
 * @returns {{ destroy(): void }}
 */
export function createNotificationBridge({
  toast,
  nav = typeof navigator !== 'undefined' ? navigator : undefined,
} = {}) {
  const sw = nav && nav.serviceWorker;
  if (
    !toast ||
    !sw ||
    typeof sw.addEventListener !== 'function' ||
    typeof toast.show !== 'function'
  ) {
    return { destroy: () => {} };
  }

  function handleMessage(event) {
    const data = event && event.data;
    if (!data || typeof data !== 'object') return;
    if (data.source !== PUSH_MESSAGE_SOURCE) return;

    toast.show({
      title:
        typeof data.title === 'string' && data.title
          ? data.title
          : 'FinanceTracker',
      body: typeof data.body === 'string' ? data.body : '',
      kind: kindForType(data.type),
    });
  }

  sw.addEventListener('message', handleMessage);

  return {
    destroy() {
      if (typeof sw.removeEventListener === 'function') {
        sw.removeEventListener('message', handleMessage);
      }
    },
  };
}
