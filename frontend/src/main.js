/**
 * main.js — entry point / bootstrap.
 * Orchestration only: no transform logic (that lives in summary.js).
 * No secrets here. No transaction data.
 */

import { fetchSummary, fetchStatus, postReclassify } from './api.js';
import { createDashboard } from './dashboard.js';
import { createQueue } from './queue.js';
import { createUploadController } from './uploadController.js';
import { postUpload } from './upload.js';
import { initTheme } from './theme.js';
import { initViews } from './views.js';
import { createCategoryContext } from './categoryContextController.js';

// ---------------------------------------------------------------------------
// Service worker (FR-3 — installable PWA), PRODUCTION ONLY.
// In dev the SW would serve cached .js/.css and hide code changes, so we never
// register it during development and actively remove any worker + caches a prior
// session left behind. Vite statically replaces import.meta.env.PROD, so only one
// branch survives in each build.
// ---------------------------------------------------------------------------
if (import.meta.env.PROD) {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        // SW registration failures are non-fatal (e.g. non-HTTPS dev, privacy mode).
      });
    });
  }
} else {
  // Dev: guarantee no stale-caching worker is active, and drop its caches.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker
      .getRegistrations()
      .then((regs) => regs.forEach((r) => r.unregister()))
      .catch(() => {});
  }
  if (self.caches) {
    caches
      .keys()
      .then((keys) => keys.forEach((k) => caches.delete(k)))
      .catch(() => {});
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const dash = createDashboard(document);
  const statusDot = document.getElementById('status-dot');
  const refreshBtn = document.getElementById('refresh');
  const fuelToggle = document.getElementById('fuel-rule-toggle');

  // Apply the stored theme immediately (avoids a flash of the wrong theme);
  // re-sync the donut border whenever the theme flips.
  initTheme({ root: document, onChange: () => dash.applyChartTheme() });

  // Render a summary object, choosing the empty state when there is no data.
  function renderSummary(summary, opts = {}) {
    const isEmpty =
      summary.count === 0 ||
      !summary.totals ||
      Object.keys(summary.totals).length === 0;

    if (isEmpty) {
      dash.showEmpty();
    } else {
      dash.render(summary, opts);
    }
  }

  // -------------------------------------------------------------------------
  // Dashboard load — reused as the post-upload refresh callback (onUploaded).
  // -------------------------------------------------------------------------
  async function load() {
    try {
      renderSummary(await fetchSummary(), { pulse: false });
    } catch (err) {
      dash.showError(err);
    }

    // Best-effort status dot — never blocks or errors the page.
    fetchStatus()
      .then((status) => {
        if (!statusDot) return;
        if (status && status.status === 'ok') {
          statusDot.style.backgroundColor = '#4CAF50';
          statusDot.title = 'Backend online';
        } else {
          statusDot.style.backgroundColor = '#EF5350';
          statusDot.title = 'Backend offline or unreachable';
        }
      })
      .catch(() => {
        // fetchStatus already returns null on failure; belt-and-suspenders.
      });
  }

  // -------------------------------------------------------------------------
  // Upload queue (FR-4) — IndexedDB-backed with memory fallback.
  // -------------------------------------------------------------------------
  const queue = createQueue(); // default: createIdbStore() with memory fallback
  createUploadController({ root: document, queue, onUploaded: load });
  queue.start();

  // Drain anything queued from a previous offline session.
  queue.flush({ postFn: (form) => postUpload(form) }).catch(() => {});

  // -------------------------------------------------------------------------
  // Manual refresh (FR-34 v1: no push — manual refresh button satisfies spec).
  // -------------------------------------------------------------------------
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => load());
  }

  // -------------------------------------------------------------------------
  // Small-fuel-stop rule toggle — apply/revert on the backend, then re-render.
  // On failure, resync the checkbox to the backend's actual state via load().
  // -------------------------------------------------------------------------
  if (fuelToggle) {
    fuelToggle.addEventListener('change', async () => {
      try {
        renderSummary(await postReclassify(fuelToggle.checked), { pulse: true });
      } catch (err) {
        dash.showError(err);
        load();
      }
    });
  }

  // -------------------------------------------------------------------------
  // View switching — Overview / Upload / Category context / (History, Settings
  // stay inert). Lazy-create the category-context controller on first switch;
  // re-run the dashboard load() whenever Overview is shown. initViews() shows
  // the default (Overview) view synchronously, which fires onShow('overview')
  // and triggers the initial load() below — no separate call needed.
  // -------------------------------------------------------------------------
  let categoryContext = null;

  initViews({
    root: document,
    onShow(view) {
      if (view === 'overview') {
        load();
      } else if (view === 'context') {
        if (!categoryContext) {
          categoryContext = createCategoryContext({ root: document });
        }
        categoryContext.load();
      }
    },
  });
});
