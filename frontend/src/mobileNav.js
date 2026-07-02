/**
 * mobileNav.js — hamburger drawer toggle for the phone view.
 *
 * On small screens the sidebar nav is an off-canvas drawer (see styles.css); this
 * wires the hamburger button to open it (with a dimmed backdrop) and closes it on
 * backdrop tap, Esc, or when a nav item is chosen. DOM wiring only — no network,
 * no data. On desktop the nav is always visible and the toggle is CSS-hidden, so
 * this controller sits inert there.
 */
export function createMobileNav({ root = document } = {}) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;
  const toggle = root.getElementById('nav-toggle');
  const nav = root.getElementById('sidebar-nav');
  const backdrop = root.getElementById('nav-backdrop');

  const listeners = [];
  function _on(el, ev, fn) {
    if (!el) return;
    el.addEventListener(ev, fn);
    listeners.push({ el, ev, fn });
  }

  const _open = () => !!(nav && nav.classList.contains('is-open'));

  function setOpen(open) {
    if (nav) nav.classList.toggle('is-open', open);
    if (backdrop) backdrop.classList.toggle('is-open', open);
    if (toggle) toggle.setAttribute('aria-expanded', String(open));
  }

  _on(toggle, 'click', (e) => {
    e.preventDefault();
    setOpen(!_open());
  });
  _on(backdrop, 'click', () => setOpen(false));
  // Close once a destination is chosen.
  _on(nav, 'click', (e) => {
    if (e.target.closest && e.target.closest('a.nav-item')) setOpen(false);
  });
  // Esc closes.
  _on(doc, 'keydown', (e) => {
    if (e.key === 'Escape') setOpen(false);
  });

  function destroy() {
    for (const { el, ev, fn } of listeners) el.removeEventListener(ev, fn);
    listeners.length = 0;
  }

  return {
    open: () => setOpen(true),
    close: () => setOpen(false),
    destroy,
    get isOpen() {
      return _open();
    },
  };
}
