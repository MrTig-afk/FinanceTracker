/**
 * uploadController.js — DOM wiring for the upload section.
 * PRIVACY: CSV bytes are read/parsed LOCALLY only to render the on-screen
 * preview in the owner's own client (never sent, never logged). Files
 * themselves are forwarded only to the owner's own backend at ${API_BASE}/upload
 * via postFn. User-facing status messages NEVER include file contents, amounts,
 * or names.
 */

import {
  buildUploadForm,
  postUpload,
  isCsvFile,
  isXlsxFile,
  isAcceptedUploadFile,
  parseCsvPreview,
  UploadValidationError,
} from './upload.js';
import { ApiError } from './api.js';

const AUTO_SWITCH_DELAY_MS = 1500;
const PREVIEW_ROW_LIMIT = 6;
const BANK_LABELS = { commbank: 'CommBank', westpac: 'Westpac' };

/**
 * Wire the upload UI region to the queue and dashboard refresh callback.
 *
 * Requires the following elements to be present in `root`:
 *   #dropzone-commbank, #file-commbank, #filename-commbank
 *   #dropzone-westpac,  #file-westpac,  #filename-westpac
 *   #upload-submit
 *   #upload-status  (role="status", aria-live="polite")
 *
 * @param {{
 *   root?: Document,
 *   queue: object,           // createQueue() instance
 *   onUploaded?: () => Promise<void>,  // called after a successful upload
 *   onUploadSuccess?: () => void,  // called ~1.5s after a successful upload to
 *                                  // switch to the Overview view.
 *   postFn?: (form: FormData) => Promise<object>,  // injectable for tests
 * }} options
 * @returns {{ destroy(): void }}
 */
export function createUploadController({
  root = document,
  queue,
  onUploaded,
  onUploadSuccess,
  postFn,
} = {}) {
  const _postFn = postFn ?? ((form) => postUpload(form));

  // Transient Blob handles only — never read or inspect the bytes for network.
  const selected = { commbank: null, westpac: null };
  // Locally-parsed preview state per bank: { kind: 'csv'|'xlsx', rows, size }.
  // Rows are held ONLY to render the on-screen preview and are never sent.
  const preview = { commbank: null, westpac: null };

  // --- DOM references -------------------------------------------------------
  const statusEl = root.getElementById('upload-status');
  const submitBtn = root.getElementById('upload-submit');
  const clearBtn = root.getElementById('upload-clear');

  // Preview panel (optional — controller degrades gracefully if absent).
  const previewCard = root.getElementById('preview-card');
  const previewTabsEl = root.getElementById('preview-tabs');
  const previewMetaEl = root.getElementById('preview-meta');
  const previewBodyEl = root.getElementById('preview-body');
  const previewNoteEl = root.getElementById('preview-note');
  const previewTableWrap = previewBodyEl
    ? previewBodyEl.closest('.preview-table-wrap')
    : null;
  let activeTab = 'westpac';

  const BANKS = ['commbank', 'westpac'];
  const zones = {};
  for (const bank of BANKS) {
    const zone = root.getElementById(`dropzone-${bank}`);
    zones[bank] = {
      zone,
      input: root.getElementById(`file-${bank}`),
      label: root.getElementById(`filename-${bank}`),
      empty: zone ? zone.querySelector(`[data-bank-empty="${bank}"]`) : null,
      loaded: zone ? zone.querySelector(`[data-bank-loaded="${bank}"]`) : null,
      ico: zone ? zone.querySelector(`[data-bank-ico="${bank}"]`) : null,
      info: zone ? zone.querySelector(`[data-bank-info="${bank}"]`) : null,
    };
  }

  // --- Listener registry (for cleanup) -------------------------------------
  const _listeners = [];
  let _switchTimer = null;
  let _submitting = false;

  function _on(el, event, handler) {
    if (!el) return;
    el.addEventListener(event, handler);
    _listeners.push({ el, event, handler });
  }

  // --- Status display -------------------------------------------------------

  function _setStatus(msg, isError = false) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.classList.toggle('upload-status--error', isError);
  }

  // Toggle the in-flight processing state: disable the button (so a slow
  // categorisation can't be double-submitted) and show an animated spinner in
  // the status line. Called immediately on click — before awaiting the upload —
  // so the user gets instant feedback while the backend runs the analyser.
  function _setProcessing(on) {
    _submitting = on;
    if (submitBtn) submitBtn.disabled = on;
    if (statusEl) statusEl.classList.toggle('upload-status--processing', on);
  }

  // --- Slot management ------------------------------------------------------

  function _setSlot(bank, file) {
    selected[bank] = file;
    const label = zones[bank]?.label;
    if (label) {
      label.textContent = file ? (file.name ?? 'file selected') : '';
    }
  }

  function _formatKb(size) {
    const bytes = typeof size === 'number' && size > 0 ? size : 0;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  // Flip a zone into its loaded (accent-tinted) state and fill the file chip.
  function _showLoadedState(bank, file, kind, rows) {
    const z = zones[bank];
    if (!z) return;
    if (z.zone) z.zone.classList.add('is-loaded');
    if (z.empty) z.empty.hidden = true;
    if (z.loaded) z.loaded.hidden = false;
    if (z.ico) z.ico.textContent = kind === 'xlsx' ? 'XLSX' : 'CSV';
    if (z.info) {
      const size = _formatKb(file.size);
      z.info.textContent =
        kind === 'csv' && Array.isArray(rows)
          ? `${rows.length} rows detected - ${size}`
          : `XLSX file - ${size}`;
    }
  }

  // Reset a zone back to its empty state and drop its selection + preview.
  function _resetZone(bank) {
    _setSlot(bank, null);
    preview[bank] = null;
    const z = zones[bank];
    if (!z) return;
    if (z.zone) z.zone.classList.remove('is-loaded');
    if (z.empty) z.empty.hidden = false;
    if (z.loaded) z.loaded.hidden = true;
    if (z.info) z.info.textContent = '';
  }

  // Read a File/Blob's text without inspecting it for anything but the local
  // preview. Uses the native .text() where available, else FileReader.
  function _readText(file) {
    if (typeof file.text === 'function') return file.text();
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result ?? ''));
      fr.onerror = () => reject(fr.error);
      fr.readAsText(file);
    });
  }

  async function _handleFile(bank, file) {
    if (!file) return;
    if (!isAcceptedUploadFile(file)) {
      _setStatus('Only .csv or .xlsx files are accepted.', true);
      return;
    }

    _setSlot(bank, file);
    // Set the staged status line SYNCHRONOUSLY (bank-name only — never file
    // contents). Doing it here rather than after the async read below means a
    // slow/late preview parse can never clobber a subsequent Processing / error
    // / success status set by the submit flow.
    _renderStagedStatus();

    const kind = isXlsxFile(file) && !isCsvFile(file) ? 'xlsx' : 'csv';

    if (kind === 'xlsx') {
      preview[bank] = { kind, rows: null, size: file.size };
      _showLoadedState(bank, file, kind, null);
      _showPreview(bank);
      return;
    }

    // CSV: read + parse locally for the on-screen preview. Best-effort.
    let rows = null;
    try {
      rows = parseCsvPreview(await _readText(file), { bank });
    } catch {
      rows = null;
    }
    // The read is async: if the slot was cleared or replaced while we were
    // reading (e.g. the upload already succeeded and reset the zones), this
    // result is stale — drop it rather than clobber the current UI state.
    if (selected[bank] !== file) return;

    preview[bank] = { kind, rows, size: file.size };
    _showLoadedState(bank, file, kind, rows);
    _showPreview(bank);
  }

  // --- Staged status line ---------------------------------------------------

  function _renderStagedStatus() {
    const staged = BANKS.filter((b) => selected[b]);
    if (staged.length === 0) {
      _setStatus('');
      return;
    }
    if (staged.length === BANKS.length) {
      _setStatus('2 files staged.');
      return;
    }
    const other = BANKS.find((b) => !selected[b]);
    _setStatus(`${BANK_LABELS[staged[0]]} staged - ${BANK_LABELS[other]} still empty.`);
  }

  // --- Preview panel --------------------------------------------------------

  function _fmtAmount(n) {
    const value = typeof n === 'number' && Number.isFinite(n) ? n : 0;
    const sign = value < 0 ? '-' : '+';
    const abs = Math.abs(value).toLocaleString('en-AU', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return `${sign}${abs}`;
  }

  function _renderPreviewRows(rows) {
    if (!previewBodyEl) return;
    previewBodyEl.textContent = '';
    for (const row of rows.slice(0, PREVIEW_ROW_LIMIT)) {
      const tr = root.createElement('tr');

      const dateTd = root.createElement('td');
      dateTd.className = 'tabular';
      dateTd.textContent = row.date ?? '';

      const descTd = root.createElement('td');
      descTd.className = 'desc';
      descTd.textContent = row.description ?? '';

      const amtTd = root.createElement('td');
      const negative = typeof row.amount === 'number' && row.amount < 0;
      amtTd.className = `num tabular ${negative ? 'amt-neg' : 'amt-pos'}`;
      amtTd.textContent = _fmtAmount(row.amount);

      tr.append(dateTd, descTd, amtTd);
      previewBodyEl.append(tr);
    }
  }

  function _renderPreview() {
    // Highlight the active tab.
    if (previewTabsEl) {
      for (const tab of previewTabsEl.querySelectorAll('.ptab')) {
        tab.classList.toggle('active', tab.dataset.bank === activeTab);
      }
    }

    const p = preview[activeTab];
    const label = BANK_LABELS[activeTab] ?? activeTab;

    const showTable = (on) => {
      if (previewTableWrap) previewTableWrap.hidden = !on;
    };
    const setNote = (text) => {
      if (!previewNoteEl) return;
      previewNoteEl.textContent = text ?? '';
      previewNoteEl.hidden = !text;
    };

    if (!p) {
      if (previewBodyEl) previewBodyEl.textContent = '';
      if (previewMetaEl) previewMetaEl.textContent = '';
      showTable(false);
      setNote(`No ${label} file staged.`);
      return;
    }

    if (p.kind === 'xlsx') {
      if (previewBodyEl) previewBodyEl.textContent = '';
      if (previewMetaEl) previewMetaEl.textContent = '';
      showTable(false);
      setNote('XLSX preview available after upload.');
      return;
    }

    const rows = Array.isArray(p.rows) ? p.rows : [];
    _renderPreviewRows(rows);
    const shown = Math.min(PREVIEW_ROW_LIMIT, rows.length);
    if (previewMetaEl) {
      previewMetaEl.textContent = `Showing ${shown} of ${rows.length} parsed rows`;
    }
    showTable(true);
    setNote('');
  }

  function _showPreview(bank) {
    if (previewCard) previewCard.hidden = false;
    if (bank) activeTab = bank;
    _renderPreview();
  }

  function _hidePreview() {
    if (previewCard) previewCard.hidden = true;
  }

  // --- Wire each drop zone and file input -----------------------------------

  for (const bank of BANKS) {
    const { zone, input } = zones[bank];

    if (zone) {
      _on(zone, 'dragover', (e) => {
        e.preventDefault();
        zone.classList.add('dragover');
      });

      _on(zone, 'dragleave', () => {
        zone.classList.remove('dragover');
      });

      _on(zone, 'drop', (e) => {
        e.preventDefault();
        zone.classList.remove('dragover');
        // Take only the first dropped file.
        const file = e.dataTransfer?.files?.[0] ?? null;
        _handleFile(bank, file);
      });

      // Click the zone to open the file picker programmatically.
      _on(zone, 'click', () => {
        if (input) input.click();
      });
    }

    if (input) {
      _on(input, 'change', () => {
        const file = input.files?.[0] ?? null;
        _handleFile(bank, file);
        // Reset the input so the same file can be re-selected if needed.
        input.value = '';
      });
    }
  }

  // --- Preview tabs ---------------------------------------------------------

  if (previewTabsEl) {
    for (const tab of previewTabsEl.querySelectorAll('.ptab')) {
      _on(tab, 'click', () => {
        activeTab = tab.dataset.bank;
        _renderPreview();
      });
    }
  }

  // --- Clear button ---------------------------------------------------------

  function _clearAll() {
    for (const bank of BANKS) _resetZone(bank);
    _hidePreview();
    _setStatus('');
  }

  _on(clearBtn, 'click', _clearAll);

  // --- Submit handler -------------------------------------------------------

  async function _handleSubmit() {
    // Guard: ignore repeat clicks while an upload is already in flight. The
    // backend runs the analyser synchronously, so the request can take several
    // seconds — without this, an impatient double-click would fire twice.
    if (_submitting) return;

    // Capture current selection before any clearing.
    const files = {};
    if (selected.commbank) files.commbank = selected.commbank;
    if (selected.westpac) files.westpac = selected.westpac;

    // Validate — UploadValidationError has a safe message.
    let form;
    try {
      form = buildUploadForm(files);
    } catch (err) {
      if (err instanceof UploadValidationError) {
        _setStatus(err.message, true);
      } else {
        _setStatus('Validation failed.', true);
      }
      return;
    }

    // Immediate feedback: lock the button and show the spinner BEFORE awaiting,
    // so the click is never a dead no-op while the backend categorises.
    _setProcessing(true);
    _setStatus('Processing your statements. This can take a few seconds.');

    try {
      await _postFn(form);

      // Success — clear the in-flight state, selection, and refresh the dashboard.
      _setProcessing(false);
      _resetZone('commbank');
      _resetZone('westpac');
      _hidePreview();
      _setStatus('Uploaded. Opening your overview.');
      if (onUploaded) await onUploaded();

      if (onUploadSuccess) {
        const uploadSection = root.querySelector('section.view[data-view="upload"]');
        _switchTimer = setTimeout(() => {
          _switchTimer = null;
          // Nice-to-have guard: only jump if the user is still on the Upload view.
          if (!uploadSection || !uploadSection.hidden) onUploadSuccess();
        }, AUTO_SWITCH_DELAY_MS);
      }
    } catch (err) {
      // Clear the in-flight state first, whatever the failure — re-enables the
      // button so the user can retry.
      _setProcessing(false);
      if (err instanceof ApiError) {
        if (err.status === null && err.cause != null) {
          // Network-level error: backend unreachable — queue for later retry.
          // FR-4: the item must survive a page reload (IndexedDB persistence).
          await queue.enqueue(files);
          _resetZone('commbank');
          _resetZone('westpac');
          _hidePreview();
          _setStatus('Backend unreachable, queued and will retry.');
        } else {
          // Server returned an explicit error (4xx / 5xx).
          // Do NOT queue — the backend actively rejected this request;
          // re-queuing would loop forever. Show a safe message only.
          _setStatus(
            `Upload failed (status ${err.status ?? 'unknown'}).`,
            true,
          );
        }
      } else {
        _setStatus('Upload failed.', true);
      }
    }
  }

  _on(submitBtn, 'click', _handleSubmit);

  // --- Destroy (remove all listeners) ---------------------------------------

  function destroy() {
    for (const { el, event, handler } of _listeners) {
      el.removeEventListener(event, handler);
    }
    _listeners.length = 0;
    if (_switchTimer !== null) {
      clearTimeout(_switchTimer);
      _switchTimer = null;
    }
  }

  return { destroy };
}
