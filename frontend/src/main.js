/**
 * main.js — entry point / bootstrap.
 * Orchestration only: no transform logic (that lives in summary.js).
 * No secrets here. No transaction data.
 */

import { fetchSummary, fetchStatus } from './api.js';
import { createDashboard } from './dashboard.js';

document.addEventListener('DOMContentLoaded', () => {
  const dash = createDashboard(document);
  const statusDot = document.getElementById('status-dot');
  const refreshBtn = document.getElementById('refresh');

  async function load() {
    try {
      const summary = await fetchSummary();

      const isEmpty =
        summary.count === 0 ||
        !summary.totals ||
        Object.keys(summary.totals).length === 0;

      if (isEmpty) {
        dash.showEmpty();
      } else {
        dash.render(summary);
      }
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
        // fetchStatus already returns null on failure; this is belt-and-suspenders.
      });
  }

  // Manual refresh (FR-34 v1: no push — manual refresh button satisfies the spec).
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => load());
  }

  load();
});
