/**
 * monthlyController.js — DOM wiring for the Monthly view (GET /month).
 * PRIVACY: renders only aggregated totals/comparison the backend already
 * aggregated locally — no raw transaction data ever reaches this module.
 * Mirrors the categoryContextController.js idiom (injectable fetchFn,
 * _on/_listeners/destroy() teardown). The donut, legend and arc<->legend
 * cross-highlight follow the Overview donut idiom in dashboard.js.
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
 *   #monthly-kpi-spent      (Spent KPI value)
 *   #monthly-income         (Income KPI value)
 *   #monthly-net            (Net KPI value)
 *   #monthly-legend         (horizontal wrap legend)
 *   #monthly-totals         (totals <tbody>)
 *   #monthly-compare        (comparison <tbody>)
 *   #monthly-compare-foot   (comparison totals footer <tr>)
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
  const kpiSpentEl = root.getElementById('monthly-kpi-spent');
  const incomeEl = root.getElementById('monthly-income');
  const netEl = root.getElementById('monthly-net');
  const legendEl = root.getElementById('monthly-legend');
  const totalsEl = root.getElementById('monthly-totals');
  const compareEl = root.getElementById('monthly-compare');
  const compareFootEl = root.getElementById('monthly-compare-foot');
  const compareLabelEl = root.getElementById('monthly-compare-label');

  /** @type {Chart|null} */
  let chartInstance = null;
  /** Index of the currently-hovered arc/legend row (symmetric highlight). */
  let hoveredIndex = null;

  const _listeners = [];
  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

  function _surfaceColor() {
    const el = doc.documentElement || document.documentElement;
    return getComputedStyle(el).getPropertyValue('--surface');
  }

  function _destroyChart() {
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
    hoveredIndex = null;
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
    if (kpiSpentEl) kpiSpentEl.textContent = '';
    if (incomeEl) incomeEl.textContent = '';
    if (netEl) {
      netEl.textContent = '';
      netEl.classList.remove('net-negative', 'net-positive');
    }
    if (legendEl) legendEl.textContent = '';
    if (totalsEl) totalsEl.textContent = '';
    if (compareEl) compareEl.textContent = '';
    if (compareFootEl) compareFootEl.textContent = '';
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

  // -------------------------------------------------------------------------
  // Donut + legend cross-highlight (mirrors dashboard.js)
  // -------------------------------------------------------------------------

  function _highlightLegend(index) {
    if (!legendEl) return;
    legendEl.querySelectorAll('.legend-row').forEach((row, i) => {
      row.classList.toggle('is-hover', index !== null && i === index);
    });
  }

  function _setHighlight(index) {
    const idx = index === null || index === undefined ? null : index;
    if (idx === hoveredIndex) return;
    hoveredIndex = idx;
    if (chartInstance && chartInstance.setActiveElements) {
      chartInstance.setActiveElements(idx === null ? [] : [{ datasetIndex: 0, index: idx }]);
      chartInstance.update();
    }
    _highlightLegend(idx);
  }

  function _renderChart(data) {
    _destroyChart();
    if (!canvas) return;
    const surface = _surfaceColor();
    chartInstance = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: data.labels,
        datasets: [
          {
            data: data.values,
            backgroundColor: data.colors,
            hoverBackgroundColor: data.colors,
            borderColor: surface,
            borderWidth: 3,
            hoverBorderColor: surface,
            hoverOffset: 12,
          },
        ],
      },
      options: {
        cutout: '70%',
        responsive: false,
        maintainAspectRatio: false,
        layout: { padding: 10 },
        animation: {
          animateRotate: true,
          animateScale: true,
          duration: 900,
          easing: 'easeInOutQuart',
        },
        hover: { animationDuration: 160 },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        onHover(_event, elements) {
          _setHighlight(elements && elements.length ? elements[0].index : null);
        },
      },
    });
  }

  function _renderLegend(data, total) {
    if (!legendEl) return;
    legendEl.textContent = '';

    const fills = [];
    data.labels.forEach((label, i) => {
      const value = data.values[i];
      const color = data.colors[i];
      const pct = total > 0 ? Math.round((value / total) * 100) : 0;

      const rowEl = doc.createElement('div');
      rowEl.className = 'legend-row';
      rowEl.dataset.category = label;
      rowEl.style.setProperty('--hl', `color-mix(in srgb, ${color} 22%, transparent)`);
      rowEl.style.setProperty('--hl-ring', `color-mix(in srgb, ${color} 65%, transparent)`);
      rowEl.style.animationDelay = `${i * 55}ms`;

      const top = doc.createElement('div');
      top.className = 'legend-row-top';

      const dot = doc.createElement('span');
      dot.className = 'legend-dot';
      dot.style.background = color;

      const name = doc.createElement('span');
      name.className = 'legend-name';
      name.textContent = label;

      const amount = doc.createElement('span');
      amount.className = 'legend-amount';
      amount.textContent = formatCurrency(value);

      const pctEl = doc.createElement('span');
      pctEl.className = 'legend-pct';
      pctEl.textContent = `${pct}%`;

      top.append(dot, name, amount, pctEl);

      const bar = doc.createElement('div');
      bar.className = 'legend-bar';
      const barFill = doc.createElement('div');
      barFill.className = 'legend-bar-fill';
      barFill.style.background = color;
      barFill.style.width = '0%';
      barFill.style.transitionDelay = `${i * 45}ms`;
      bar.appendChild(barFill);

      rowEl.append(top, bar);

      rowEl.addEventListener('mouseenter', () => _setHighlight(i));
      rowEl.addEventListener('mouseleave', () => _setHighlight(null));

      legendEl.appendChild(rowEl);
      fills.push({ barFill, pct });
    });

    // Animate the share-bar fill in on the next frame (CSS width transition).
    requestAnimationFrame(() => {
      fills.forEach(({ barFill, pct }) => {
        barFill.style.width = `${pct}%`;
      });
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

      tr.append(nameTd, amountTd);
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

  /** Directional arrow glyph (no dash characters — house style). */
  function _arrow(pctChange) {
    if (pctChange === null || pctChange === undefined || pctChange === 0) return '';
    return pctChange > 0 ? '▲' : '▼';
  }

  function _numCell(text, cls) {
    const td = doc.createElement('td');
    td.classList.add('num');
    if (cls) td.classList.add(cls);
    td.textContent = text;
    return td;
  }

  function _pctCell(pctChange, cls) {
    const td = doc.createElement('td');
    td.classList.add('num', cls);
    const pill = doc.createElement('span');
    pill.className = 'delta-pill';
    const arrow = _arrow(pctChange);
    if (arrow) {
      const arrowEl = doc.createElement('span');
      arrowEl.className = 'arrow';
      arrowEl.textContent = arrow;
      pill.appendChild(arrowEl);
    }
    const pctText = doc.createElement('span');
    pctText.textContent =
      pctChange === null || pctChange === undefined ? 'n/a' : `${pctChange}%`;
    pill.appendChild(pctText);
    td.appendChild(pill);
    return td;
  }

  function _renderComparison(response) {
    if (!compareEl) return;
    compareEl.textContent = '';
    if (compareFootEl) compareFootEl.textContent = '';

    const rows = response.comparison ?? [];
    let sumCurrent = 0;
    let sumPrev = 0;

    for (const row of rows) {
      const cls = _deltaClass(row.pct_change);
      const tr = doc.createElement('tr');

      const nameTd = doc.createElement('td');
      nameTd.textContent = row.category;

      const currentTd = _numCell(formatCurrency(row.current));
      const prevTd = _numCell(formatCurrency(row.previous ?? '0'));
      prevTd.style.color = 'var(--muted)';
      const deltaTd = _numCell(formatCurrency(row.delta), cls);
      const pctTd = _pctCell(row.pct_change, cls);

      tr.append(nameTd, currentTd, prevTd, deltaTd, pctTd);
      compareEl.appendChild(tr);

      sumCurrent += Number(row.current) || 0;
      sumPrev += Number(row.previous) || 0;
    }

    if (compareFootEl && rows.length > 0) {
      const totDelta = sumCurrent - sumPrev;
      const totPct = sumPrev !== 0 ? Math.round((totDelta / Math.abs(sumPrev)) * 100) : null;
      const cls = _deltaClass(totPct);

      const labelTd = doc.createElement('td');
      labelTd.textContent = 'Total';

      const currentTd = _numCell(formatCurrency(sumCurrent));
      const prevTd = _numCell(formatCurrency(sumPrev));
      prevTd.style.color = 'var(--muted)';
      const deltaTd = _numCell(formatCurrency(totDelta), cls);
      const pctTd = _pctCell(totPct, cls);

      compareFootEl.append(labelTd, currentTd, prevTd, deltaTd, pctTd);
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
    const spent = spendTotal(response);
    const net = computeNet(response);

    _renderChart(data);
    _renderLegend(data, spent);

    if (spentEl) spentEl.textContent = formatCurrency(spent);
    if (kpiSpentEl) kpiSpentEl.textContent = formatCurrency(spent);
    // Income is derived so the three KPIs stay internally consistent:
    // net = income - spent  ->  income = net + spent.
    if (incomeEl) incomeEl.textContent = formatCurrency(net + spent);
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
