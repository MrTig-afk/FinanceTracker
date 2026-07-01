/**
 * indexHtml.test.js — static assertions against frontend/index.html.
 * Reads the file from disk via fs.readFileSync and checks for exact markup
 * fragments introduced by Change 2 (logo / brand mark integration). No DOM
 * construction, no network calls, no real transaction data.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const INDEX_HTML_PATH = resolve(__dirname, '../index.html');

let html;

beforeAll(() => {
  html = readFileSync(INDEX_HTML_PATH, 'utf8');
});

describe('index.html — favicon', () => {
  it('points the favicon <link> at /finance-tracker-app-icon.svg', () => {
    expect(html).toContain('rel="icon" href="/finance-tracker-app-icon.svg"');
  });

  it('no longer references the old /icon.svg favicon', () => {
    expect(html).not.toContain('href="/icon.svg"');
  });
});

describe('index.html — sidebar brand mark', () => {
  it('contains a .brand-mark <img> pointing at the mark SVG', () => {
    expect(html).toContain('class="brand-mark"');
    expect(html).toContain('src="/finance-tracker-mark.svg"');
  });

  it('no longer contains the old .brand-dot span', () => {
    expect(html).not.toContain('class="brand-dot"');
  });

  it('the wordmark is real HTML text split into two .brand-word spans', () => {
    expect(html).toContain('class="brand-word"');
    expect(html).toContain('brand-word--accent');
    expect(html).toContain('Finance');
    expect(html).toContain('Tracker');
  });
});

describe('index.html — no new webfont dependency introduced', () => {
  it('still references only the pre-existing Google Fonts stylesheet (Figtree/JetBrains Mono)', () => {
    const fontLinkMatches = html.match(/fonts\.googleapis\.com\/css2[^"]*/g) ?? [];
    expect(fontLinkMatches.length).toBe(1);
    expect(fontLinkMatches[0]).toContain('Figtree');
  });
});
