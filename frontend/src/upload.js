/**
 * upload.js — pure upload logic.
 * PRIVACY: CSV files (Blobs) are forwarded only to the owner's own backend at
 * ${API_BASE}/upload. No file CONTENTS are read, parsed, or inspected here —
 * only the Blob/File handle is held and forwarded. No secrets in this file.
 * VITE_API_BASE is a non-secret URL (localhost / Tailscale).
 */

import { API_BASE, ApiError } from './api.js';

export const ALLOWED_EXT = '.csv';
export const BANK_FIELDS = ['commbank', 'westpac'];

/**
 * Thrown when the files object fails validation (no file present, or wrong
 * extension). Message is always safe — never contains file contents.
 */
export class UploadValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = 'UploadValidationError';
  }
}

/**
 * Return true if the file looks like a CSV.
 *
 * Rules:
 *  - A File with a non-empty name: accepted only if the name ends with '.csv'
 *    (case-insensitive). A .txt file is rejected even if its type is text/csv.
 *  - A Blob with no name (name === undefined) or an empty name: accepted if
 *    the MIME type is '' (unknown), 'text/csv', 'application/csv', or
 *    'application/vnd.ms-excel'.
 *
 * @param {File|Blob} file
 * @returns {boolean}
 */
export function isCsvFile(file) {
  const name = file.name; // undefined for plain Blobs; string for Files
  if (typeof name === 'string' && name.length > 0) {
    // Has an explicit name — extension is the only check.
    return name.toLowerCase().endsWith(ALLOWED_EXT);
  }
  // No name (raw Blob) — fall back to MIME type check.
  const type = (file.type ?? '').toLowerCase();
  return (
    type === '' ||
    type === 'text/csv' ||
    type === 'application/csv' ||
    type === 'application/vnd.ms-excel'
  );
}

/**
 * Build a multipart FormData for the backend /upload endpoint.
 *
 * @param {{ commbank?: File|Blob, westpac?: File|Blob }} files
 *   At least one of commbank / westpac must be present and truthy.
 * @returns {FormData}
 * @throws {UploadValidationError}
 *   If no file is present, or if a present file fails the CSV check.
 */
export function buildUploadForm(files) {
  const present = BANK_FIELDS.filter((k) => files[k]);

  if (present.length === 0) {
    throw new UploadValidationError('Please choose at least one CSV file.');
  }

  const form = new FormData();
  for (const key of present) {
    const file = files[key];
    if (!isCsvFile(file)) {
      throw new UploadValidationError('Only .csv files are accepted.');
    }
    // Append under the exact field name — backend uses the name to identify
    // the bank; do NOT read or inspect the file bytes.
    form.append(key, file);
  }
  return form;
}

/**
 * POST a multipart form to the backend /upload endpoint.
 *
 * Do NOT set Content-Type manually — the browser must set the multipart
 * boundary itself. Only the Accept header is added.
 *
 * @param {FormData} form
 * @param {{ fetchImpl?: typeof fetch }} options
 *   `fetchImpl` is injectable for tests (defaults to the global `fetch`).
 * @returns {Promise<object>}  RunReport JSON on success.
 * @throws {ApiError}          On network failure or non-2xx HTTP status.
 */
export async function postUpload(form, { fetchImpl = fetch } = {}) {
  let res;
  try {
    res = await fetchImpl(`${API_BASE}/upload`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
      body: form,
      // NOTE: Content-Type MUST NOT be set here — the browser sets it
      // automatically with the correct multipart boundary.
    });
  } catch (e) {
    throw new ApiError('network error', { cause: e });
  }

  if (!res.ok) {
    throw new ApiError('upload failed', { status: res.status });
  }

  return res.json();
}
