/**
 * views.js — lightweight view switcher (no SPA router library).
 * Shows/hides <section class="view" data-view="..."> elements and toggles the
 * matching sidebar nav item's active state. No network, no secrets, no
 * transaction data — DOM wiring only.
 */

/** Header title/subtitle text per view. */
const VIEW_COPY = {
  overview: {
    title: 'Overview',
    subtitle: '',
  },
  upload: {
    title: 'Upload',
    subtitle: 'Add CommBank and Westpac CSV exports.',
  },
  context: {
    title: 'Category context',
    subtitle:
      'Add merchant examples and notes per category. This context is prepended to every ' +
      'categorisation request so the model sorts your transactions the way you would.',
  },
  monthly: {
    title: 'Monthly',
    subtitle: 'One month’s breakdown, and how each category moved vs the previous month.',
  },
  yearly: {
    title: 'Yearly',
    subtitle: 'One year’s breakdown, and how each category moved vs the previous year.',
  },
};

const DEFAULT_VIEW = 'overview';

/**
 * Wire up view switching.
 *
 * Requires the following elements to be present in `root`:
 *   nav items: [data-view] on each clickable nav <a>
 *   sections:  <section class="view" data-view="...">
 *   header:    <h1> and #month-label (subtitle) inside .site-header
 *
 * @param {{
 *   root?: Document,
 *   onShow?: (view: string) => void,  // called after a view becomes visible
 * }} options
 * @returns {{ show(view: string): void, destroy(): void }}
 */
export function initViews({ root = document, onShow } = {}) {
  const navLinks = Array.from(root.querySelectorAll('a.nav-item[data-view]'));
  const sections = Array.from(root.querySelectorAll('section.view[data-view]'));
  const headingEl = root.querySelector('.site-header h1');
  const subtitleEl = root.getElementById ? root.getElementById('month-label') : null;

  const _listeners = [];

  function show(view) {
    if (!VIEW_COPY[view]) return;

    for (const section of sections) {
      section.hidden = section.dataset.view !== view;
    }

    for (const link of navLinks) {
      link.classList.toggle('nav-item--active', link.dataset.view === view);
    }

    const copy = VIEW_COPY[view];
    if (headingEl && copy) headingEl.textContent = copy.title;
    // The Overview view keeps its own dynamic month subtitle (set elsewhere);
    // other views show their static description.
    if (subtitleEl && view !== 'overview') subtitleEl.textContent = copy.subtitle;

    if (onShow) onShow(view);
  }

  for (const link of navLinks) {
    const handler = (e) => {
      e.preventDefault();
      show(link.dataset.view);
    };
    link.addEventListener('click', handler);
    _listeners.push({ el: link, event: 'click', handler });
  }

  show(DEFAULT_VIEW);

  function destroy() {
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { show, destroy };
}
