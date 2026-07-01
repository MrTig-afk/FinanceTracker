/**
 * push.js — Web Push subscribe controller (v2 Pass 3 — SCAFFOLD).
 * PRIVACY: never logs subscription contents. Degrades gracefully (no throw) when
 * Push/Notification are unsupported, permission is denied, or the VAPID public key
 * is not configured (placeholder default) — the button simply stays disabled with a
 * safe status message. No financial data is ever involved in this flow.
 *
 * Mirrors createUploadController's shape: listener registry + destroy().
 */

/** Sentinel matching the placeholder committed to frontend/.env.example. */
export const VAPID_PLACEHOLDER = 'REPLACE_WITH_VAPID_PUBLIC_KEY';

/**
 * Decode a URL-safe base64 VAPID public key into a Uint8Array
 * (standard applicationServerKey format for PushManager.subscribe).
 * @param {string} base64String
 * @returns {Uint8Array}
 */
export function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');

  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function _isConfigured(key) {
  return Boolean(key) && key !== VAPID_PLACEHOLDER;
}

function _isSupported() {
  return (
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

/**
 * Wire the "Enable notifications" control (#enable-push / #push-status) in the
 * Upload view.
 *
 * @param {{
 *   root?: Document,
 *   api?: { subscribe(sub: object): Promise<object>, unsubscribe(endpoint: string): Promise<object> },
 *   vapidPublicKey?: string,  // injectable; defaults to import.meta.env.VITE_VAPID_PUBLIC_KEY
 * }} options
 * @returns {{ destroy(): void }}
 */
export function createPushController({
  root = document,
  api,
  vapidPublicKey,
} = {}) {
  const key = vapidPublicKey ?? import.meta.env.VITE_VAPID_PUBLIC_KEY;

  const button = root.getElementById('enable-push');
  const statusEl = root.getElementById('push-status');

  const _listeners = [];

  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

  function _setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
  }

  function _disable() {
    if (button) button.disabled = true;
  }

  // --- Support / config checks (never throw) --------------------------------

  if (!_isSupported()) {
    _disable();
    _setStatus('Notifications are not supported on this device.');
    return { destroy: () => {} };
  }

  if (!_isConfigured(key)) {
    _disable();
    _setStatus('Notifications are not configured yet.');
    return { destroy: () => {} };
  }

  // --- Click handler (all failure branches set status and return) -----------

  async function _handleClick() {
    try {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') {
        _setStatus('Notifications permission was not granted.');
        return;
      }

      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      });

      await api.subscribe(sub.toJSON());
      _setStatus('Notifications enabled.');
    } catch {
      // Never log subscription contents; never throw out of this handler.
      _setStatus('Could not enable notifications.');
    }
  }

  _on(button, 'click', _handleClick);

  function destroy() {
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { destroy };
}
