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
  it('lists data-view attributes in the exact order: upload, overview, search, transfers, trends, monthly, yearly, context, contact, settings', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/);
    expect(navMatch).not.toBeNull();
    const dataViews = [...navMatch[0].matchAll(/data-view="([^"]+)"/g)].map((m) => m[1]);
    expect(dataViews).toEqual([
      'upload', 'overview', 'search', 'transfers', 'trends', 'monthly', 'yearly', 'context', 'contact', 'settings',
    ]);
  });

  it('keeps Overview as the only nav-item--active entry', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/)[0];
    const activeMatches = navMatch.match(/nav-item--active/g) ?? [];
    expect(activeMatches.length).toBe(1);
    expect(navMatch).toContain('nav-item nav-item--active" data-view="overview"');
  });

  it('drops the inert History item and makes Settings + Contact real nav-view links (Feature E)', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/)[0];
    expect(navMatch).not.toContain('nav-item--inert');
    expect(navMatch).not.toContain('History');
    expect(navMatch).toContain('data-view="settings"');
    expect(navMatch).toContain('data-view="contact"');
  });
});

// Isolate a single view section's markup for scoped assertions.
function viewSection(view) {
  const re = new RegExp(`<section class="view" data-view="${view}" hidden>[\\s\\S]*?\\n {10}</section>`);
  const m = html.match(re);
  return m ? m[0] : '';
}

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

  it('has the hero KPI + legend ids', () => {
    const s = viewSection('monthly');
    expect(s).toContain('id="monthly-kpi-spent"');
    expect(s).toContain('id="monthly-income"');
    expect(s).toContain('id="monthly-net"');
    expect(s).toContain('id="monthly-legend"');
    expect(s).toContain('donut-glow');
  });

  it('has real comparison column headers and a totals footer row', () => {
    const s = viewSection('monthly');
    expect(s).toContain('<th>Category</th>');
    expect(s).toContain('<th>This month</th>');
    expect(s).toContain('<th>Last month</th>');
    expect(s).toContain('<th>Change</th>');
    expect(s).toContain('<th>Change %</th>');
    expect(s).toContain('id="monthly-compare-foot"');
  });

  it('gives the breakdown and comparison tables clear titles', () => {
    const s = viewSection('monthly');
    expect(s).toContain('Spending breakdown');
    expect(s).toContain('Category totals');
    expect(s).toContain('Change vs');
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

  it('has the hero KPI + legend ids', () => {
    const s = viewSection('yearly');
    expect(s).toContain('id="yearly-kpi-spent"');
    expect(s).toContain('id="yearly-income"');
    expect(s).toContain('id="yearly-net"');
    expect(s).toContain('id="yearly-legend"');
    expect(s).toContain('donut-glow');
  });

  it('has real comparison column headers (year variant) and a totals footer row', () => {
    const s = viewSection('yearly');
    expect(s).toContain('<th>Category</th>');
    expect(s).toContain('<th>This year</th>');
    expect(s).toContain('<th>Last year</th>');
    expect(s).toContain('<th>Change</th>');
    expect(s).toContain('<th>Change %</th>');
    expect(s).toContain('id="yearly-compare-foot"');
  });

  it('gives the breakdown and comparison tables clear titles', () => {
    const s = viewSection('yearly');
    expect(s).toContain('Spending breakdown');
    expect(s).toContain('Category totals');
    expect(s).toContain('Change vs');
  });
});

// ---------------------------------------------------------------------------
// v2 Pass 2 — Trends view + Overview mini spend-over-time bar.
// ---------------------------------------------------------------------------

describe('index.html — Trends view section', () => {
  it('has a <section data-view="trends" hidden> block', () => {
    expect(html).toMatch(/<section class="view" data-view="trends" hidden>/);
  });

  it('contains the trends window select, message banner, inline-SVG chart, and legend', () => {
    expect(html).toContain('id="trends-window"');
    expect(html).toContain('id="trends-message"');
    expect(html).toContain('id="trends-chart"');
    expect(html).toContain('id="trends-legend"');
  });

  it('renders the chart as an inline <svg> (no <canvas>) with the design viewBox', () => {
    const trendsMatch = html.match(/<section class="view" data-view="trends" hidden>[\s\S]*?<\/section>\s*<\/div>/);
    expect(trendsMatch).not.toBeNull();
    const trends = trendsMatch[0];
    expect(trends).toContain('viewBox="0 0 900 400"');
    expect(trends).not.toContain('id="trends-canvas"');
    expect(trends).not.toContain('<canvas');
  });

  it('the Trends nav link uses the exact label "Trends"', () => {
    const navMatch = html.match(/<a href="#" class="nav-item" data-view="trends">[\s\S]*?<\/a>/);
    expect(navMatch).not.toBeNull();
    expect(navMatch[0]).toContain('Trends');
  });
});

// v7 feature 3 — Net position card inside the Trends view.
describe('index.html — Net position card (v7 feature 3)', () => {
  it('contains the net-position message, inline-SVG chart, and legend ids', () => {
    expect(html).toContain('id="netpos-message"');
    expect(html).toContain('id="netpos-chart"');
    expect(html).toContain('id="netpos-legend"');
  });

  it('renders the Net position card inside the Trends view section', () => {
    const trendsMatch = html.match(/<section class="view" data-view="trends" hidden>[\s\S]*?\n {10}<\/section>/);
    expect(trendsMatch).not.toBeNull();
    const trends = trendsMatch[0];
    expect(trends).toContain('class="card netpos-card"');
    expect(trends).toContain('id="netpos-chart"');
  });

  it('draws the net-position chart as an inline <svg> (no <canvas>) with the design viewBox', () => {
    const svgMatch = html.match(/<svg id="netpos-chart"[\s\S]*?<\/svg>/);
    expect(svgMatch).not.toBeNull();
    expect(svgMatch[0]).toContain('viewBox="0 0 900 400"');
    expect(html).not.toContain('id="netpos-canvas"');
    expect(html).not.toContain('<canvas id="netpos');
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
// v7 feature 2 — Transfers unseen-count nav badge.
// ---------------------------------------------------------------------------

describe('index.html — Transfers unseen-count nav badge', () => {
  it('has the badge span with the pinned id, class, and hidden attribute', () => {
    expect(html).toContain('id="nav-badge-transfers"');
    const spanMatch = html.match(/<span id="nav-badge-transfers"[^>]*>/);
    expect(spanMatch).not.toBeNull();
    expect(spanMatch[0]).toContain('class="nav-badge"');
    expect(spanMatch[0]).toContain('hidden');
  });

  it('nests the badge INSIDE the Transfers anchor (one element, both navs)', () => {
    const anchorMatch = html.match(
      /<a href="#" class="nav-item" data-view="transfers">[\s\S]*?<\/a>/,
    );
    expect(anchorMatch).not.toBeNull();
    expect(anchorMatch[0]).toContain('id="nav-badge-transfers"');
    // The anchor still names the view and keeps its dot.
    expect(anchorMatch[0]).toContain('Transfers');
    expect(anchorMatch[0]).toContain('class="nav-dot"');
  });

  it('the badge lives inside the sidebar-nav block (also the mobile drawer)', () => {
    const navMatch = html.match(/<nav class="sidebar-nav"[^>]*>[\s\S]*?<\/nav>/)[0];
    expect(navMatch).toContain('id="nav-badge-transfers"');
  });

  it('keeps the Transfers anchor opening tag byte-identical (pinned regex intact)', () => {
    // The pinned trends-anchor-style regex must still match the transfers anchor.
    const navMatch = html.match(
      /<a href="#" class="nav-item" data-view="transfers">[\s\S]*?<\/a>/,
    );
    expect(navMatch).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Upload redesign — branded dropzones + client preview + xlsx acceptance.
// ---------------------------------------------------------------------------

describe('index.html — upload redesign (branded dropzones)', () => {
  it('keeps the required dropzone / input / status / submit ids', () => {
    for (const id of [
      'dropzone-commbank',
      'dropzone-westpac',
      'file-commbank',
      'file-westpac',
      'filename-commbank',
      'filename-westpac',
      'upload-submit',
      'upload-status',
    ]) {
      expect(html).toContain(`id="${id}"`);
    }
  });

  it('both file inputs accept .csv AND .xlsx', () => {
    const inputs = html.match(/<input id="file-(commbank|westpac)"[^>]*>/g) ?? [];
    expect(inputs.length).toBe(2);
    for (const input of inputs) {
      expect(input).toContain('accept=".csv,.xlsx"');
    }
  });

  it('references the bank brand marks on white logo tiles', () => {
    expect(html).toContain('src="/commbank-mark.svg"');
    expect(html).toContain('src="/westpac-mark.svg"');
    expect(html).toContain('class="zone-badge"');
  });

  it('shows CSV and XLSX format pills in the empty state', () => {
    expect(html).toContain('class="format-pill">CSV<');
    expect(html).toContain('class="format-pill">XLSX<');
  });

  it('has the primary "Upload and categorise" button and a ghost Clear button', () => {
    const submit = html.match(/<button id="upload-submit"[^>]*>([^<]*)<\/button>/);
    expect(submit).not.toBeNull();
    expect(submit[1]).toBe('Upload and categorise');
    expect(html).toContain('id="upload-clear"');
  });
});

describe('index.html — bank web-login links on the dropzones', () => {
  it('links to the NetBank and Westpac online banking login pages', () => {
    expect(html).toContain(
      'href="https://www.my.commbank.com.au/netbank/Logon/Logon.aspx"',
    );
    expect(html).toContain(
      'href="https://banking.westpac.com.au/wbc/banking/handler?fi=wbc&amp;TAM_OP=login&amp;segment=personal&amp;logout=false"',
    );
  });

  it('has exactly two login links, one per dropzone', () => {
    expect(html.match(/class="zone-login-link"/g)).toHaveLength(2);
  });

  it('every login link opens in a new tab with noopener noreferrer', () => {
    const anchors = html.match(/<a\s+class="zone-login-link"[\s\S]*?>/g) ?? [];
    expect(anchors).toHaveLength(2);
    for (const anchor of anchors) {
      expect(anchor).toContain('target="_blank"');
      expect(anchor).toContain('rel="noopener noreferrer"');
    }
  });
});

describe('index.html — preview panel', () => {
  it('has a #preview-card with tabs, meta, table body, and note', () => {
    expect(html).toContain('id="preview-card"');
    expect(html).toContain('id="preview-tabs"');
    expect(html).toContain('id="preview-meta"');
    expect(html).toContain('id="preview-body"');
    expect(html).toContain('id="preview-note"');
  });

  it('shows the local-read privacy note in the footer', () => {
    expect(html).toContain('Preview is read locally. Nothing is sent until you press Upload.');
  });

  it('places #preview-card between #upload-card and #push-card', () => {
    const upload = html.indexOf('id="upload-card"');
    const preview = html.indexOf('id="preview-card"');
    const push = html.indexOf('id="push-card"');
    expect(upload).toBeGreaterThan(-1);
    expect(preview).toBeGreaterThan(upload);
    expect(push).toBeGreaterThan(preview);
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
