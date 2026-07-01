/**
 * overviewTrendController.js — DOM wiring for the Overview "Spending over the
 * last N months" mini bar chart (fed by GET /trends spend_by_month).
 * PRIVACY: renders only aggregated per-month spend magnitudes the backend
 * already aggregated locally — no raw transaction data reaches this module.
 * Mirrors the monthlyController.js idiom (injectable fetchFn, teardown).
 * load() is BEST-EFFORT: it never throws, so a Trends fetch failure never
 * breaks the Overview donut.
 */

import { Chart, BarController, BarElement, CategoryScale, LinearScale, Tooltip } from 'chart.js';
Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip);

import { fetchTrends } from './api.js';
import { monthLabel, parseAmount } from './summary.js';

// Single fixed bar colour (the bars are one series) — falls back to the
// design accent if the --accent token cannot be read from the document.
const _FALLBACK_BAR_COLOR = '#3d9a6f';

/**
 * Wire the Overview mini spend-over-time bar chart.
 *
 * Requires the following elements to be present in `root`:
 *   #overview-trend-canvas   (bar-chart canvas)
 *   #overview-trend-message  (best-effort message banner)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: (months?: number, end?: string) => Promise<object>,
 * }} options
 * @returns {{ load(): Promise<void>, destroy(): void }}
 */
export function createOverviewTrend({ root = document, fetchFn } = {}) {
  const _fetchFn = fetchFn ?? fetchTrends;

  const canvas = root.getElementById('overview-trend-canvas');
  const messageEl = root.getElementById('overview-trend-message');

  /** @type {Chart|null} */
  let chartInstance = null;

  function _destroyChart() {
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
  }

  function _hideMessage() {
    if (messageEl) messageEl.hidden = true;
  }

  function _showMessage(text) {
    if (messageEl) {
      messageEl.textContent = text;
      messageEl.hidden = false;
    }
  }

  function _barColor() {
    const ownerDoc = canvas?.ownerDocument ?? document;
    if (typeof getComputedStyle !== 'function' || !ownerDoc.documentElement) {
      return _FALLBACK_BAR_COLOR;
    }
    const value = getComputedStyle(ownerDoc.documentElement).getPropertyValue('--accent').trim();
    return value || _FALLBACK_BAR_COLOR;
  }

  function _render(response) {
    _hideMessage();
    const months = response.months ?? [];

    if (months.length === 0) {
      _destroyChart();
      _showMessage('No spending history yet.');
      return;
    }

    _destroyChart();
    if (!canvas) return;

    chartInstance = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: months.map(monthLabel),
        datasets: [
          {
            data: (response.spend_by_month ?? []).map(parseAmount),
            backgroundColor: _barColor(),
          },
        ],
      },
      options: {
        responsive: false,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  async function load() {
    try {
      const response = await _fetchFn();
      _render(response);
    } catch {
      // Best-effort: never throw out of load() — the Overview donut must
      // render regardless of a Trends fetch failure. Fixed safe message only.
      _destroyChart();
      _showMessage('No spending history yet.');
    }
  }

  function destroy() {
    _destroyChart();
  }

  return { load, destroy };
}
