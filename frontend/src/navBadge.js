/**
 * navBadge.js — DOM wiring for the Transfers unseen-count nav badge.
 *
 * A tiny unread-mail-style count pill on the Transfers nav item. DOM wiring only:
 * no network, no transaction data — the count is a bare integer set by the caller.
 * Text is written via textContent only, so a value can never inject HTML. One badge
 * element serves both navs (#sidebar-nav is also the mobile off-canvas drawer).
 */

/**
 * Wire the nav badge element.
 *
 * @param {{ root?: Document, id?: string }} options
 * @returns {{ set(count: unknown): void, clear(): void, destroy(): void }}
 */
export function createNavBadge({ root = document, id = 'nav-badge-transfers' } = {}) {
  const el = root.getElementById(id);

  function clear() {
    if (!el) return;
    el.hidden = true;
    el.textContent = '';
  }

  function set(count) {
    if (!el) return;
    // Fail closed to hidden: only a finite positive number shows the pill.
    if (typeof count !== 'number' || !Number.isFinite(count) || count <= 0) {
      clear();
      return;
    }
    el.hidden = false;
    el.textContent = count > 99 ? '99+' : String(count);
  }

  // No listeners to remove; provided for idiom parity with the other controllers.
  function destroy() {}

  return { set, clear, destroy };
}
