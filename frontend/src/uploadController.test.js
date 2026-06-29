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

function csvFile(name = 'commbank.csv') {
  return new File([SYNTH_CSV], name, { type: 'text/csv' });
}

// ---------------------------------------------------------------------------
// Minimal upload-section HTML (mirrors the contract in index.html).
// ---------------------------------------------------------------------------

const UPLOAD_HTML = `
  <div id="dropzone-commbank" class="dropzone" tabindex="0" role="button">
    <span class="dropzone-hint">Drop CSV here</span>
    <span id="filename-commbank" class="filename"></span>
  </div>
  <input id="file-commbank" type="file" accept=".csv" />

  <div id="dropzone-westpac" class="dropzone" tabindex="0" role="button">
    <span class="dropzone-hint">Drop CSV here</span>
    <span id="filename-westpac" class="filename"></span>
  </div>
  <input id="file-westpac" type="file" accept=".csv" />

  <button id="upload-submit" type="button">Upload</button>
  <p id="upload-status" role="status" aria-live="polite"></p>
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
// destroy — removes listeners (no effect after destroy)
// ---------------------------------------------------------------------------

describe('destroy', () => {
  it('destroy() is callable without throwing', () => {
    controller = createUploadController({ root: document, queue, postFn: vi.fn() });
    expect(() => controller.destroy()).not.toThrow();
    controller = null; // already destroyed, skip afterEach destroy
  });
});
