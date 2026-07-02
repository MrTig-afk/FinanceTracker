/**
 * upload.test.js — unit tests for upload.js.
 * No real network calls — fetch is injected via the fetchImpl seam.
 * All file fixtures are synthetic, built inline. No real transaction data.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  ALLOWED_EXT,
  ALLOWED_EXTS,
  BANK_FIELDS,
  UploadValidationError,
  isCsvFile,
  isXlsxFile,
  isAcceptedUploadFile,
  parseCsvPreview,
  buildUploadForm,
  postUpload,
} from './upload.js';
import { ApiError } from './api.js';

function xlsxFile(name = 'westpac.xlsx') {
  // Synthetic placeholder bytes — we never parse xlsx client-side.
  return new File(['PK\x03\x04'], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

// ---------------------------------------------------------------------------
// Synthetic fixtures — no real transaction data, no real CSVs.
// ---------------------------------------------------------------------------

const SYNTH_CSV = 'date,amount,desc\n01-06-2026,-5.00,SYNTH\n';

function csvFile(name = 'commbank.csv') {
  return new File([SYNTH_CSV], name, { type: 'text/csv' });
}

function csvBlob() {
  return new Blob([SYNTH_CSV], { type: 'text/csv' });
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Exported constants
// ---------------------------------------------------------------------------

describe('exports', () => {
  it('ALLOWED_EXT is ".csv"', () => {
    expect(ALLOWED_EXT).toBe('.csv');
  });

  it('BANK_FIELDS contains commbank and westpac', () => {
    expect(BANK_FIELDS).toContain('commbank');
    expect(BANK_FIELDS).toContain('westpac');
  });
});

// ---------------------------------------------------------------------------
// UploadValidationError
// ---------------------------------------------------------------------------

describe('UploadValidationError', () => {
  it('has name === "UploadValidationError"', () => {
    const e = new UploadValidationError('test');
    expect(e.name).toBe('UploadValidationError');
  });

  it('is an instance of Error', () => {
    expect(new UploadValidationError('test')).toBeInstanceOf(Error);
  });

  it('carries the supplied message', () => {
    const e = new UploadValidationError('Only .csv files are accepted.');
    expect(e.message).toBe('Only .csv files are accepted.');
  });
});

// ---------------------------------------------------------------------------
// isCsvFile
// ---------------------------------------------------------------------------

describe('isCsvFile', () => {
  it('returns true for a File with a .csv name', () => {
    expect(isCsvFile(csvFile('commbank.csv'))).toBe(true);
  });

  it('returns true for a File with an uppercase .CSV name', () => {
    expect(isCsvFile(csvFile('EXPORT.CSV'))).toBe(true);
  });

  it('returns false for a File with a .txt name', () => {
    expect(isCsvFile(new File([SYNTH_CSV], 'notes.txt', { type: 'text/plain' }))).toBe(false);
  });

  it('returns false for a .txt File even when its MIME type is text/csv (name takes precedence)', () => {
    // A file whose name ends in .txt is rejected even if the type attribute
    // looks csv-ish — prevents type-spoofing.
    expect(isCsvFile(new File([SYNTH_CSV], 'data.txt', { type: 'text/csv' }))).toBe(false);
  });

  it('returns true for a plain Blob with type "text/csv"', () => {
    // Raw Blob has no name property, so MIME type is used.
    expect(isCsvFile(new Blob([SYNTH_CSV], { type: 'text/csv' }))).toBe(true);
  });

  it('returns true for a plain Blob with an empty type (unknown)', () => {
    expect(isCsvFile(new Blob([SYNTH_CSV], { type: '' }))).toBe(true);
  });

  it('returns false for a plain Blob with a non-csv MIME type', () => {
    expect(isCsvFile(new Blob([SYNTH_CSV], { type: 'text/plain' }))).toBe(false);
  });

  it('returns false for an .xlsx file (not a CSV)', () => {
    expect(isCsvFile(xlsxFile())).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isXlsxFile / isAcceptedUploadFile / ALLOWED_EXTS
// ---------------------------------------------------------------------------

describe('isXlsxFile', () => {
  it('returns true for a File with an .xlsx name', () => {
    expect(isXlsxFile(xlsxFile('westpac.xlsx'))).toBe(true);
  });

  it('returns true for an uppercase .XLSX name', () => {
    expect(isXlsxFile(xlsxFile('EXPORT.XLSX'))).toBe(true);
  });

  it('returns false for a .csv file', () => {
    expect(isXlsxFile(csvFile())).toBe(false);
  });

  it('fails closed for a nameless Blob (cannot confidently detect xlsx)', () => {
    expect(isXlsxFile(new Blob(['PK'], { type: '' }))).toBe(false);
  });
});

describe('isAcceptedUploadFile', () => {
  it('accepts CSV files', () => {
    expect(isAcceptedUploadFile(csvFile())).toBe(true);
  });

  it('accepts XLSX files', () => {
    expect(isAcceptedUploadFile(xlsxFile())).toBe(true);
  });

  it('rejects a .txt file', () => {
    expect(isAcceptedUploadFile(new File([SYNTH_CSV], 'notes.txt', { type: 'text/plain' }))).toBe(false);
  });
});

describe('ALLOWED_EXTS', () => {
  it('contains both .csv and .xlsx', () => {
    expect(ALLOWED_EXTS).toContain('.csv');
    expect(ALLOWED_EXTS).toContain('.xlsx');
  });
});

// ---------------------------------------------------------------------------
// parseCsvPreview — client-side, local-only preview parsing (no network)
// ---------------------------------------------------------------------------

describe('parseCsvPreview — commbank profile (no header)', () => {
  // Synthetic rows: date, signed amount, description, balance.
  const CB = [
    '01/06/2026,-42.85,SYNTH GROCER,1000.00',
    '02/06/2026,3200.00,SYNTH SALARY,4200.00',
  ].join('\n');

  it('parses each non-empty line as a row (no header skipped)', () => {
    const rows = parseCsvPreview(CB, { bank: 'commbank' });
    expect(rows).toHaveLength(2);
  });

  it('maps date/amount/description from the signed-amount column', () => {
    const [first, second] = parseCsvPreview(CB, { bank: 'commbank' });
    expect(first).toMatchObject({ date: '01/06/2026', description: 'SYNTH GROCER', amount: -42.85 });
    expect(second.amount).toBe(3200);
  });

  it('defaults amount to 0 when the amount cell is not numeric', () => {
    const rows = parseCsvPreview('01/06/2026,,SYNTH,10.00', { bank: 'commbank' });
    expect(rows[0].amount).toBe(0);
  });
});

describe('parseCsvPreview — westpac profile (header + split debit/credit)', () => {
  // Synthetic: account-number, Date, Narrative, Debit, Credit, Balance.
  const WP = [
    'Account,Date,Narrative,Debit,Credit,Balance',
    '123456,01/06/2026,SYNTH GROCER,42.85,,1000.00',
    '123456,02/06/2026,SYNTH SALARY,,3200.00,4200.00',
  ].join('\n');

  it('skips the header row', () => {
    const rows = parseCsvPreview(WP, { bank: 'westpac' });
    expect(rows).toHaveLength(2);
  });

  it('drops the leading account-number column and merges debit/credit to a signed amount', () => {
    const [debitRow, creditRow] = parseCsvPreview(WP, { bank: 'westpac' });
    expect(debitRow).toMatchObject({ date: '01/06/2026', description: 'SYNTH GROCER', amount: -42.85 });
    expect(creditRow).toMatchObject({ description: 'SYNTH SALARY', amount: 3200 });
  });
});

describe('parseCsvPreview — quoting and edge cases', () => {
  it('tolerates a quoted field containing a comma', () => {
    const rows = parseCsvPreview('01/06/2026,-10.00,"SYNTH, INC",5.00', { bank: 'commbank' });
    expect(rows[0].description).toBe('SYNTH, INC');
  });

  it('returns an empty array for empty text', () => {
    expect(parseCsvPreview('', { bank: 'commbank' })).toEqual([]);
  });

  it('ignores blank lines', () => {
    const rows = parseCsvPreview('01/06/2026,-1.00,A,0\n\n02/06/2026,-2.00,B,0\n', { bank: 'commbank' });
    expect(rows).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// buildUploadForm — field names and validation
// ---------------------------------------------------------------------------

describe('buildUploadForm', () => {
  it('returns a FormData when only commbank is provided', () => {
    const form = buildUploadForm({ commbank: csvFile() });
    expect(form).toBeInstanceOf(FormData);
  });

  it('FormData contains the "commbank" field when only commbank is provided', () => {
    const form = buildUploadForm({ commbank: csvFile() });
    expect(form.has('commbank')).toBe(true);
  });

  it('FormData does NOT contain the "westpac" field when only commbank is provided', () => {
    const form = buildUploadForm({ commbank: csvFile() });
    expect(form.has('westpac')).toBe(false);
  });

  it('returns a FormData when only westpac is provided', () => {
    const form = buildUploadForm({ westpac: csvFile('westpac.csv') });
    expect(form).toBeInstanceOf(FormData);
    expect(form.has('westpac')).toBe(true);
    expect(form.has('commbank')).toBe(false);
  });

  it('FormData contains both fields when both files are provided', () => {
    const form = buildUploadForm({
      commbank: csvFile('commbank.csv'),
      westpac: csvFile('westpac.csv'),
    });
    expect(form.has('commbank')).toBe(true);
    expect(form.has('westpac')).toBe(true);
  });

  it('throws UploadValidationError when no files are provided (empty object)', () => {
    expect(() => buildUploadForm({})).toThrow(UploadValidationError);
  });

  it('throws UploadValidationError with a safe message when no files provided', () => {
    let err;
    try {
      buildUploadForm({});
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(UploadValidationError);
    // Message must be safe — no file contents or sensitive data.
    expect(err.message).toBeTruthy();
    expect(typeof err.message).toBe('string');
  });

  it('accepts an .xlsx file under a bank key', () => {
    const form = buildUploadForm({ westpac: xlsxFile('westpac.xlsx') });
    expect(form.has('westpac')).toBe(true);
  });

  it('accepts a mix of CSV and XLSX across the two banks', () => {
    const form = buildUploadForm({ commbank: csvFile(), westpac: xlsxFile() });
    expect(form.has('commbank')).toBe(true);
    expect(form.has('westpac')).toBe(true);
  });

  it('throws UploadValidationError when a non-CSV/XLSX file is provided', () => {
    const txtFile = new File([SYNTH_CSV], 'export.txt', { type: 'text/plain' });
    expect(() => buildUploadForm({ commbank: txtFile })).toThrow(UploadValidationError);
  });

  it('safe message for non-CSV file contains no file contents', () => {
    const txtFile = new File(['PRIVATE DATA'], 'export.txt', { type: 'text/plain' });
    let err;
    try {
      buildUploadForm({ commbank: txtFile });
    } catch (e) {
      err = e;
    }
    expect(err.message).not.toContain('PRIVATE DATA');
  });

  it('accepts a plain CSV Blob (no filename) under a bank key', () => {
    const form = buildUploadForm({ commbank: csvBlob() });
    expect(form.has('commbank')).toBe(true);
  });

  it('uses exactly the field name "commbank" — not "CommBank" or other variants', () => {
    const form = buildUploadForm({ commbank: csvFile() });
    // Exact case match required by the backend.
    expect(form.has('commbank')).toBe(true);
    expect(form.has('CommBank')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// postUpload — network behaviour (no real network; fetchImpl injected)
// ---------------------------------------------------------------------------

describe('postUpload', () => {
  it('sends a POST request to a URL that includes "/upload"', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ processed: 1, errors: [] }),
    });
    const form = buildUploadForm({ commbank: csvFile() });
    await postUpload(form, { fetchImpl: mockFetch });

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain('/upload');
  });

  it('uses method POST', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    const form = buildUploadForm({ commbank: csvFile() });
    await postUpload(form, { fetchImpl: mockFetch });

    const [, options] = mockFetch.mock.calls[0];
    expect(options.method).toBe('POST');
  });

  it('does NOT set Content-Type manually (lets browser set multipart boundary)', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    const form = buildUploadForm({ commbank: csvFile() });
    await postUpload(form, { fetchImpl: mockFetch });

    const [, options] = mockFetch.mock.calls[0];
    // Content-Type must be absent from the caller-supplied headers so the
    // browser can set the multipart boundary automatically.
    expect(options.headers['Content-Type']).toBeUndefined();
    expect(options.headers['content-type']).toBeUndefined();
  });

  it('sends Accept: application/json header', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    const form = buildUploadForm({ commbank: csvFile() });
    await postUpload(form, { fetchImpl: mockFetch });

    const [, options] = mockFetch.mock.calls[0];
    expect(options.headers).toMatchObject({ Accept: 'application/json' });
  });

  it('resolves with the RunReport JSON on a 200 response', async () => {
    const REPORT = { processed: 2, errors: [], months: ['2026-06'] };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => REPORT,
    });
    const form = buildUploadForm({ commbank: csvFile() });
    const result = await postUpload(form, { fetchImpl: mockFetch });
    expect(result).toEqual(REPORT);
  });

  it('throws ApiError with status 500 on a 500 response', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });
    const form = buildUploadForm({ commbank: csvFile() });
    const err = await postUpload(form, { fetchImpl: mockFetch }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });

  it('throws ApiError on a 422 response', async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: false, status: 422 });
    const form = buildUploadForm({ commbank: csvFile() });
    const err = await postUpload(form, { fetchImpl: mockFetch }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
  });

  it('throws ApiError (not raw TypeError) when the injected fetch rejects', async () => {
    const mockFetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    const form = buildUploadForm({ commbank: csvFile() });
    const err = await postUpload(form, { fetchImpl: mockFetch }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
  });

  it('network-error ApiError has null status and a TypeError cause (unreachable backend)', async () => {
    const mockFetch = vi.fn().mockRejectedValue(new TypeError('net::ERR_CONNECTION_REFUSED'));
    const form = buildUploadForm({ commbank: csvFile() });
    const thrown = await postUpload(form, { fetchImpl: mockFetch }).catch((e) => e);
    expect(thrown.status).toBeNull();
    expect(thrown.cause).toBeInstanceOf(TypeError);
  });
});
