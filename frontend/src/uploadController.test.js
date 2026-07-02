/**
 * uploadController.test.js — DOM wiring tests for uploadController.js.
 * jsdom provides the DOM. No real network — postFn is injected.
 * No real IndexedDB — createMemoryStore() is injected into the queue.
 * All fixtures are synthetic, built inline. No real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createUploadController } from './uploadController.js';
import { createQueue, createMemoryStore } from './queue.js';
import { ApiError } from './api.js';

// ---------------------------------------------------------------------------
// Synthetic CSV fixture — no real transaction data.
// ---------------------------------------------------------------------------

const SYNTH_CSV = 'date,amount,desc\n01-06-2026,-5.00,SYNTH\n';

// Mirrors uploadController.js's AUTO_SWITCH_DELAY_MS (spec: ~1.5s after a
// successful upload). Kept as a literal here so the test asserts the
// documented behaviour rather than importing an internal, unexported constant.
const AUTO_SWITCH_WAIT = 1500;

function csvFile(name = 'commbank.csv') {
  return new File([SYNTH_CSV], name, { type: 'text/csv' });
}

// ---------------------------------------------------------------------------
// Minimal upload-section HTML (mirrors the contract in index.html).
// ---------------------------------------------------------------------------

function zoneHtml(bank) {
  return `
    <div id="dropzone-${bank}" class="zone" tabindex="0" role="button">
      <div class="zone-body">
        <div class="zone-empty" data-bank-empty="${bank}">
          <span class="zone-hint">Drop file or click to browse</span>
        </div>
        <div class="zone-loaded" data-bank-loaded="${bank}" hidden>
          <div class="zone-file">
            <span class="zone-file-ico" data-bank-ico="${bank}">CSV</span>
            <span id="filename-${bank}" class="zone-file-name filename"></span>
            <span class="zone-file-info tabular" data-bank-info="${bank}"></span>
          </div>
        </div>
      </div>
      <input id="file-${bank}" type="file" accept=".csv,.xlsx" />
    </div>
  `;
}

const UPLOAD_HTML = `
  <section class="view" data-view="upload">
    <section id="upload-card" class="card upload">
      <div class="upload-zones">
        ${zoneHtml('commbank')}
        ${zoneHtml('westpac')}
      </div>
      <div class="upload-actions">
        <button id="upload-submit" type="button">Upload and categorise</button>
        <button id="upload-clear" type="button">Clear</button>
        <p id="upload-status" role="status" aria-live="polite"></p>
      </div>
    </section>

    <section id="preview-card" class="card preview-card" hidden>
      <div class="preview-head">
        <span class="preview-title">Preview</span>
        <div id="preview-tabs" class="preview-tabs">
          <button type="button" class="ptab" data-bank="westpac">Westpac</button>
          <button type="button" class="ptab" data-bank="commbank">CommBank</button>
        </div>
        <span id="preview-meta" class="preview-meta tabular"></span>
      </div>
      <div class="preview-table-wrap">
        <table class="preview-table">
          <thead>
            <tr><th>Date</th><th>Description</th><th class="num">Amount</th></tr>
          </thead>
          <tbody id="preview-body"></tbody>
        </table>
      </div>
      <p id="preview-note" class="preview-note" hidden></p>
    </section>
  </section>
`;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueue() {
  return createQueue({
    store: createMemoryStore(),
    idFn: (() => { let n = 0; return () => `q-${++n}`; })(),
    now: () => 1_000_000,
  });
}

function getStatus() {
  return document.getElementById('upload-status').textContent;
}

function getFilenameLabel(bank) {
  return document.getElementById(`filename-${bank}`).textContent;
}

function clickSubmit() {
  document.getElementById('upload-submit').click();
}

/** Simulate the file-input change event after assigning a file to input.files. */
function simulateFileSelect(inputId, file) {
  const input = document.getElementById(inputId);
  // jsdom does not allow direct assignment of files, so we override the getter.
  Object.defineProperty(input, 'files', {
    value: { 0: file, length: 1, item: (i) => (i === 0 ? file : null) },
    configurable: true,
  });
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

let controller;
let queue;

beforeEach(() => {
  document.body.innerHTML = UPLOAD_HTML;
  queue = makeQueue();
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Zero-files submitted → validation error, no network call
// ---------------------------------------------------------------------------

describe('submit with no files selected', () => {
  it('shows the UploadValidationError message in #upload-status', async () => {
    const postFn = vi.fn();
    controller = createUploadController({ root: document, queue, postFn });

    clickSubmit();
    await Promise.resolve(); // flush microtasks

    expect(getStatus()).toContain('CSV');
    expect(postFn).not.toHaveBeenCalled();
  });

  it('does not call postFn when no files are selected', async () => {
    const postFn = vi.fn();
    controller = createUploadController({ root: document, queue, postFn });

    clickSubmit();
    await Promise.resolve();

    expect(postFn).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Non-CSV file selected → slot not set, status shows rejection
// ---------------------------------------------------------------------------

describe('non-CSV file dropped/selected', () => {
  it('shows a rejection message when a .txt file is selected', async () => {
    const postFn = vi.fn();
    controller = createUploadController({ root: document, queue, postFn });

    const txtFile = new File([SYNTH_CSV], 'notes.txt', { type: 'text/plain' });
    simulateFileSelect('file-commbank', txtFile);

    expect(getStatus()).toContain('.csv');
  });

  it('does not set the filename label when a non-CSV file is selected', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    const txtFile = new File([SYNTH_CSV], 'notes.txt', { type: 'text/plain' });
    simulateFileSelect('file-commbank', txtFile);

    // The slot must not have been set; filename label remains empty.
    expect(getFilenameLabel('commbank')).toBe('');
  });
});

// ---------------------------------------------------------------------------
// postFn resolves → success path
// ---------------------------------------------------------------------------

describe('submit — postFn resolves', () => {
  it('shows a success message in #upload-status', async () => {
    const postFn = vi.fn().mockResolvedValue({ processed: 1 });
    const onUploaded = vi.fn().mockResolvedValue(undefined);
    controller = createUploadController({ root: document, queue, postFn, onUploaded });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(getStatus()).toContain('Uploaded');
  });

  it('shows a processing state (disabled button + spinner) while in flight, and clears it on success', async () => {
    let resolveUpload;
    const postFn = vi.fn(() => new Promise((res) => { resolveUpload = res; }));
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();

    // Synchronously after the click, before the upload resolves.
    const btn = document.getElementById('upload-submit');
    const status = document.getElementById('upload-status');
    expect(btn.disabled).toBe(true);
    expect(status.classList.contains('upload-status--processing')).toBe(true);
    expect(getStatus()).toContain('Processing');

    resolveUpload({});
    await new Promise((r) => setTimeout(r, 20));

    // Cleared on success.
    expect(btn.disabled).toBe(false);
    expect(status.classList.contains('upload-status--processing')).toBe(false);
    expect(getStatus()).toContain('Uploaded');
  });

  it('ignores a second submit while an upload is already in flight', async () => {
    let resolveUpload;
    const postFn = vi.fn(() => new Promise((res) => { resolveUpload = res; }));
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    clickSubmit(); // second click while in flight must not fire a second request

    expect(postFn).toHaveBeenCalledOnce();

    resolveUpload({});
    await new Promise((r) => setTimeout(r, 20));
  });

  it('re-enables the button and clears the spinner when the upload fails', async () => {
    const serverErr = new ApiError('upload failed', { status: 500, cause: null });
    const postFn = vi.fn().mockRejectedValue(serverErr);
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    const btn = document.getElementById('upload-submit');
    const status = document.getElementById('upload-status');
    expect(btn.disabled).toBe(false);
    expect(status.classList.contains('upload-status--processing')).toBe(false);
  });

  it('calls onUploaded exactly once after a successful upload', async () => {
    const postFn = vi.fn().mockResolvedValue({ processed: 1 });
    const onUploaded = vi.fn().mockResolvedValue(undefined);
    controller = createUploadController({ root: document, queue, postFn, onUploaded });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(onUploaded).toHaveBeenCalledOnce();
  });

  it('clears the commbank filename label after success', async () => {
    const postFn = vi.fn().mockResolvedValue({});
    const onUploaded = vi.fn().mockResolvedValue(undefined);
    controller = createUploadController({ root: document, queue, postFn, onUploaded });

    simulateFileSelect('file-commbank', csvFile());
    // Confirm label was set before submit.
    expect(getFilenameLabel('commbank')).not.toBe('');

    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(getFilenameLabel('commbank')).toBe('');
  });

  it('does NOT call queue.enqueue on success', async () => {
    const postFn = vi.fn().mockResolvedValue({});
    const enqueueSpy = vi.spyOn(queue, 'enqueue');
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(enqueueSpy).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// postFn rejects with a NETWORK ApiError (no status, has cause) → enqueue
// ---------------------------------------------------------------------------

describe('submit — network ApiError (backend unreachable)', () => {
  it('calls queue.enqueue and shows "queued" message', async () => {
    const networkErr = new ApiError('network error', {
      status: null,
      cause: new TypeError('Failed to fetch'),
    });
    const postFn = vi.fn().mockRejectedValue(networkErr);
    const enqueueSpy = vi.spyOn(queue, 'enqueue');
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(enqueueSpy).toHaveBeenCalledOnce();
    expect(getStatus()).toContain('queued');
  });

  it('does NOT call onUploaded when the item is queued (network failure)', async () => {
    const networkErr = new ApiError('network error', {
      status: null,
      cause: new TypeError('Failed to fetch'),
    });
    const postFn = vi.fn().mockRejectedValue(networkErr);
    const onUploaded = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploaded });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(onUploaded).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// postFn rejects with a SERVER ApiError (has status) → NOT queued
// ---------------------------------------------------------------------------

describe('submit — server ApiError (4xx/5xx, backend reachable)', () => {
  it('does NOT call queue.enqueue when the server returns an error status', async () => {
    const serverErr = new ApiError('upload failed', { status: 500, cause: null });
    const postFn = vi.fn().mockRejectedValue(serverErr);
    const enqueueSpy = vi.spyOn(queue, 'enqueue');
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(enqueueSpy).not.toHaveBeenCalled();
  });

  it('shows a safe error message that includes the status code', async () => {
    const serverErr = new ApiError('upload failed', { status: 422, cause: null });
    const postFn = vi.fn().mockRejectedValue(serverErr);
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await new Promise((r) => setTimeout(r, 20));

    expect(getStatus()).toContain('422');
  });
});

// ---------------------------------------------------------------------------
// Change 3 — auto-jump to Overview ~1.5s after a successful upload.
// Uses fake timers. postFn/onUploaded resolve/reject via real Promises
// (fake timers do not affect microtask scheduling), so we flush pending
// microtasks with vi.advanceTimersByTimeAsync(0) before asserting.
// ---------------------------------------------------------------------------

describe('onUploadSuccess — auto-switch after AUTO_SWITCH_DELAY_MS', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls onUploadSuccess exactly once, ~1500ms after a successful upload', async () => {
    const postFn = vi.fn().mockResolvedValue({ processed: 1 });
    const onUploadSuccess = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploadSuccess });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await vi.advanceTimersByTimeAsync(0); // flush the postFn microtask chain

    expect(onUploadSuccess).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1499);
    expect(onUploadSuccess).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(onUploadSuccess).toHaveBeenCalledOnce();
  });

  it('does NOT call onUploadSuccess when postFn rejects with a server ApiError', async () => {
    const serverErr = new ApiError('upload failed', { status: 500, cause: null });
    const postFn = vi.fn().mockRejectedValue(serverErr);
    const onUploadSuccess = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploadSuccess });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(AUTO_SWITCH_WAIT);

    expect(onUploadSuccess).not.toHaveBeenCalled();
  });

  it('does NOT call onUploadSuccess when the upload is queued (network ApiError)', async () => {
    const networkErr = new ApiError('network error', {
      status: null,
      cause: new TypeError('Failed to fetch'),
    });
    const postFn = vi.fn().mockRejectedValue(networkErr);
    const onUploadSuccess = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploadSuccess });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(AUTO_SWITCH_WAIT);

    expect(onUploadSuccess).not.toHaveBeenCalled();
  });

  it('guard: does not call onUploadSuccess if the user navigated away (upload section hidden)', async () => {
    const postFn = vi.fn().mockResolvedValue({});
    const onUploadSuccess = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploadSuccess });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await vi.advanceTimersByTimeAsync(0);

    // User manually switches away from the Upload view before the timer fires.
    document.querySelector('section.view[data-view="upload"]').hidden = true;

    await vi.advanceTimersByTimeAsync(AUTO_SWITCH_WAIT);
    expect(onUploadSuccess).not.toHaveBeenCalled();
  });

  it('calls onUploadSuccess when the upload section is present and NOT hidden', async () => {
    const postFn = vi.fn().mockResolvedValue({});
    const onUploadSuccess = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploadSuccess });

    // Confirm the fixture's guard element resolves and starts visible.
    expect(document.querySelector('section.view[data-view="upload"]').hidden).toBe(false);

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(AUTO_SWITCH_WAIT);

    expect(onUploadSuccess).toHaveBeenCalledOnce();
  });

  it('destroy() before the delay elapses clears the timer — callback never fires', async () => {
    const postFn = vi.fn().mockResolvedValue({});
    const onUploadSuccess = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploadSuccess });

    simulateFileSelect('file-commbank', csvFile());
    clickSubmit();
    await vi.advanceTimersByTimeAsync(0);

    controller.destroy();
    controller = null; // already destroyed, skip afterEach destroy

    await vi.advanceTimersByTimeAsync(AUTO_SWITCH_WAIT);
    expect(onUploadSuccess).not.toHaveBeenCalled();
  });

  it('does not schedule any timer at all on the error path (no leaked pending timers)', async () => {
    const serverErr = new ApiError('upload failed', { status: 500, cause: null });
    const postFn = vi.fn().mockRejectedValue(serverErr);
    const onUploadSuccess = vi.fn();
    controller = createUploadController({ root: document, queue, postFn, onUploadSuccess });

    simulateFileSelect('file-commbank', csvFile());
    // Drain the local CSV preview read (jsdom's FileReader schedules a timer to
    // fire onload) so the only timer we could observe below is one the
    // controller itself scheduled.
    await vi.advanceTimersByTimeAsync(20);
    clickSubmit();
    await vi.advanceTimersByTimeAsync(0);

    // No auto-switch timer is scheduled on the error path.
    expect(vi.getTimerCount()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// destroy — removes listeners (no effect after destroy)
// ---------------------------------------------------------------------------

describe('destroy', () => {
  it('destroy() is callable without throwing', () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });
    expect(() => controller.destroy()).not.toThrow();
    controller = null; // already destroyed, skip afterEach destroy
  });
});

// ---------------------------------------------------------------------------
// Client-side CSV preview + XLSX acceptance (redesign)
// ---------------------------------------------------------------------------

// Synthetic CommBank-shaped CSV (no header): date, signed amount, description, balance.
const SYNTH_CB_CSV = [
  '01/06/2026,-42.85,SYNTH GROCER,1000.00',
  '02/06/2026,3200.00,SYNTH SALARY,4200.00',
].join('\n');

function cbCsvFile(name = 'commbank.csv') {
  return new File([SYNTH_CB_CSV], name, { type: 'text/csv' });
}

function xlsxFile(name = 'westpac.xlsx') {
  return new File(['PK\x03\x04'], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

// The CSV read (file.text()) and the preview render are async. A single fixed
// delay can lose the race under full-suite load (the render's macrotask lands
// after the timer fires), which is what made this file flaky. Instead, drain the
// microtask queue and yield the macrotask queue several times: any micro- or
// macrotask-based work settles deterministically within these turns, regardless
// of machine load.
async function flush() {
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  }
}

describe('client-side CSV preview', () => {
  it('reveals the preview card and renders parsed rows when a CSV is chosen', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-commbank', cbCsvFile());
    await flush();

    expect(document.getElementById('preview-card').hidden).toBe(false);
    const rows = document.querySelectorAll('#preview-body tr');
    expect(rows.length).toBe(2);
    expect(document.getElementById('preview-meta').textContent).toContain('Showing 2 of 2');
  });

  it('renders amounts with sign classes (negative red / positive accent)', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-commbank', cbCsvFile());
    await flush();

    const amountCells = document.querySelectorAll('#preview-body td.num');
    expect(amountCells[0].classList.contains('amt-neg')).toBe(true);
    expect(amountCells[1].classList.contains('amt-pos')).toBe(true);
  });

  it('sets the CommBank tab active and shows the row-count chip in the zone', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-commbank', cbCsvFile());
    await flush();

    const active = document.querySelector('#preview-tabs .ptab.active');
    expect(active.dataset.bank).toBe('commbank');
    expect(document.querySelector('[data-bank-info="commbank"]').textContent).toContain('2 rows detected');
    expect(document.querySelector('[data-bank-loaded="commbank"]').hidden).toBe(false);
  });

  it('renders no transaction data into #upload-status (privacy)', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-commbank', cbCsvFile());
    await flush();

    expect(getStatus()).not.toContain('SYNTH GROCER');
    expect(getStatus()).not.toContain('3200');
  });
});

describe('XLSX acceptance', () => {
  it('accepts an .xlsx file without a rejection message', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-westpac', xlsxFile());
    await flush();

    expect(getStatus()).not.toContain('accepted');
    expect(document.querySelector('[data-bank-loaded="westpac"]').hidden).toBe(false);
    expect(document.querySelector('[data-bank-ico="westpac"]').textContent).toBe('XLSX');
  });

  it('does NOT parse xlsx client-side — shows the after-upload note instead', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-westpac', xlsxFile());
    await flush();

    const note = document.getElementById('preview-note');
    expect(note.hidden).toBe(false);
    expect(note.textContent).toContain('XLSX preview available after upload');
    expect(document.querySelectorAll('#preview-body tr').length).toBe(0);
  });

  it('builds and submits a form for an xlsx file (postFn called)', async () => {
    const postFn = vi.fn().mockResolvedValue({});
    controller = createUploadController({ root: document, queue, postFn });

    simulateFileSelect('file-westpac', xlsxFile());
    await flush();
    clickSubmit();
    await flush();

    expect(postFn).toHaveBeenCalledOnce();
  });
});

describe('Clear button', () => {
  it('resets both zones, hides the preview, and clears the status', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-commbank', cbCsvFile());
    await flush();
    expect(document.getElementById('preview-card').hidden).toBe(false);

    document.getElementById('upload-clear').click();

    expect(document.getElementById('preview-card').hidden).toBe(true);
    expect(getFilenameLabel('commbank')).toBe('');
    expect(document.querySelector('[data-bank-loaded="commbank"]').hidden).toBe(true);
    expect(getStatus()).toBe('');
  });
});

describe('preview tab switching', () => {
  it('switches the visible bank when a tab is clicked', async () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });

    simulateFileSelect('file-commbank', cbCsvFile());
    await flush();

    // Click the (empty) Westpac tab.
    document.querySelector('#preview-tabs .ptab[data-bank="westpac"]').click();

    const active = document.querySelector('#preview-tabs .ptab.active');
    expect(active.dataset.bank).toBe('westpac');
    expect(document.getElementById('preview-note').textContent).toContain('No Westpac file staged');
  });
});
