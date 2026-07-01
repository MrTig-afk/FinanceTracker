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

// ---------------------------------------------------------------------------
// Service worker registration (FR-3 — installable PWA)
// Guarded so it never runs outside a browser / secure context.
// public/sw.js is served at /sw.js by Vite (public/ root) without bundling.
// ---------------------------------------------------------------------------
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // SW registration failures are non-fatal (e.g. non-HTTPS dev, privacy mode).
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const dash = createDashboard(document);
  const statusDot = document.getElementById('status-dot');
  const refreshBtn = document.getElementById('refresh');
  const fuelToggle = document.getElementById('fuel-rule-toggle');

  // Render a summary object, choosing the empty state when there is no data.
  function renderSummary(summary) {
    const isEmpty =
      summary.count === 0 ||
      !summary.totals ||
      Object.keys(summary.totals).length === 0;

    if (isEmpty) {
      dash.showEmpty();
    } else {
      dash.render(summary);
    }
  }

  // -------------------------------------------------------------------------
  // Dashboard load — reused as the post-upload refresh callback (onUploaded).
  // -------------------------------------------------------------------------
  async function load() {
    try {
      renderSummary(await fetchSummary());
    } catch (err) {
      dash.showError(err);
    }

    // Best-effort status dot — never blocks or errors the page.
    fetchStatus()
      .then((status) => {
        if (!statusDot) return;
        if (status && status.ok) {
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
        renderSummary(await postReclassify(fuelToggle.checked));
      } catch (err) {
        dash.showError(err);
        load();
      }
    });
  }

  load();
});
