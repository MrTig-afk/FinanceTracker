/**
 * manifest.test.js — validates frontend/public/manifest.webmanifest.
 * Reads the file from disk via fs.readFileSync and asserts structural
 * correctness. No network calls. No real transaction data.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

// ---------------------------------------------------------------------------
// Resolve the manifest path relative to this test file's directory.
// This test lives in frontend/src/; the manifest is at frontend/public/.
// ---------------------------------------------------------------------------

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const MANIFEST_PATH = resolve(__dirname, '../public/manifest.webmanifest');

let manifest;

beforeAll(() => {
  // Throws here (before any it()) if the file is missing — intentional: a
  // missing manifest is a build error, not a test failure to swallow.
  const raw = readFileSync(MANIFEST_PATH, 'utf8');
  manifest = JSON.parse(raw); // throws SyntaxError if not valid JSON
});

// ---------------------------------------------------------------------------
// Structural checks
// ---------------------------------------------------------------------------

describe('manifest.webmanifest', () => {
  it('parses as valid JSON', () => {
    // If beforeAll succeeded, the parse was valid. Confirm the result is an object.
    expect(typeof manifest).toBe('object');
    expect(manifest).not.toBeNull();
  });

  it('name is "FinanceTracker"', () => {
    expect(manifest.name).toBe('FinanceTracker');
  });

  it('short_name is present', () => {
    expect(manifest.short_name).toBeTruthy();
  });

  it('start_url is present', () => {
    expect(manifest.start_url).toBeTruthy();
  });

  it('display is "standalone"', () => {
    expect(manifest.display).toBe('standalone');
  });

  it('icons is a non-empty array', () => {
    expect(Array.isArray(manifest.icons)).toBe(true);
    expect(manifest.icons.length).toBeGreaterThan(0);
  });

  it('every icon entry has a src property', () => {
    for (const icon of manifest.icons) {
      expect(typeof icon.src).toBe('string');
      expect(icon.src.length).toBeGreaterThan(0);
    }
  });

  it('scope is present', () => {
    expect(manifest.scope).toBeTruthy();
  });

  it('background_color is present', () => {
    expect(manifest.background_color).toBeTruthy();
  });

  it('theme_color is present', () => {
    expect(manifest.theme_color).toBeTruthy();
  });
});
