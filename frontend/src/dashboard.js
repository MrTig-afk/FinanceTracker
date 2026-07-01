/**
 * dashboard.js — DOM rendering layer.
 * Imports Chart.js and summary.js; touches the DOM only inside createDashboard().
 * No IO, no network at module level.
 */

import { Chart, DoughnutController, ArcElement, Legend } from 'chart.js';
Chart.register(DoughnutController, ArcElement, Legend);

import {
  toChartData,
  computeNet,
  formatCurrency,
  monthLabel,
  spendTotal,
  accountBalances,
} from './summary.js';

const COUNT_UP_DURATION_MS = 1100;
const PULSE_DURATION_MS = 950;
const AFFECTED_CATEGORIES = new Set(['Transport', 'Dining Out']);

/**
 * Create the dashboard controller bound to the given DOM root.
 * Pass `document` in production; pass a jsdom Document in tests.
 *
 * @param {Document} root
 * @returns {{
 *   render(summary: object, opts?: { pulse?: boolean }): void,
 *   showEmpty(): void,
 *   showError(err: Error): void,
 *   applyChartTheme(): void,
 *   destroy(): void,
 * }}
 */
export function createDashboard(root = document) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const monthLabelEl = root.getElementById('month-label');
  const sidebarMonthEl = root.getElementById('sidebar-month');
  const netValueEl = root.getElementById('net-value');
  const canvas = root.getElementById('donut-canvas');
  const spentTotalEl = root.getElementById('spent-total');
  const legendEl = root.getElementById('legend');
  const fuelToggleEl = root.getElementById('fuel-rule-toggle');
  const fuelNoteEl = root.getElementById('fuel-note');
  const messageEl = root.getElementById('message');
  const balancesEl = root.getElementById('balances');

  /** @type {Chart|null} */
  let chartInstance = null;

  /** Cached for applyChartTheme() and for the count-up baseline of the next render. */
  let lastSummary = null;
  let lastDisplayedTotal = 0;

  let rafId = null;
  let pulseTimeoutId = null;

  // -------------------------------------------------------------------------
  // Small DOM helpers
  // -------------------------------------------------------------------------

  function _destroyChart() {
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
  }

  function _clearLegend() {
    if (legendEl) legendEl.textContent = '';
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

  function _currentSurfaceColor() {
    const el = doc.documentElement || document.documentElement;
    return getComputedStyle(el).getPropertyValue('--surface');
  }

  function _cancelCountUp() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  function _cancelPulse() {
    if (pulseTimeoutId !== null) {
      clearTimeout(pulseTimeoutId);
      pulseTimeoutId = null;
    }
  }

  // -------------------------------------------------------------------------
  // SPENT count-up (ease-out-cubic tween, mirrors the mockup's tweenTotal)
  // -------------------------------------------------------------------------

  function _tweenSpentTotal(target) {
    if (!spentTotalEl) {
      lastDisplayedTotal = target;
      return;
    }

    _cancelCountUp();
    const start = lastDisplayedTotal;
    const t0 = performance.now();

    // `now` is the DOMHighResTimeStamp requestAnimationFrame passes to its
    // callback. Tests may stub rAF to invoke the callback immediately with a
    // large timestamp, which drives `p` to 1 on the very first frame.
    const step = (now) => {
      const elapsed = now - t0;
      const p = Math.min(1, elapsed / COUNT_UP_DURATION_MS);
      const eased = 1 - Math.pow(1 - p, 3);
      const current = start + (target - start) * eased;

      if (p >= 1) {
        spentTotalEl.textContent = formatCurrency(target);
        lastDisplayedTotal = target;
        rafId = null;
        return;
      }

      spentTotalEl.textContent = formatCurrency(current);
      rafId = requestAnimationFrame(step);
    };

    rafId = requestAnimationFrame(step);
  }

  // -------------------------------------------------------------------------
  // Legend
  // -------------------------------------------------------------------------

  function _highlightLegendFromArc(index) {
    if (!legendEl) return;
    const rows = legendEl.querySelectorAll('.legend-row');
    rows.forEach((row, i) => {
      row.classList.toggle('is-hover', index !== null && i === index);
    });
  }

  function _renderLegend(data, total) {
    _clearLegend();
    if (!legendEl) return;

    const rows = [];

    data.labels.forEach((label, i) => {
      const value = data.values[i];
      const color = data.colors[i];
      const pct = total > 0 ? Math.round((value / total) * 100) : 0;

      const rowEl = document.createElement('div');
      rowEl.className = 'legend-row';
      rowEl.style.setProperty('--hl', `color-mix(in srgb, ${color} 20%, transparent)`);
      rowEl.style.animationDelay = `${i * 55}ms`;
      rowEl.dataset.category = label;

      rowEl.addEventListener('mouseenter', () => {
        if (!chartInstance) return;
        chartInstance.setActiveElements([{ datasetIndex: 0, index: i }]);
        chartInstance.update();
      });
      rowEl.addEventListener('mouseleave', () => {
        if (!chartInstance) return;
        chartInstance.setActiveElements([]);
        chartInstance.update();
      });

      const top = document.createElement('div');
      top.className = 'legend-row-top';

      const dot = document.createElement('span');
      dot.className = 'legend-dot';
      dot.style.background = color;

      const name = document.createElement('span');
      name.className = 'legend-name';
      name.textContent = label;

      const amount = document.createElement('span');
      amount.className = 'legend-amount';
      amount.textContent = formatCurrency(value);

      const pctEl = document.createElement('span');
      pctEl.className = 'legend-pct';
      pctEl.textContent = `${pct}%`;

      top.appendChild(dot);
      top.appendChild(name);
      top.appendChild(amount);
      top.appendChild(pctEl);

      const bar = document.createElement('div');
      bar.className = 'legend-bar';
      const barFill = document.createElement('div');
      barFill.className = 'legend-bar-fill';
      barFill.style.width = '0%';
      barFill.style.transitionDelay = `${i * 45}ms`;
      bar.appendChild(barFill);

      rowEl.appendChild(top);
      rowEl.appendChild(bar);
      legendEl.appendChild(rowEl);

      rows.push({ barFill, pct });
    });

    // Animate the bar fill in on the next frame so the CSS width transition runs.
    requestAnimationFrame(() => {
      rows.forEach(({ barFill, pct }) => {
        barFill.style.width = `${pct}%`;
      });
    });
  }

  // -------------------------------------------------------------------------
  // Fuel card
  // -------------------------------------------------------------------------

  function _renderFuelNote(summary) {
    if (!fuelToggleEl && !fuelNoteEl) return;

    const applied = Boolean(summary.fuel_rule_applied);
    if (fuelToggleEl) fuelToggleEl.checked = applied;

    if (!fuelNoteEl) return;

    const n = summary.fuel_rule_eligible ?? 0;
    const amount = summary.fuel_rule_eligible_amount ?? '0.00';

    let text;
    if (applied) {
      text =
        `${n} small servo purchase${n === 1 ? '' : 's'} ` +
        `(${formatCurrency(Math.abs(Number(amount) || 0))}) moved to Dining Out and saved to your ledger.`;
    } else {
      text =
        'Off — small servo purchases stay under Transport. Turn on to reclassify ' +
        `${n} transaction${n === 1 ? '' : 's'}.`;
    }

    fuelNoteEl.textContent = text;
    fuelNoteEl.classList.toggle('fuel-note--on', applied);
    fuelNoteEl.classList.toggle('fuel-note--off', !applied);
  }

  // -------------------------------------------------------------------------
  // Account balances (local-only; never sent off-machine)
  // -------------------------------------------------------------------------

  function _renderBalances(summary) {
    if (!balancesEl) return;
    balancesEl.textContent = '';

    const rows = accountBalances(summary);
    if (rows.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'balances-empty';
      empty.textContent = 'No account balances yet.';
      balancesEl.appendChild(empty);
      return;
    }

    rows.forEach(({ label, opening, closing }) => {
      const rowEl = document.createElement('div');
      rowEl.className = 'balance-row';

      const bankEl = document.createElement('span');
      bankEl.className = 'balance-row-bank';
      bankEl.textContent = label;

      const figuresEl = document.createElement('span');
      figuresEl.className = 'balance-row-figures';
      const openingText = opening === null ? '—' : formatCurrency(opening);
      const closingText = closing === null ? '—' : formatCurrency(closing);
      figuresEl.textContent = `${openingText} → ${closingText}`;

      rowEl.appendChild(bankEl);
      rowEl.appendChild(figuresEl);
      balancesEl.appendChild(rowEl);
    });
  }

  function _pulseAffectedRows() {
    _cancelPulse();

    if (legendEl) {
      legendEl.querySelectorAll('.legend-row').forEach((rowEl) => {
        if (AFFECTED_CATEGORIES.has(rowEl.dataset.category)) {
          rowEl.classList.add('pulse-hi');
        }
      });
    }
    if (fuelNoteEl) fuelNoteEl.classList.add('note-in');

    pulseTimeoutId = setTimeout(() => {
      if (legendEl) {
        legendEl.querySelectorAll('.pulse-hi').forEach((el) => el.classList.remove('pulse-hi'));
      }
      if (fuelNoteEl) fuelNoteEl.classList.remove('note-in');
      pulseTimeoutId = null;
    }, PULSE_DURATION_MS);
  }

  // -------------------------------------------------------------------------
  // Chart
  // -------------------------------------------------------------------------

  function _renderChart(data) {
    _destroyChart();
    if (!canvas) return;

    chartInstance = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: data.labels,
        datasets: [
          {
            data: data.values,
            backgroundColor: data.colors,
            borderColor: _currentSurfaceColor(),
            borderWidth: 3,
            hoverOffset: 7,
          },
        ],
      },
      options: {
        cutout: '70%',
        responsive: false,
        maintainAspectRatio: false,
        animation: {
          animateRotate: true,
          animateScale: true,
          duration: 900,
          easing: 'easeInOutQuart',
        },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
        },
        onHover(event, elements) {
          _highlightLegendFromArc(elements && elements.length ? elements[0].index : null);
        },
      },
    });
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  /**
   * Render summary data to the dashboard.
   * @param {object} summary
   * @param {{ pulse?: boolean }} opts  pulse=true highlights the fuel-affected
   *   legend rows + fuel note (used right after a reclassify toggle).
   */
  function render(summary, { pulse = false } = {}) {
    lastSummary = summary;

    // Subtitle + sidebar footer
    const label = monthLabel(summary.year_month);
    if (monthLabelEl) {
      monthLabelEl.textContent = summary.year_month
        ? `Spending breakdown for ${label}`
        : 'Spending breakdown';
    }
    if (sidebarMonthEl) {
      sidebarMonthEl.textContent = summary.year_month
        ? `${label} · CommBank + Westpac imported.`
        : 'No statements imported yet.';
    }

    // Net value — colour-coded by sign
    const net = computeNet(summary);
    if (netValueEl) {
      netValueEl.textContent = formatCurrency(net);
      netValueEl.classList.toggle('net-negative', net < 0);
      netValueEl.classList.toggle('net-positive', net >= 0);
    }

    // Donut + SPENT + legend
    const data = toChartData(summary);
    const total = spendTotal(summary);

    if (data.labels.length === 0) {
      _destroyChart();
      _showMessage('No spending to chart yet.');
      if (spentTotalEl) spentTotalEl.textContent = formatCurrency(0);
      lastDisplayedTotal = 0;
      _clearLegend();
    } else {
      _hideMessage();
      _renderChart(data);
      _tweenSpentTotal(total);
      _renderLegend(data, total);
    }

    // Fuel card
    _renderFuelNote(summary);

    // Per-account balances (local-only)
    _renderBalances(summary);

    if (pulse) {
      _pulseAffectedRows();
    }
  }

  /**
   * Re-sync the donut ring border to the current --surface value without
   * re-animating the chart. Called by main.js after a theme toggle. No-op
   * when no chart is live.
   */
  function applyChartTheme() {
    if (!chartInstance) return;
    chartInstance.data.datasets[0].borderColor = _currentSurfaceColor();
    chartInstance.update('none');
  }

  /**
   * Show an empty-data state.
   * Called by main.js when summary.count === 0 or totals is empty.
   */
  function showEmpty() {
    _destroyChart();
    _clearLegend();
    if (spentTotalEl) spentTotalEl.textContent = formatCurrency(0);
    lastDisplayedTotal = 0;
    _showMessage('No data yet — upload a statement to get started.');
    if (fuelToggleEl) fuelToggleEl.checked = false;
    if (fuelNoteEl) {
      fuelNoteEl.textContent = 'No spending data yet.';
      fuelNoteEl.classList.remove('fuel-note--on');
      fuelNoteEl.classList.add('fuel-note--off');
    }
    if (balancesEl) balancesEl.textContent = '';
  }

  /**
   * Show a generic error message.
   * Never exposes raw response bodies, stack traces, or transaction data.
   * @param {import('./api.js').ApiError} err
   */
  function showError(err) {
    _destroyChart();
    _clearLegend();
    const statusPart = err && err.status ? ` (status ${err.status})` : '';
    _showMessage(`Could not load summary.${statusPart}`);
  }

  /**
   * Tear down the Chart instance and cancel any in-flight animation timers.
   * Call before removing the dashboard from the DOM.
   */
  function destroy() {
    _destroyChart();
    _cancelCountUp();
    _cancelPulse();
  }

  return { render, showEmpty, showError, applyChartTheme, destroy };
}
