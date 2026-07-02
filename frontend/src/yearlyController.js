/**
 * yearlyController.js — DOM wiring for the Yearly view (GET /year).
 * PRIVACY: renders only aggregated totals/comparison the backend already
 * aggregated locally — no raw transaction data ever reaches this module.
 * Mirrors the categoryContextController.js idiom (injectable fetchFn,
 * _on/_listeners/destroy() teardown). The donut, legend and arc<->legend
 * cross-highlight follow the Overview donut idiom in dashboard.js.
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
 *   #yearly-kpi-spent      (Spent KPI value)
 *   #yearly-income         (Income KPI value)
 *   #yearly-net            (Net KPI value)
 *   #yearly-legend         (horizontal wrap legend)
 *   #yearly-totals         (totals <tbody>)
 *   #yearly-compare        (comparison <tbody>)
 *   #yearly-compare-foot   (comparison totals footer <tr>)
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
  const kpiSpentEl = root.getElementById('yearly-kpi-spent');
  const incomeEl = root.getElementById('yearly-income');
  const netEl = root.getElementById('yearly-net');
  const legendEl = root.getElementById('yearly-legend');
  const totalsEl = root.getElementById('yearly-totals');
  const compareEl = root.getElementById('yearly-compare');
  const compareFootEl = root.getElementById('yearly-compare-foot');
  const compareLabelEl = root.getElementById('yearly-compare-label');

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
    for (const y of available) {
      const opt = doc.createElement('option');
      opt.value = y;
      opt.textContent = y;
      if (y === selected) opt.selected = true;
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
      _populateSelect(response.available_years ?? [], response.y ?? null);
      _showMessage('No data yet. Upload a statement to get started.');
      return;
    }

    _populateSelect(response.available_years ?? [], response.y);

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
