/**
 * dashboard.js — DOM rendering layer.
 * Imports Chart.js and summary.js; touches the DOM only inside createDashboard().
 * No IO, no network at module level.
 */

import { Chart, DoughnutController, ArcElement, Tooltip, Legend } from 'chart.js';
Chart.register(DoughnutController, ArcElement, Tooltip, Legend);

import {
  toChartData,
  categoryRows,
  computeNet,
  formatCurrency,
  monthLabel,
} from './summary.js';

/**
 * Create the dashboard controller bound to the given DOM root.
 * Pass `document` in production; pass a jsdom Document in tests.
 *
 * @param {Document} root
 * @returns {{ render(summary: object): void, showError(err: Error): void, showEmpty(): void, destroy(): void }}
 */
export function createDashboard(root = document) {
  const monthLabelEl = root.getElementById('month-label');
  const netValueEl = root.getElementById('net-value');
  const canvas = root.getElementById('chart');
  const tbody = root.getElementById('totals-body');
  const messageEl = root.getElementById('message');

  /** @type {Chart|null} */
  let chartInstance = null;

  function _destroyChart() {
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
  }

  function _clearTable() {
    if (tbody) tbody.textContent = '';
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

  /**
   * Render summary data to the dashboard.
   * - Sets month label and net value.
   * - Populates the totals table (all categories, sorted by abs amount DESC).
   * - Creates the donut chart if spending data exists; shows an empty-state
   *   message if all categories are excluded (no net-negative non-Income rows).
   * @param {object} summary
   */
  function render(summary) {
    // Month label
    if (monthLabelEl) {
      monthLabelEl.textContent = monthLabel(summary.year_month);
    }

    // Net value — colour-coded by sign
    const net = computeNet(summary);
    if (netValueEl) {
      netValueEl.textContent = formatCurrency(net);
      netValueEl.classList.toggle('net-negative', net < 0);
      netValueEl.classList.toggle('net-positive', net >= 0);
    }

    // Totals table — always populate (includes Income, Uncategorised, all rows)
    _clearTable();
    if (tbody) {
      const rows = categoryRows(summary);
      rows.forEach((row) => {
        const tr = document.createElement('tr');

        const catTd = document.createElement('td');
        catTd.textContent = row.category;

        const amtTd = document.createElement('td');
        amtTd.className = 'amount-col';
        amtTd.textContent = row.formatted;

        tr.appendChild(catTd);
        tr.appendChild(amtTd);
        tbody.appendChild(tr);
      });
    }

    // Donut chart — only for net-negative non-Income categories
    const data = toChartData(summary);
    _destroyChart();

    if (data.labels.length === 0) {
      // Totals table remains visible; only the chart area shows an empty notice
      _showMessage('No spending to chart yet.');
    } else {
      _hideMessage();
      if (canvas) {
        chartInstance = new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: data.labels,
            datasets: [
              {
                data: data.values,
                backgroundColor: data.colors,
                borderWidth: 2,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
              legend: { position: 'bottom' },
              tooltip: {
                callbacks: {
                  label(context) {
                    return ` ${context.label}: ${formatCurrency(context.raw)}`;
                  },
                },
              },
            },
          },
        });
      }
    }
  }

  /**
   * Show an empty-data state.
   * Called by main.js when summary.count === 0 or totals is empty.
   * Clears chart and table, shows the "no data" banner.
   */
  function showEmpty() {
    _destroyChart();
    _clearTable();
    _showMessage('No data yet — upload a statement to get started.');
  }

  /**
   * Show a generic error message.
   * Never exposes raw response bodies, stack traces, or transaction data.
   * @param {import('./api.js').ApiError} err
   */
  function showError(err) {
    _destroyChart();
    _clearTable();
    const statusPart = err && err.status ? ` (status ${err.status})` : '';
    _showMessage(`Could not load summary.${statusPart}`);
  }

  /**
   * Tear down the Chart instance. Call before removing the dashboard from the DOM.
   */
  function destroy() {
    _destroyChart();
  }

  return { render, showEmpty, showError, destroy };
}
