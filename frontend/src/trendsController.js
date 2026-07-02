/**
 * trendsController.js — DOM wiring for the Trends view (GET /trends).
 *
 * Renders an inline-SVG multi-line chart (no charting library) with a clean
 * right-hand legend column, matching the approved design. Two interactions:
 *   - HOVER a line OR a legend row -> bidirectional spotlight (that series goes
 *     "hot", everything else dims).
 *   - CLICK a line or legend row -> hide just that series (kept in a `hidden`
 *     Set); the other lines stay put. Click again to restore. Multiple series
 *     are independently hideable. Hovering a hidden row does not spotlight.
 *
 * PRIVACY: renders only aggregated per-category monthly totals the backend
 * already aggregated locally — no raw transaction data ever reaches this
 * module. Mirrors the monthlyController.js idiom (injectable fetchFn,
 * _on/_listeners/destroy() teardown) and the Overview donut's arc<->legend
 * cross-highlight idiom (dashboard.js).
 */

import { fetchTrends } from './api.js';
import { colorFor, formatCurrency, parseAmount } from './summary.js';

const _WINDOW_OPTIONS = [3, 6, 12, 24];
const _NOT_ENOUGH_HISTORY = 'Not enough history yet. Upload at least two months to see trends.';

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

/** Whole-dollar axis label (no cents — keeps gridline labels compact). */
function _axisLabel(v) {
  return '$' + Math.round(v).toLocaleString('en-AU');
}

/**
 * Wire the Trends view.
 *
 * Requires the following elements to be present in `root`:
 *   #trends-window   (window <select>)
 *   #trends-message  (empty/error banner)
 *   #trends-chart    (inline <svg> container)
 *   #trends-legend   (legend row container)
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
  const svgEl = root.getElementById('trends-chart');
  const legendEl = root.getElementById('trends-legend');

  let _selectPopulated = false;

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

  /**
   * Build renderable series from the response: exclude Income (mirrors the
   * controller filter), take the absolute magnitude of each signed value.
   * @returns {Array<{ name: string, color: string, values: number[] }>}
   */
  function _buildSeries(response) {
    return (response.series ?? [])
      .filter((s) => s.category !== 'Income')
      .map((s) => ({
        name: s.category,
        color: colorFor(s.category),
        values: (s.values ?? []).map((v) => Math.abs(parseAmount(v))),
      }));
  }

  function _allZero(series) {
    return series.every((s) => s.values.every((v) => v === 0));
  }

  // -------------------------------------------------------------------------
  // Spotlight (hover) + hide (click) — bidirectional line<->legend highlight.
  // -------------------------------------------------------------------------

  function _renderHidden() {
    _groups.forEach((g) => g.classList.toggle('hide', _hidden.has(g.dataset.line)));
    _rows.forEach((r) => r.classList.toggle('off', _hidden.has(r.dataset.legend)));
  }

  function _highlight(name) {
    // Only spotlight a series that is currently visible.
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

    const maxV = Math.max(0, ...series.flatMap((s) => s.values));
    const niceMax = Math.max(500, Math.ceil(maxV / 500) * 500);

    const x = (i) => _PAD_L + (i * _PLOT_W) / xDen;
    const y = (v) => _PAD_T + _PLOT_H - (v / niceMax) * _PLOT_H;

    // Gridlines + y-axis labels (mono).
    for (let g = 0; g <= _GRID_STEPS; g++) {
      const val = (niceMax / _GRID_STEPS) * g;
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

    // Series: wide transparent hit-line + visible line + points, one <g> each.
    series.forEach((s) => {
      const g = _svg('g', { class: 's-group' });
      g.dataset.line = s.name;
      const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
      g.appendChild(_svg('polyline', { class: 's-hit', points: pts }));
      g.appendChild(_svg('polyline', { class: 's-line', points: pts, stroke: s.color }));
      s.values.forEach((v, i) => {
        g.appendChild(_svg('circle', { class: 's-pt', cx: x(i), cy: y(v), r: 3, fill: s.color }));
      });
      svgEl.appendChild(g);
      _groups.push(g);
    });

    // Legend: colored dot + name + latest-month value.
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
      val.textContent = formatCurrency(s.values[s.values.length - 1] ?? 0);

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
    _populateWindowSelect(response.window);

    const monthsAvailable = response.months_available ?? 0;
    const months = response.months ?? [];

    if (monthsAvailable <= 1 || months.length === 0) {
      _showMessage(_NOT_ENOUGH_HISTORY);
      return;
    }

    const series = _buildSeries(response);

    if (series.length === 0 || _allZero(series)) {
      _showMessage(_NOT_ENOUGH_HISTORY);
      return;
    }

    _renderChart(response, series);
  }

  async function _fetchAndRender(months) {
    try {
      const response = await _fetchFn(months);
      _render(response);
    } catch {
      // Never expose raw error/stack — fixed safe message only.
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
    _clearChart();
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { load, destroy };
}
