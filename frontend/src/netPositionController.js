/**
 * netPositionController.js — DOM wiring for the Net position card (GET /balances).
 *
 * Renders an inline-SVG multi-line chart (no charting library) of each bank's
 * monthly closing balance plus a derived combined-net line, matching the Trends
 * view's chart idiom. Two interactions, identical to trendsController.js:
 *   - HOVER a line OR a legend row -> bidirectional spotlight (that series goes
 *     "hot", everything else dims).
 *   - CLICK a line or legend row -> hide just that series; click again restores.
 *
 * PRIVACY: balances are SENSITIVE. This module renders only the owner's own
 * closing-balance figures served by the LOCAL backend; no balance value ever
 * leaves the machine, and nothing here makes an off-machine call. Mirrors the
 * trendsController.js idiom (injectable fetchFn, _on/_listeners/destroy()
 * teardown, the same spotlight/hide cross-highlight).
 */

import { fetchBalances } from './api.js';
import { formatCurrency, parseAmount } from './summary.js';

// Bank display labels — mirrors summary.js `_BANK_LABELS` (not exported there;
// defined locally to avoid changing summary.js exports). Unknown keys capitalised.
const _BANK_LABELS = {
  commbank: 'CommBank',
  westpac: 'Westpac',
};

// Fixed line colours, distinct from the category palette. Net is dashed (§6.5).
const _BANK_COLORS = {
  commbank: '#f6b93b',
  westpac: '#e55039',
};
const _NET_COLOR = '#8e7dff';

// SVG geometry (matches the design's viewBox 0 0 900 400).
const _NS = 'http://www.w3.org/2000/svg';
const _VBW = 900;
const _VBH = 400;
const _PAD_L = 58;
const _PAD_R = 22;
const _PAD_T = 16;
const _PAD_B = 36;
const _PLOT_W = _VBW - _PAD_L - _PAD_R;
const _PLOT_H = _VBH - _PAD_T - _PAD_B;
const _GRID_STEPS = 4; // 5 gridlines (0..4)

/** color-mix helper (matches the app's tint idiom used by the donut legend). */
function _mix(hex, pct) {
  return `color-mix(in srgb, ${hex} ${pct}%, transparent)`;
}

/** '2026-01' -> 'Jan'. Malformed input returned unchanged (no throw). */
function _monthAbbrev(ym) {
  if (typeof ym !== 'string') return String(ym ?? '');
  const parts = ym.split('-');
  if (parts.length < 2) return ym;
  const y = Number(parts[0]);
  const m = Number(parts[1]);
  if (!Number.isFinite(y) || !Number.isFinite(m) || m < 1 || m > 12) return ym;
  return new Date(y, m - 1, 1).toLocaleString('en-AU', { month: 'short' });
}

/** Whole-dollar axis label; negatives render as '-$1,000' (no cents). */
function _axisLabel(v) {
  return (v < 0 ? '-' : '') + '$' + Math.round(Math.abs(v)).toLocaleString('en-AU');
}

/** Human label for a bank key (defensive capitalisation for unknown keys). */
function _bankLabel(bank) {
  return _BANK_LABELS[bank] ?? (bank.charAt(0).toUpperCase() + bank.slice(1));
}

/**
 * Split `values` into maximal contiguous runs of non-null indices.
 * @param {Array<number|null>} values
 * @returns {number[][]}  each inner array is a run of consecutive indices.
 */
function _runs(values) {
  const out = [];
  let run = [];
  values.forEach((v, i) => {
    if (v === null) {
      if (run.length) out.push(run);
      run = [];
    } else {
      run.push(i);
    }
  });
  if (run.length) out.push(run);
  return out;
}

/** Latest (last) non-null value in `values`, or null when all null. */
function _latestValue(values) {
  for (let i = values.length - 1; i >= 0; i--) {
    if (values[i] !== null) return values[i];
  }
  return null;
}

/**
 * Wire the Net position card.
 *
 * Requires the following elements to be present in `root`:
 *   #netpos-message  (empty/error banner)
 *   #netpos-chart    (inline <svg> container)
 *   #netpos-legend   (legend row container)
 *
 * @param {{
 *   root?: Document,
 *   fetchFn?: () => Promise<object>,
 * }} options
 * @returns {{ load(): Promise<void>, destroy(): void }}
 */
export function createNetPosition({ root = document, fetchFn } = {}) {
  const _fetchFn = fetchFn ?? fetchBalances;
  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const messageEl = root.getElementById('netpos-message');
  const svgEl = root.getElementById('netpos-chart');
  const legendEl = root.getElementById('netpos-legend');

  /** Names of series the user has toggled off (hidden). Reset on each render. */
  let _hidden = new Set();
  /** Live series-group and legend-row elements, keyed for spotlight/hide wiring. */
  let _groups = [];
  let _rows = [];

  const _listeners = [];
  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

  function _svg(tag, attrs) {
    const e = doc.createElementNS(_NS, tag);
    for (const k in attrs) e.setAttribute(k, String(attrs[k]));
    return e;
  }

  function _clearChart() {
    if (svgEl) svgEl.textContent = '';
    if (legendEl) legendEl.textContent = '';
    _groups = [];
    _rows = [];
    _hidden = new Set();
  }

  function _hideMessage() {
    if (messageEl) messageEl.hidden = true;
  }

  function _showMessage(text) {
    _clearChart();
    if (messageEl) {
      messageEl.textContent = text;
      messageEl.hidden = false;
    }
  }

  /**
   * Build renderable series from the response: one per bank (signed closing
   * balances, nulls preserved as gaps) plus a combined 'Net' series. No
   * Math.abs — balances can legitimately be negative.
   * @returns {Array<{ name: string, color: string, values: Array<number|null>, dashed: boolean }>}
   */
  function _buildSeries(response) {
    const parse = (v) => (v === null || v === undefined ? null : parseAmount(v));
    const series = (response.series ?? []).map((s) => ({
      name: _bankLabel(s.bank),
      color: _BANK_COLORS[s.bank] ?? _NET_COLOR,
      values: (s.values ?? []).map(parse),
      dashed: false,
    }));
    if (series.length > 0) {
      series.push({
        name: 'Net',
        color: _NET_COLOR,
        values: (response.net ?? []).map(parse),
        dashed: true,
      });
    }
    return series;
  }

  // -------------------------------------------------------------------------
  // Spotlight (hover) + hide (click) — bidirectional line<->legend highlight.
  // -------------------------------------------------------------------------

  function _renderHidden() {
    _groups.forEach((g) => g.classList.toggle('hide', _hidden.has(g.dataset.line)));
    _rows.forEach((r) => r.classList.toggle('off', _hidden.has(r.dataset.legend)));
  }

  function _highlight(name) {
    const active = name && !_hidden.has(name) ? name : null;
    _groups.forEach((g) => {
      const on = !active || g.dataset.line === active;
      g.classList.toggle('dim', !!active && !on);
      g.classList.toggle('hot', !!active && on);
    });
    _rows.forEach((r) => {
      const on = !active || r.dataset.legend === active;
      r.classList.toggle('dim', !!active && !on && !_hidden.has(r.dataset.legend));
      r.classList.toggle('hot', !!active && on);
    });
  }

  function _toggleHide(name) {
    if (_hidden.has(name)) _hidden.delete(name);
    else _hidden.add(name);
    _renderHidden();
    _highlight(null);
  }

  function _renderChart(response, series) {
    _clearChart();
    if (!svgEl) return;

    const months = response.months ?? [];
    const n = months.length;
    const xDen = Math.max(1, n - 1);

    // Signed Y domain: include 0 and any negatives, on a 500 step.
    const nonNull = series.flatMap((s) => s.values.filter((v) => v !== null));
    const dataMax = Math.max(0, ...nonNull);
    const dataMin = Math.min(0, ...nonNull);
    const niceMax = Math.max(500, Math.ceil(dataMax / 500) * 500);
    const niceMin = Math.min(0, Math.floor(dataMin / 500) * 500);

    const x = (i) => _PAD_L + (i * _PLOT_W) / xDen;
    const y = (v) => _PAD_T + _PLOT_H - ((v - niceMin) / (niceMax - niceMin)) * _PLOT_H;

    // Gridlines + y-axis labels (5 levels evenly spaced niceMin..niceMax).
    for (let g = 0; g <= _GRID_STEPS; g++) {
      const val = niceMin + ((niceMax - niceMin) / _GRID_STEPS) * g;
      svgEl.appendChild(
        _svg('line', { class: 'grid-line', x1: _PAD_L, x2: _VBW - _PAD_R, y1: y(val), y2: y(val) }),
      );
      const t = _svg('text', { class: 'axis-label', x: _PAD_L - 10, y: y(val) + 4, 'text-anchor': 'end' });
      t.textContent = _axisLabel(val);
      svgEl.appendChild(t);
    }

    // X-axis labels (month abbreviations).
    months.forEach((m, i) => {
      const t = _svg('text', { class: 'x-label', x: x(i), y: _VBH - 10, 'text-anchor': 'middle' });
      t.textContent = _monthAbbrev(m);
      svgEl.appendChild(t);
    });

    // Series: segmented polylines over contiguous non-null runs + points, one <g>
    // each. Null months render as visible breaks (no segment, no point) — never
    // zeros, never a bridging line.
    series.forEach((s) => {
      const g = _svg('g', { class: 's-group' });
      g.dataset.line = s.name;
      for (const run of _runs(s.values)) {
        if (run.length < 2) continue; // a lone point draws no polyline
        const pts = run.map((i) => `${x(i)},${y(s.values[i])}`).join(' ');
        g.appendChild(_svg('polyline', { class: 's-hit', points: pts }));
        const lineClass = s.dashed ? 's-line netline' : 's-line';
        g.appendChild(_svg('polyline', { class: lineClass, points: pts, stroke: s.color }));
      }
      s.values.forEach((v, i) => {
        if (v === null) return;
        g.appendChild(_svg('circle', { class: 's-pt', cx: x(i), cy: y(v), r: 3, fill: s.color }));
      });
      svgEl.appendChild(g);
      _groups.push(g);
    });

    // Legend: colored dot + name + latest non-null value.
    series.forEach((s) => {
      const row = doc.createElement('button');
      row.className = 'trends-legend-row';
      row.type = 'button';
      row.dataset.legend = s.name;
      row.style.setProperty('--hl', _mix(s.color, 22));
      row.style.setProperty('--hl-ring', _mix(s.color, 65));

      const dot = doc.createElement('span');
      dot.className = 'trends-legend-dot';
      dot.style.background = s.color;

      const name = doc.createElement('span');
      name.className = 'trends-legend-name';
      name.textContent = s.name;

      const val = doc.createElement('span');
      val.className = 'trends-legend-val';
      const latest = _latestValue(s.values);
      val.textContent = latest === null ? '' : formatCurrency(latest);

      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(val);
      legendEl.appendChild(row);
      _rows.push(row);
    });

    // Wire bidirectional spotlight + click-to-hide on both sides.
    _groups.forEach((g) => {
      g.addEventListener('mouseenter', () => _highlight(g.dataset.line));
      g.addEventListener('mouseleave', () => _highlight(null));
      g.addEventListener('click', () => _toggleHide(g.dataset.line));
    });
    _rows.forEach((r) => {
      r.addEventListener('mouseenter', () => _highlight(r.dataset.legend));
      r.addEventListener('mouseleave', () => _highlight(null));
      r.addEventListener('click', () => _toggleHide(r.dataset.legend));
    });
  }

  function _render(response) {
    _hideMessage();

    const months = response.months ?? [];
    const series = _buildSeries(response);

    if (months.length === 0 || series.length === 0) {
      _showMessage('Balances build up from your next upload.');
      return;
    }

    _renderChart(response, series);
  }

  async function load() {
    try {
      const response = await _fetchFn();
      _render(response);
    } catch {
      // Never expose raw error/stack — fixed safe message only.
      _showMessage('Could not load net position.');
    }
  }

  function destroy() {
    _clearChart();
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { load, destroy };
}
