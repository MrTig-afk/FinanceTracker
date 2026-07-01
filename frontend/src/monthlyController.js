/**
 * monthlyController.js — DOM wiring for the Monthly view (GET /month).
 * PRIVACY: renders only aggregated totals/comparison the backend already
 * aggregated locally — no raw transaction data ever reaches this module.
 * Mirrors the categoryContextController.js idiom (injectable fetchFn,
 * _on/_listeners/destroy() teardown). Chart rendering follows dashboard.js.
 */

import { Chart, DoughnutController, ArcElement, Legend } from 'chart.js';
Chart.register(DoughnutController, ArcElement, Legend);

import { fetchMonth } from './api.js';
import {
  toChartData,
  categoryRows,
  spendTotal,
  computeNet,
  formatCurrency,
  monthLabel,
} from './summary.js';

/**
 * Wire the Monthly view.
 *
 * Requires the following elements to be present in `root`:
 *   #monthly-select         (period <select>)
 *   #monthly-message        (empty/error banner)
 *   #monthly-canvas         (donut canvas)
 *   #monthly-spent          (SPENT overlay total)
 *   #monthly-net            (net value)
 *   #monthly-totals         (totals <tbody>)
 *   #monthly-compare        (comparison <tbody>)
 *   #monthly-compare-label  (comparison heading target month)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: (ym?: string) => Promise<object>,
 * }} options
 * @returns {{ load(): Promise<void>, destroy(): void }}
 */
export function createMonthly({ root = document, fetchFn } = {}) {
  const _fetchFn = fetchFn ?? fetchMonth;
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const selectEl = root.getElementById('monthly-select');
  const messageEl = root.getElementById('monthly-message');
  const canvas = root.getElementById('monthly-canvas');
  const spentEl = root.getElementById('monthly-spent');
  const netEl = root.getElementById('monthly-net');
  const totalsEl = root.getElementById('monthly-totals');
  const compareEl = root.getElementById('monthly-compare');
  const compareLabelEl = root.getElementById('monthly-compare-label');

  /** @type {Chart|null} */
  let chartInstance = null;

  const _listeners = [];
  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

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

  function _clearAll() {
    _destroyChart();
    if (spentEl) spentEl.textContent = formatCurrency(0);
    if (netEl) {
      netEl.textContent = '';
      netEl.classList.remove('net-negative', 'net-positive');
    }
    if (totalsEl) totalsEl.textContent = '';
    if (compareEl) compareEl.textContent = '';
    if (compareLabelEl) compareLabelEl.textContent = '';
    if (selectEl) selectEl.textContent = '';
  }

  function _populateSelect(available, selected) {
    if (!selectEl) return;
    selectEl.textContent = '';
    for (const ym of available) {
      const opt = doc.createElement('option');
      opt.value = ym;
      opt.textContent = monthLabel(ym);
      if (ym === selected) opt.selected = true;
      selectEl.appendChild(opt);
    }
  }

  function _renderChart(data) {
    _destroyChart();
    if (!canvas) return;
    chartInstance = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: data.labels,
        datasets: [{ data: data.values, backgroundColor: data.colors }],
      },
      options: {
        cutout: '70%',
        responsive: false,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
  }

  function _renderTotals(response) {
    if (!totalsEl) return;
    totalsEl.textContent = '';
    for (const { category, formatted } of categoryRows(response)) {
      const tr = doc.createElement('tr');

      const nameTd = doc.createElement('td');
      nameTd.textContent = category;

      const amountTd = doc.createElement('td');
      amountTd.textContent = formatted;

      tr.appendChild(nameTd);
      tr.appendChild(amountTd);
      totalsEl.appendChild(tr);
    }
  }

  /** Class from pct_change sign: >0 delta-up, <0 delta-down, null delta-new. */
  function _deltaClass(pctChange) {
    if (pctChange === null || pctChange === undefined) return 'delta-new';
    if (pctChange > 0) return 'delta-up';
    if (pctChange < 0) return 'delta-down';
    return 'delta-new';
  }

  function _renderComparison(response) {
    if (!compareEl) return;
    compareEl.textContent = '';
    for (const row of response.comparison ?? []) {
      const tr = doc.createElement('tr');

      const nameTd = doc.createElement('td');
      nameTd.textContent = row.category;

      const currentTd = doc.createElement('td');
      currentTd.textContent = formatCurrency(row.current);

      const cls = _deltaClass(row.pct_change);

      const deltaTd = doc.createElement('td');
      deltaTd.classList.add(cls);
      deltaTd.textContent = formatCurrency(row.delta);

      const pctTd = doc.createElement('td');
      pctTd.classList.add(cls);
      pctTd.textContent =
        row.pct_change === null || row.pct_change === undefined ? 'n/a' : `${row.pct_change}%`;

      tr.appendChild(nameTd);
      tr.appendChild(currentTd);
      tr.appendChild(deltaTd);
      tr.appendChild(pctTd);
      compareEl.appendChild(tr);
    }
  }

  function _render(response) {
    _hideMessage();

    const totals = response.totals ?? {};
    const isEmpty = response.count === 0 || Object.keys(totals).length === 0;

    if (isEmpty) {
      _clearAll();
      _populateSelect(response.available_months ?? [], response.ym ?? null);
      _showMessage('No data yet. Upload a statement to get started.');
      return;
    }

    _populateSelect(response.available_months ?? [], response.ym);

    const data = toChartData(response);
    _renderChart(data);

    if (spentEl) spentEl.textContent = formatCurrency(spendTotal(response));

    const net = computeNet(response);
    if (netEl) {
      netEl.textContent = formatCurrency(net);
      netEl.classList.toggle('net-negative', net < 0);
      netEl.classList.toggle('net-positive', net >= 0);
    }

    _renderTotals(response);
    _renderComparison(response);

    if (compareLabelEl) {
      compareLabelEl.textContent = response.prev_ym ? monthLabel(response.prev_ym) : 'no prior month';
    }
  }

  async function _fetchAndRender(arg) {
    try {
      const response = await _fetchFn(arg);
      _render(response);
    } catch {
      // Never expose raw error/stack — fixed safe message only.
      _clearAll();
      _showMessage('Could not load month.');
    }
  }

  async function load() {
    await _fetchAndRender();
  }

  _on(selectEl, 'change', () => {
    _fetchAndRender(selectEl.value);
  });

  function destroy() {
    _destroyChart();
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { load, destroy };
}
