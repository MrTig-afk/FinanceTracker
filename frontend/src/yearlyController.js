/**
 * yearlyController.js — DOM wiring for the Yearly view (GET /year).
 * PRIVACY: renders only aggregated totals/comparison the backend already
 * aggregated locally — no raw transaction data ever reaches this module.
 * Mirrors the categoryContextController.js idiom (injectable fetchFn,
 * _on/_listeners/destroy() teardown). Chart rendering follows dashboard.js.
 * Identical logic to monthlyController.js, keyed by 'y' instead of 'ym'.
 */

import { Chart, DoughnutController, ArcElement, Legend } from 'chart.js';
Chart.register(DoughnutController, ArcElement, Legend);

import { fetchYear } from './api.js';
import { toChartData, categoryRows, spendTotal, computeNet, formatCurrency } from './summary.js';

/**
 * Wire the Yearly view.
 *
 * Requires the following elements to be present in `root`:
 *   #yearly-select         (period <select>)
 *   #yearly-message        (empty/error banner)
 *   #yearly-canvas         (donut canvas)
 *   #yearly-spent          (SPENT overlay total)
 *   #yearly-net            (net value)
 *   #yearly-totals         (totals <tbody>)
 *   #yearly-compare        (comparison <tbody>)
 *   #yearly-compare-label  (comparison heading target year)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: (y?: string) => Promise<object>,
 * }} options
 * @returns {{ load(): Promise<void>, destroy(): void }}
 */
export function createYearly({ root = document, fetchFn } = {}) {
  const _fetchFn = fetchFn ?? fetchYear;
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const selectEl = root.getElementById('yearly-select');
  const messageEl = root.getElementById('yearly-message');
  const canvas = root.getElementById('yearly-canvas');
  const spentEl = root.getElementById('yearly-spent');
  const netEl = root.getElementById('yearly-net');
  const totalsEl = root.getElementById('yearly-totals');
  const compareEl = root.getElementById('yearly-compare');
  const compareLabelEl = root.getElementById('yearly-compare-label');

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
    for (const y of available) {
      const opt = doc.createElement('option');
      opt.value = y;
      opt.textContent = y;
      if (y === selected) opt.selected = true;
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
      _populateSelect(response.available_years ?? [], response.y ?? null);
      _showMessage('No data yet. Upload a statement to get started.');
      return;
    }

    _populateSelect(response.available_years ?? [], response.y);

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
      compareLabelEl.textContent = response.prev_y ? response.prev_y : 'no prior year';
    }
  }

  async function _fetchAndRender(arg) {
    try {
      const response = await _fetchFn(arg);
      _render(response);
    } catch {
      // Never expose raw error/stack — fixed safe message only.
      _clearAll();
      _showMessage('Could not load year.');
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
