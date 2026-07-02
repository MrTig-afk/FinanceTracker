/**
 * upload.js — pure upload logic.
 * PRIVACY: CSV files (Blobs) are forwarded only to the owner's own backend at
 * ${API_BASE}/upload. No file CONTENTS are read, parsed, or inspected here —
 * only the Blob/File handle is held and forwarded. No secrets in this file.
 * VITE_API_BASE is a non-secret URL (localhost / Tailscale).
 */

import { API_BASE, ApiError } from './api.js';

export const ALLOWED_EXT = '.csv';
export const ALLOWED_EXTS = ['.csv', '.xlsx'];
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
 * Return true if the file looks like an .xlsx workbook.
 *
 * XLSX is only recognised by an explicit filename extension — a nameless Blob
 * cannot be confidently identified as xlsx (its MIME type is ambiguous), so we
 * fail closed and return false.
 *
 * @param {File|Blob} file
 * @returns {boolean}
 */
export function isXlsxFile(file) {
  const name = file.name;
  if (typeof name === 'string' && name.length > 0) {
    return name.toLowerCase().endsWith('.xlsx');
  }
  return false;
}

/**
 * Return true if the file is an accepted upload (CSV or XLSX).
 *
 * @param {File|Blob} file
 * @returns {boolean}
 */
export function isAcceptedUploadFile(file) {
  return isCsvFile(file) || isXlsxFile(file);
}

/**
 * Build a multipart FormData for the backend /upload endpoint.
 *
 * @param {{ commbank?: File|Blob, westpac?: File|Blob }} files
 *   At least one of commbank / westpac must be present and truthy.
 * @returns {FormData}
 * @throws {UploadValidationError}
 *   If no file is present, or if a present file is neither CSV nor XLSX.
 */
export function buildUploadForm(files) {
  const present = BANK_FIELDS.filter((k) => files[k]);

  if (present.length === 0) {
    throw new UploadValidationError('Please choose at least one CSV or XLSX file.');
  }

  const form = new FormData();
  for (const key of present) {
    const file = files[key];
    if (!isAcceptedUploadFile(file)) {
      throw new UploadValidationError('Only .csv or .xlsx files are accepted.');
    }
    // Append under the exact field name — backend uses the name to identify
    // the bank; do NOT read or inspect the file bytes.
    form.append(key, file);
  }
  return form;
}

// ---------------------------------------------------------------------------
// Client-side CSV preview parser.
//
// PRIVACY: this runs ENTIRELY in the browser to render a local read-only
// preview. The parsed rows are NEVER sent anywhere — only the raw File is
// forwarded to the owner's own backend when Upload is pressed. No library is
// used; this is a small RFC-4180-ish splitter that is good enough for a
// preview of the two supported bank export shapes.
// ---------------------------------------------------------------------------

/** Split one CSV line into fields, tolerating quoted fields containing commas. */
function splitCsvLine(line) {
  const out = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i += 1; // escaped quote
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

/** Parse a possibly-formatted money string to a Number, or null if not numeric. */
function toAmount(raw) {
  if (raw == null) return null;
  const cleaned = String(raw).replace(/[^0-9.\-]/g, '');
  if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/**
 * Parse CSV text into preview rows `{ date, description, amount }`.
 *
 * Per-bank profiles mirror `.claude/rules/data-formats.md`:
 *  - commbank: no header; columns `date, amount(signed), description, balance`.
 *  - westpac:  header row; drop the leading account-number column; merge the
 *              separate debit/credit columns into one signed amount.
 *
 * @param {string} text  raw CSV file contents
 * @param {{ bank?: 'commbank'|'westpac' }} [opts]
 * @returns {Array<{ date: string, description: string, amount: number }>}
 */
export function parseCsvPreview(text, { bank = 'commbank' } = {}) {
  const lines = String(text ?? '')
    .split(/\r\n|\r|\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);

  const rows = [];

  if (bank === 'westpac') {
    // First line is a header — skip it.
    for (let i = 1; i < lines.length; i += 1) {
      const cols = splitCsvLine(lines[i]).slice(1); // drop account-number column
      const date = (cols[0] ?? '').trim();
      const description = (cols[1] ?? '').trim();
      const debit = toAmount(cols[2]);
      const credit = toAmount(cols[3]);
      let amount = 0;
      if (credit != null && credit !== 0) amount = Math.abs(credit);
      else if (debit != null && debit !== 0) amount = -Math.abs(debit);
      rows.push({ date, description, amount });
    }
    return rows;
  }

  // commbank: no header row.
  for (const line of lines) {
    const cols = splitCsvLine(line);
    const date = (cols[0] ?? '').trim();
    const amount = toAmount(cols[1]);
    const description = (cols[2] ?? '').trim();
    rows.push({ date, description, amount: amount ?? 0 });
  }
  return rows;
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
