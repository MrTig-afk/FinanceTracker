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

// ---------------------------------------------------------------------------
// v2 Pass 1 — nav reorder + Monthly/Yearly view sections.
// ---------------------------------------------------------------------------

describe('index.html — nav order (v2 Pass 2)', () => {
  it('lists data-view attributes in the exact order: upload, overview, trends, monthly, yearly, context', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/);
    expect(navMatch).not.toBeNull();
    const dataViews = [...navMatch[0].matchAll(/data-view="([^"]+)"/g)].map((m) => m[1]);
    expect(dataViews).toEqual(['upload', 'overview', 'trends', 'monthly', 'yearly', 'context']);
  });

  it('keeps Overview as the only nav-item--active entry', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/)[0];
    const activeMatches = navMatch.match(/nav-item--active/g) ?? [];
    expect(activeMatches.length).toBe(1);
    expect(navMatch).toContain('nav-item nav-item--active" data-view="overview"');
  });

  it('History and Settings remain inert (not converted to nav-view links)', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/)[0];
    expect(navMatch).toContain('nav-item--inert');
    expect(navMatch).not.toContain('data-view="history"');
    expect(navMatch).not.toContain('data-view="settings"');
  });
});

describe('index.html — Monthly view section', () => {
  it('has a <section data-view="monthly" hidden> block', () => {
    expect(html).toMatch(/<section class="view" data-view="monthly" hidden>/);
  });

  it('contains the monthly period select, message banner, and canvas', () => {
    expect(html).toContain('id="monthly-select"');
    expect(html).toContain('id="monthly-message"');
    expect(html).toContain('id="monthly-canvas"');
    expect(html).toContain('id="monthly-totals"');
    expect(html).toContain('id="monthly-compare"');
    expect(html).toContain('id="monthly-compare-label"');
  });
});

describe('index.html — Yearly view section', () => {
  it('has a <section data-view="yearly" hidden> block', () => {
    expect(html).toMatch(/<section class="view" data-view="yearly" hidden>/);
  });

  it('contains the yearly period select, message banner, and canvas', () => {
    expect(html).toContain('id="yearly-select"');
    expect(html).toContain('id="yearly-message"');
    expect(html).toContain('id="yearly-canvas"');
    expect(html).toContain('id="yearly-totals"');
    expect(html).toContain('id="yearly-compare"');
    expect(html).toContain('id="yearly-compare-label"');
  });
});

// ---------------------------------------------------------------------------
// v2 Pass 2 — Trends view + Overview mini spend-over-time bar.
// ---------------------------------------------------------------------------

describe('index.html — Trends view section', () => {
  it('has a <section data-view="trends" hidden> block', () => {
    expect(html).toMatch(/<section class="view" data-view="trends" hidden>/);
  });

  it('contains the trends window select, message banner, and canvas', () => {
    expect(html).toContain('id="trends-window"');
    expect(html).toContain('id="trends-message"');
    expect(html).toContain('id="trends-canvas"');
  });

  it('the Trends nav link uses the exact label "Trends"', () => {
    const navMatch = html.match(/<a href="#" class="nav-item" data-view="trends">[\s\S]*?<\/a>/);
    expect(navMatch).not.toBeNull();
    expect(navMatch[0]).toContain('Trends');
  });
});

describe('index.html — Overview mini spend-over-time bar', () => {
  it('has an #overview-trend-canvas inside the overview section', () => {
    expect(html).toContain('id="overview-trend-canvas"');
    expect(html).toContain('overview-trend-card');
  });

  it('has an #overview-trend-message banner', () => {
    expect(html).toContain('id="overview-trend-message"');
  });

  it('the overview mini card title is the exact spec string', () => {
    expect(html).toContain('Spending over the last 6 months');
  });
});

// ---------------------------------------------------------------------------
// v2 Pass 3 — push-notification control in the Upload view.
// ---------------------------------------------------------------------------

describe('index.html — push notification control (v2 Pass 3)', () => {
  it('has a #push-card section', () => {
    expect(html).toContain('id="push-card"');
  });

  it('#push-card appears after #upload-card in document order (Upload view placement)', () => {
    expect(html.indexOf('id="upload-card"')).toBeGreaterThan(-1);
    expect(html.indexOf('id="push-card"')).toBeGreaterThan(html.indexOf('id="upload-card"'));
  });

  it('contains #enable-push and #push-status', () => {
    expect(html).toContain('id="enable-push"');
    expect(html).toContain('id="push-status"');
  });

  it('the enable-push button text is the exact spec string with no em-dash/emoji', () => {
    const buttonMatch = html.match(/<button id="enable-push"[^>]*>([^<]*)<\/button>/);
    expect(buttonMatch).not.toBeNull();
    expect(buttonMatch[1]).toBe('Enable notifications');
    expect(buttonMatch[1]).not.toContain('—');
  });

  it('#push-status starts empty (populated by JS, not hardcoded)', () => {
    const statusMatch = html.match(/<p id="push-status"[^>]*>([^<]*)<\/p>/);
    expect(statusMatch).not.toBeNull();
    expect(statusMatch[1].trim()).toBe('');
  });

  it('does not add a new Settings/History nav item for push (nav stays inert)', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/)[0];
    expect(navMatch).not.toContain('data-view="push"');
    expect(navMatch).not.toContain('data-view="notifications"');
  });
});
