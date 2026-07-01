/**
 * trendsController.js — DOM wiring for the Trends view (GET /trends).
 * PRIVACY: renders only aggregated per-category monthly totals the backend
 * already aggregated locally — no raw transaction data ever reaches this
 * module. Mirrors the monthlyController.js idiom (injectable fetchFn,
 * _on/_listeners/destroy() teardown).
 */

import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Legend,
  Tooltip,
} from 'chart.js';
Chart.register(LineController, LineElement, PointElement, CategoryScale, LinearScale, Legend, Tooltip);

import { fetchTrends } from './api.js';
import { colorFor, monthLabel, parseAmount } from './summary.js';

const _WINDOW_OPTIONS = [3, 6, 12, 24];
const _NOT_ENOUGH_HISTORY = 'Not enough history yet. Upload at least two months to see trends.';

/**
 * Wire the Trends view.
 *
 * Requires the following elements to be present in `root`:
 *   #trends-window   (window <select>)
 *   #trends-message  (empty/error banner)
 *   #trends-canvas   (line-chart canvas)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: (months?: number, end?: string) => Promise<object>,
 * }} options
 * @returns {{ load(): Promise<void>, destroy(): void }}
 */
export function createTrends({ root = document, fetchFn } = {}) {
  const _fetchFn = fetchFn ?? fetchTrends;
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const selectEl = root.getElementById('trends-window');
  const messageEl = root.getElementById('trends-message');
  const canvas = root.getElementById('trends-canvas');

  /** @type {Chart|null} */
  let chartInstance = null;
  let _selectPopulated = false;

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

  function _populateWindowSelect(selected) {
    if (!selectEl || _selectPopulated) return;
    selectEl.textContent = '';
    for (const n of _WINDOW_OPTIONS) {
      const opt = doc.createElement('option');
      opt.value = String(n);
      opt.textContent = `${n} months`;
      if (n === selected) opt.selected = true;
      selectEl.appendChild(opt);
    }
    _selectPopulated = true;
  }

  function _buildDatasets(response) {
    return (response.series ?? [])
      .filter((s) => s.category !== 'Income')
      .map((s) => ({
        label: s.category,
        data: (s.values ?? []).map((v) => Math.abs(parseAmount(v))),
        borderColor: colorFor(s.category),
        backgroundColor: colorFor(s.category),
        tension: 0.3,
        pointRadius: 2,
      }));
  }

  function _allZero(datasets) {
    return datasets.every((d) => d.data.every((v) => v === 0));
  }

  function _renderChart(response, datasets) {
    _destroyChart();
    if (!canvas) return;

    chartInstance = new Chart(canvas, {
      type: 'line',
      data: {
        labels: response.months.map(monthLabel),
        datasets,
      },
      options: {
        responsive: false,
        maintainAspectRatio: false,
        plugins: { legend: { display: true } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  function _render(response) {
    _hideMessage();
    _populateWindowSelect(response.window);

    const monthsAvailable = response.months_available ?? 0;
    const months = response.months ?? [];

    if (monthsAvailable <= 1 || months.length === 0) {
      _destroyChart();
      _showMessage(_NOT_ENOUGH_HISTORY);
      return;
    }

    const datasets = _buildDatasets(response);

    if (_allZero(datasets)) {
      _destroyChart();
      _showMessage(_NOT_ENOUGH_HISTORY);
      return;
    }

    _renderChart(response, datasets);
  }

  async function _fetchAndRender(months) {
    try {
      const response = await _fetchFn(months);
      _render(response);
    } catch {
      // Never expose raw error/stack — fixed safe message only.
      _destroyChart();
      _showMessage('Could not load trends.');
    }
  }

  async function load() {
    await _fetchAndRender();
  }

  _on(selectEl, 'change', () => {
    _fetchAndRender(Number(selectEl.value));
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
