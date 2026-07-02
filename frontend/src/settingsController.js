/**
 * settingsController.js — DOM wiring for the Settings view (Feature E).
 * PRIVACY: everything here is the owner's own local settings, notification
 * preferences, and learned-correction notes. Nothing is sent off-machine; the
 * only calls are to the owner's own local backend. No transaction data crosses
 * any boundary from this screen.
 *
 * Mirrors createCategoryContext: a fetch-render-save controller returning
 * { load() }, injectable api for tests, best-effort (load() never throws).
 */

import {
  getSettings,
  putSettings,
  getCorrections,
  deleteCorrection,
  getCategoriserStatus,
  postCategoriserTest,
  postCategoriserRetry,
  postReset,
  transactionsCsvUrl,
} from './api.js';

/**
 * Notification types (backend keys) paired with human-friendly labels. Order
 * here is the render order of the toggle list.
 */
const NOTIFICATION_TYPES = [
  ['processed', 'Statement processed'],
  ['processed_recovered', 'Statement processed after recovery'],
  ['categorisation_failed', 'Sorting delayed'],
  ['categorisation_recovered', 'Sorting caught up'],
  ['parse_error', 'Could not read a file'],
  ['drive_backup_failed', 'Drive backup failed'],
  ['duplicate_noop', 'Duplicate upload skipped'],
  ['generic_error', 'Something went wrong'],
  ['monthly_reminder', 'Monthly upload reminder'],
];

const RESET_CONFIRM = 'RESET';

/**
 * Wire the Settings view.
 *
 * Expected elements in `root` (all optional — the controller no-ops on any
 * that are absent, so partial DOM fragments in tests still work):
 *   #settings-notifications          container for the per-type toggle list
 *   #settings-notifications-status   inline status/error line
 *   #settings-backup-link            <a> download-backup anchor
 *   #settings-reset-input            text input (type RESET to arm)
 *   #settings-reset-btn              danger button
 *   #settings-reset-status           inline status/error line
 *   #settings-categoriser-summary    configured / uncategorised summary
 *   #settings-categoriser-test       Test OpenRouter button
 *   #settings-categoriser-test-status
 *   #settings-categoriser-retry      Retry uncategorised button
 *   #settings-categoriser-retry-status
 *   #settings-corrections-toggle     opt-in checkbox
 *   #settings-corrections-list       container for the corrections list
 *   #settings-corrections-status     inline status/error line
 *
 * @param {{
 *   root?: Document,
 *   api?: object,   // inject fakes in tests; missing members fall back to real api.js
 * }} options
 * @returns {{ load(): Promise<void>, destroy(): void }}
 */
export function createSettings({ root = document, api } = {}) {
  const _api = {
    getSettings,
    putSettings,
    getCorrections,
    deleteCorrection,
    getCategoriserStatus,
    postCategoriserTest,
    postCategoriserRetry,
    postReset,
    transactionsCsvUrl,
    ...(api ?? {}),
  };

  const doc = root.documentElement ? root : root.ownerDocument ?? document;

  const notifWrap = root.getElementById('settings-notifications');
  const notifStatus = root.getElementById('settings-notifications-status');

  const backupLink = root.getElementById('settings-backup-link');
  const resetInput = root.getElementById('settings-reset-input');
  const resetBtn = root.getElementById('settings-reset-btn');
  const resetStatus = root.getElementById('settings-reset-status');

  const catSummary = root.getElementById('settings-categoriser-summary');
  const catTestBtn = root.getElementById('settings-categoriser-test');
  const catTestStatus = root.getElementById('settings-categoriser-test-status');
  const catRetryBtn = root.getElementById('settings-categoriser-retry');
  const catRetryStatus = root.getElementById('settings-categoriser-retry-status');

  const corrToggle = root.getElementById('settings-corrections-toggle');
  const corrList = root.getElementById('settings-corrections-list');
  const corrStatus = root.getElementById('settings-corrections-status');

  const _listeners = [];
  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

  function _setStatus(el, msg, isError = false) {
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('settings-status--error', isError);
  }

  // --- Notifications --------------------------------------------------------

  function _renderNotifications(notifications) {
    if (!notifWrap) return;
    notifWrap.textContent = '';
    const values = notifications ?? {};

    for (const [type, label] of NOTIFICATION_TYPES) {
      const row = doc.createElement('div');
      row.className = 'settings-toggle-row';

      const text = doc.createElement('span');
      text.className = 'settings-toggle-label';
      text.textContent = label;

      // The switch is its own <label> wrapping the input (matches the working
      // fuel-card toggle). Do NOT also wrap the row in a <label>, or a click
      // fires the change twice (toggle on, then the outer label toggles it back).
      const sw = doc.createElement('label');
      sw.className = 'fuel-switch';

      const input = doc.createElement('input');
      input.type = 'checkbox';
      input.className = 'settings-notif-toggle';
      input.dataset.type = type;
      input.checked = Boolean(values[type]);
      input.setAttribute('aria-label', label);

      const track = doc.createElement('span');
      track.className = 'fuel-switch-track';
      const knob = doc.createElement('span');
      knob.className = 'knob';
      track.appendChild(knob);

      sw.appendChild(input);
      sw.appendChild(track);

      input.addEventListener('change', async () => {
        _setStatus(notifStatus, '');
        try {
          await _api.putSettings({ notifications: { [type]: input.checked } });
          _setStatus(notifStatus, 'Saved.');
        } catch {
          input.checked = !input.checked; // revert the optimistic flip
          _setStatus(notifStatus, 'Could not save that preference.', true);
        }
      });

      row.appendChild(text);
      row.appendChild(sw);
      notifWrap.appendChild(row);
    }
  }

  // --- Categoriser health ---------------------------------------------------

  function _renderCategoriserSummary(status) {
    if (!catSummary) return;
    if (!status) {
      catSummary.textContent = 'Could not load categoriser status.';
      return;
    }
    const configured = status.configured ? 'yes' : 'no';
    const count = Number(status.uncategorised_count ?? 0);
    catSummary.textContent = `Configured: ${configured} · Uncategorised transactions: ${count}`;
  }

  async function _handleCategoriserTest() {
    _setStatus(catTestStatus, 'Testing...');
    try {
      const r = await _api.postCategoriserTest();
      let msg;
      if (!r.configured) {
        msg = 'Not configured';
      } else if (r.rate_limited) {
        msg = 'Rate limited (free-tier throttling)';
      } else if (r.reachable) {
        msg = 'Reachable';
      } else {
        msg = 'Not reachable';
      }
      _setStatus(catTestStatus, msg);
    } catch {
      _setStatus(catTestStatus, 'Could not run the test.', true);
    }
  }

  async function _handleCategoriserRetry() {
    _setStatus(catRetryStatus, 'Retrying...');
    try {
      const r = await _api.postCategoriserRetry();
      if (r.ok) {
        const sorted = Number(r.categorised ?? 0);
        const remaining = Number(r.remaining ?? 0);
        _setStatus(catRetryStatus, `Sorted ${sorted}, ${remaining} remaining`);
      } else {
        _setStatus(catRetryStatus, r.detail || 'Retry did not complete.', true);
      }
      // Refresh the summary line so the uncategorised count reflects the retry.
      _api
        .getCategoriserStatus()
        .then((s) => _renderCategoriserSummary(s))
        .catch(() => {});
    } catch {
      _setStatus(catRetryStatus, 'Could not run the retry.', true);
    }
  }

  // --- Learned corrections --------------------------------------------------

  function _renderCorrections(corrections) {
    if (!corrList) return;
    corrList.textContent = '';
    const items = corrections ?? [];

    if (items.length === 0) {
      const empty = doc.createElement('p');
      empty.className = 'settings-corrections-empty';
      empty.textContent = 'No learned corrections yet.';
      corrList.appendChild(empty);
      return;
    }

    for (const c of items) {
      const row = doc.createElement('div');
      row.className = 'settings-correction-row';

      const meta = doc.createElement('span');
      meta.className = 'settings-correction-meta';
      const desc = doc.createElement('span');
      desc.className = 'settings-correction-desc';
      desc.textContent = c.cleaned_description;
      const arrow = doc.createElement('span');
      arrow.className = 'settings-correction-arrow';
      arrow.textContent = ' → ';
      const cat = doc.createElement('span');
      cat.className = 'settings-correction-cat';
      cat.textContent = c.category;
      meta.appendChild(desc);
      meta.appendChild(arrow);
      meta.appendChild(cat);

      const remove = doc.createElement('button');
      remove.type = 'button';
      remove.className = 'settings-correction-remove';
      remove.textContent = 'Remove';
      remove.setAttribute('aria-label', `Remove correction for ${c.cleaned_description}`);
      remove.addEventListener('click', async () => {
        remove.disabled = true;
        _setStatus(corrStatus, '');
        try {
          await _api.deleteCorrection(c.id);
          await _loadCorrections();
        } catch {
          remove.disabled = false;
          _setStatus(corrStatus, 'Could not remove that correction.', true);
        }
      });

      row.appendChild(meta);
      row.appendChild(remove);
      corrList.appendChild(row);
    }
  }

  async function _loadCorrections() {
    try {
      const data = await _api.getCorrections();
      if (corrToggle) corrToggle.checked = Boolean(data.enabled);
      _renderCorrections(data.corrections ?? []);
    } catch {
      _renderCorrections([]);
      _setStatus(corrStatus, 'Could not load learned corrections.', true);
    }
  }

  // --- Data & backup (reset danger zone) ------------------------------------

  function _syncResetButton() {
    if (!resetBtn) return;
    const armed = resetInput ? resetInput.value.trim() === RESET_CONFIRM : false;
    resetBtn.disabled = !armed;
  }

  async function _handleReset() {
    if (resetInput && resetInput.value.trim() !== RESET_CONFIRM) return;
    if (resetBtn) resetBtn.disabled = true;
    _setStatus(resetStatus, 'Resetting...');
    try {
      const r = await _api.postReset(RESET_CONFIRM);
      if (r && r.ok) {
        _setStatus(resetStatus, 'All data cleared. Please refresh the page.');
        if (resetInput) resetInput.value = '';
      } else {
        _setStatus(resetStatus, 'Reset did not complete.', true);
      }
    } catch {
      _setStatus(resetStatus, 'Could not reset. Nothing was changed.', true);
    } finally {
      _syncResetButton();
    }
  }

  // --- One-time wiring (static controls) ------------------------------------

  if (backupLink) {
    try {
      backupLink.href = _api.transactionsCsvUrl();
      backupLink.setAttribute('download', 'transactions.csv');
    } catch {
      // leave the anchor as-is if the URL helper somehow fails
    }
  }

  if (resetBtn) resetBtn.disabled = true;
  _on(resetInput, 'input', _syncResetButton);
  _on(resetBtn, 'click', _handleReset);

  _on(catTestBtn, 'click', _handleCategoriserTest);
  _on(catRetryBtn, 'click', _handleCategoriserRetry);

  _on(corrToggle, 'change', async () => {
    _setStatus(corrStatus, '');
    try {
      await _api.putSettings({ corrections_enabled: corrToggle.checked });
    } catch {
      corrToggle.checked = !corrToggle.checked; // revert
      _setStatus(corrStatus, 'Could not save that preference.', true);
    }
  });

  // --- load() — best-effort; never throws -----------------------------------

  async function load() {
    _setStatus(notifStatus, '');
    _setStatus(corrStatus, '');
    _setStatus(resetStatus, '');
    _setStatus(catTestStatus, '');
    _setStatus(catRetryStatus, '');

    // Settings (notification toggles + corrections opt-in).
    try {
      const settings = await _api.getSettings();
      _renderNotifications(settings.notifications ?? {});
      if (corrToggle) corrToggle.checked = Boolean(settings.corrections_enabled);
    } catch {
      _renderNotifications({});
      _setStatus(notifStatus, 'Could not load notification settings.', true);
    }

    // Categoriser health.
    try {
      const status = await _api.getCategoriserStatus();
      _renderCategoriserSummary(status);
    } catch {
      _renderCategoriserSummary(null);
    }

    // Learned corrections list.
    await _loadCorrections();

    _syncResetButton();
  }

  function destroy() {
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
  }

  return { load, destroy };
}
