/**
 * toast.js — reusable top-right slide-in toast controller.
 *
 * Generalised out of the original fuel-rule toast so BOTH the fuel-rule
 * confirmation (fuelToast.js) and push-notification messages (notifications.js)
 * render through the same DOM/lifecycle code and share one visual language.
 *
 * A single toast lives per region at a time: each show() replaces any current
 * one, slides in from the top-right, runs a ~5s countdown bar, then fades out
 * and removes itself.
 *
 * No IO, no network. DOM-only, so it is testable against jsdom.
 */

const DEFAULT_TIMEOUT_MS = 5000; // matches the 5s CSS countdown bar
const DEFAULT_FADE_MS = 450; // matches the toast fade-out transition

/**
 * Create a toast controller bound to the given DOM root + region.
 * Lazily creates a fixed top-right region if one is not already present.
 *
 * @param {Document} [root]
 * @param {{ regionId?: string, timeoutMs?: number, fadeMs?: number }} [opts]
 * @returns {{
 *   show(spec: {
 *     title?: string,
 *     body?: string,
 *     kind?: string,          // shorthand -> `toast--${kind}` modifier class
 *     modifier?: string,      // raw modifier class (takes precedence over kind)
 *     buildBody?: (textEl: HTMLElement, doc: Document) => void, // rich body builder
 *   }): (HTMLElement|null),
 *   destroy(): void,
 * }}
 */
export function createToast(
  root = document,
  {
    regionId = 'toast-region',
    timeoutMs = DEFAULT_TIMEOUT_MS,
    fadeMs = DEFAULT_FADE_MS,
  } = {},
) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  let region = root.getElementById(regionId);
  if (!region) {
    region = doc.createElement('div');
    region.id = regionId;
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

  function show({ title = '', body = '', kind = '', modifier, buildBody } = {}) {
    // Single instance: drop any current toast and its pending timers first.
    _clearTimers();
    _removeCurrent();

    const mod =
      modifier !== undefined ? modifier : kind ? `toast--${kind}` : '';

    const el = doc.createElement('div');
    el.className = mod ? `toast ${mod}` : 'toast';

    const accent = doc.createElement('span');
    accent.className = 'toast-accent';

    const bodyEl = doc.createElement('div');
    bodyEl.className = 'toast-body';

    const titleEl = doc.createElement('div');
    titleEl.className = 'toast-title';
    const dot = doc.createElement('span');
    dot.className = 'dot';
    titleEl.appendChild(dot);
    titleEl.appendChild(doc.createTextNode(title));

    const textEl = doc.createElement('div');
    textEl.className = 'toast-text';
    if (typeof buildBody === 'function') {
      buildBody(textEl, doc);
    } else {
      textEl.textContent = body;
    }

    bodyEl.appendChild(titleEl);
    bodyEl.appendChild(textEl);

    const timer = doc.createElement('span');
    timer.className = 'toast-timer';

    el.appendChild(accent);
    el.appendChild(bodyEl);
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
