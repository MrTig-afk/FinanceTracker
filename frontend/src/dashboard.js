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
 * @param {{ onCategorySelect?: (category: string, meta: { month: string|null, color: string }) => void }} [opts]
 *   onCategorySelect fires when a legend row or donut slice is clicked, with the
 *   category name and the month/colour needed to open the drill-down drawer.
 * @returns {{
 *   render(summary: object, opts?: { pulse?: boolean }): void,
 *   showEmpty(): void,
 *   showError(err: Error): void,
 *   applyChartTheme(): void,
 *   destroy(): void,
 * }}
 */
export function createDashboard(root = document, { onCategorySelect } = {}) {
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const monthLabelEl = root.getElementById('month-label');
  const sidebarMonthEl = root.getElementById('sidebar-month');
  const netValueEl = root.getElementById('net-value');
  const canvas = root.getElementById('donut-canvas');
  const spentTotalEl = root.getElementById('spent-total');
  const legendEl = root.getElementById('legend');
  const fuelToggleEl = root.getElementById('fuel-rule-toggle');
  const messageEl = root.getElementById('message');
  const balancesEl = root.getElementById('balances');

  /** @type {Chart|null} */
  let chartInstance = null;

  /** Index of the currently-hovered arc/legend row (arc<->legend symmetric highlight). */
  let hoveredIndex = null;

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
    hoveredIndex = null;
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

  // --- Last-known summary snapshot (offline fallback) -----------------------
  //
  // PRIVACY: this stores the owner's own summary on the owner's own device —
  // the same device that renders it. The service-worker rule (API responses
  // are never cached in the SW cache) is untouched; this is a deliberate,
  // app-level, device-local snapshot so the dashboard can show the last synced
  // data while the laptop or the Tailscale link is unreachable.

  /** localStorage key for the last successfully rendered summary. */
  const SNAPSHOT_KEY = 'ft_last_summary';

  // True while re-rendering FROM the snapshot, so the restore does not
  // overwrite the snapshot's saved_at with the render time.
  let restoringSnapshot = false;

  function _saveSnapshot(summary) {
    if (restoringSnapshot) return;
    try {
      localStorage.setItem(
        SNAPSHOT_KEY,
        JSON.stringify({ saved_at: new Date().toISOString(), summary }),
      );
    } catch {
      // Private browsing / storage disabled / quota — the snapshot is a
      // nice-to-have, never worth breaking a render over.
    }
  }

  /** @returns {{ summary: object, savedAt: string } | null} */
  function _loadSnapshot() {
    try {
      const raw = localStorage.getItem(SNAPSHOT_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      if (!parsed.summary || typeof parsed.summary !== 'object') return null;
      return { summary: parsed.summary, savedAt: String(parsed.saved_at || '') };
    } catch {
      return null; // corrupt JSON / storage error — fail closed to the error path
    }
  }

  function _snapshotDateLabel(savedAt) {
    const t = Date.parse(savedAt);
    if (Number.isNaN(t)) return 'an earlier session';
    return new Date(t).toLocaleString('en-AU', {
      day: 'numeric',
      month: 'short',
      hour: 'numeric',
      minute: '2-digit',
    });
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

  /**
   * Shared arc<->legend highlight: sets both (a) the active/lifted chart arc
   * and (b) the matching legend row's `.is-hover` class, so hovering either
   * one produces the same symmetric visible pair. Passing `null` clears both.
   */
  function _setHighlight(index) {
    const idx = index === null || index === undefined ? null : index;
    if (idx === hoveredIndex) return; // guard: no redundant chart.update() on mousemove
    hoveredIndex = idx;
    // (a) lift/emphasise the matching arc
    if (chartInstance) {
      chartInstance.setActiveElements(idx === null ? [] : [{ datasetIndex: 0, index: idx }]);
      chartInstance.update();
    }
    // (b) highlight the matching legend row
    _highlightLegendFromArc(idx);
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
      rowEl.style.setProperty('--hl', `color-mix(in srgb, ${color} 38%, transparent)`);
      rowEl.style.setProperty('--hl-ring', `color-mix(in srgb, ${color} 65%, transparent)`);
      rowEl.style.animationDelay = `${i * 55}ms`;
      rowEl.dataset.category = label;

      rowEl.addEventListener('mouseenter', () => _setHighlight(i));
      rowEl.addEventListener('mouseleave', () => _setHighlight(null));

      // Click / keyboard-activate a legend row to open its category drill-down.
      if (onCategorySelect) {
        rowEl.classList.add('is-clickable');
        rowEl.setAttribute('role', 'button');
        rowEl.tabIndex = 0;
        const activate = () =>
          onCategorySelect(label, {
            month: lastSummary?.year_month ?? null,
            color,
          });
        rowEl.addEventListener('click', activate);
        rowEl.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            activate();
          }
        });
      }

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

  // Sync the toggle to the backend's actual rule state. The user-facing
  // confirmation is a transient toast fired on the toggle action (see
  // fuelToast.js, wired in main.js) — not a rendered note here.
  function _syncFuelToggle(summary) {
    if (!fuelToggleEl) return;
    // Reflect the persisted preference (fuel_rule_enabled), NOT whether a row was
    // actually moved (fuel_rule_applied) — otherwise the toggle snaps back to off
    // whenever there are no eligible under-$10 fuel rows to reclassify. Fall back
    // to fuel_rule_applied for older payloads that predate the preference field.
    const on =
      summary.fuel_rule_enabled ?? summary.fuel_rule_applied ?? false;
    fuelToggleEl.checked = Boolean(on);
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

    pulseTimeoutId = setTimeout(() => {
      if (legendEl) {
        legendEl.querySelectorAll('.pulse-hi').forEach((el) => el.classList.remove('pulse-hi'));
      }
      pulseTimeoutId = null;
    }, PULSE_DURATION_MS);
  }

  // -------------------------------------------------------------------------
  // Chart
  // -------------------------------------------------------------------------

  function _renderChart(data) {
    _destroyChart();
    hoveredIndex = null;
    if (!canvas) return;

    chartInstance = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: data.labels,
        datasets: [
          {
            data: data.values,
            backgroundColor: data.colors,
            // Pin the hovered arc to its exact palette colour. Without this,
            // Chart.js derives a hover colour (saturate+darken) that shifts
            // periwinkle hues toward blue, so the arc no longer matched its
            // legend swatch on hover.
            hoverBackgroundColor: data.colors,
            borderColor: _currentSurfaceColor(),
            borderWidth: 3,
            hoverBorderColor: _currentSurfaceColor(),
            hoverOffset: 12,
          },
        ],
      },
      options: {
        cutout: '70%',
        responsive: false,
        maintainAspectRatio: false,
        // Explicit hard margin on top of Chart.js's own hoverOffset-aware
        // radius reservation, so the popped-out hovered arc always has
        // guaranteed clearance and can never be clipped by the canvas edge
        // (fixed-size, non-responsive canvas — see #donut-canvas in index.html).
        layout: { padding: 10 },
        animation: {
          animateRotate: true,
          animateScale: true,
          duration: 900,
          easing: 'easeInOutQuart',
        },
        // Snappier than the default 400ms hover animation so the pop-out reads
        // as an immediate, deliberate response rather than a slow drift.
        hover: { animationDuration: 160 },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
        },
        onHover(event, elements) {
          _setHighlight(elements && elements.length ? elements[0].index : null);
        },
        onClick(event, elements) {
          if (!onCategorySelect || !elements || !elements.length) return;
          const idx = elements[0].index;
          onCategorySelect(data.labels[idx], {
            month: lastSummary?.year_month ?? null,
            color: data.colors[idx],
          });
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
    _saveSnapshot(summary);

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

    // Fuel card — keep the toggle in sync with the backend's rule state.
    _syncFuelToggle(summary);

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
    if (balancesEl) balancesEl.textContent = '';
  }

  /**
   * Show a fetch-failure state.
   * When a device-local snapshot of the last successful summary exists, render
   * it and banner "showing data from <date>" — the offline mode the app was
   * always meant to have (uploads queue separately via queue.js). Without a
   * snapshot, fall back to the plain error, adding a Tailscale hint for
   * network-level failures (no HTTP status = the laptop was never reached).
   * Never exposes raw response bodies, stack traces, or transaction data.
   * @param {import('./api.js').ApiError} err
   */
  function showError(err) {
    const snapshot = _loadSnapshot();
    if (snapshot) {
      restoringSnapshot = true;
      try {
        render(snapshot.summary);
      } finally {
        restoringSnapshot = false;
      }
      _showMessage(
        `Offline - showing data from ${_snapshotDateLabel(snapshot.savedAt)}. ` +
          'New uploads will be queued.',
      );
      return;
    }

    _destroyChart();
    _clearLegend();
    const statusPart = err && err.status ? ` (status ${err.status})` : '';
    const hint =
      err && err.status ? '' : ' Check that Tailscale is connected on this device.';
    _showMessage(`Could not load summary.${statusPart}${hint}`);
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
