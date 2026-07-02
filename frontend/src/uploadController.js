/**
 * uploadController.js — DOM wiring for the upload section.
 * PRIVACY: Only Blob/File handles are held in component state. No file
 * contents are read, parsed, inspected, or logged here. Files are forwarded
 * only to the owner's own backend at ${API_BASE}/upload via postFn.
 * User-facing status messages NEVER include CSV contents, amounts, or names.
 */

import { buildUploadForm, postUpload, isCsvFile, UploadValidationError } from './upload.js';
import { ApiError } from './api.js';

const AUTO_SWITCH_DELAY_MS = 1500;

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

  // Transient Blob handles only — never read or inspect the bytes.
  const selected = { commbank: null, westpac: null };

  // --- DOM references -------------------------------------------------------
  const statusEl = root.getElementById('upload-status');
  const submitBtn = root.getElementById('upload-submit');

  const BANKS = ['commbank', 'westpac'];
  const zones = {};
  for (const bank of BANKS) {
    zones[bank] = {
      zone: root.getElementById(`dropzone-${bank}`),
      input: root.getElementById(`file-${bank}`),
      label: root.getElementById(`filename-${bank}`),
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

  function _handleFile(bank, file) {
    if (!file) return;
    if (!isCsvFile(file)) {
      _setStatus('Only .csv files are accepted.', true);
      return;
    }
    _setSlot(bank, file);
    _setStatus('');
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
      _setSlot('commbank', null);
      _setSlot('westpac', null);
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
          _setSlot('commbank', null);
          _setSlot('westpac', null);
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
