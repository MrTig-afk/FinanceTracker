/**
 * views.test.js — unit tests for the view switcher (views.js).
 * jsdom provides the DOM. No network. No transaction data — pure DOM structure.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { initViews } from './views.js';

// ---------------------------------------------------------------------------
// Minimal shell mirroring index.html's nav + view sections.
// ---------------------------------------------------------------------------

const SHELL_HTML = `
  <nav>
    <a href="#" class="nav-item" data-view="upload">Upload</a>
    <a href="#" class="nav-item nav-item--active" data-view="overview">Overview</a>
    <a href="#" class="nav-item" data-view="trends">Trends</a>
    <a href="#" class="nav-item" data-view="monthly">Monthly</a>
    <a href="#" class="nav-item" data-view="yearly">Yearly</a>
    <a href="#" class="nav-item" data-view="context">Category context</a>
    <span class="nav-item nav-item--inert">History</span>
    <span class="nav-item nav-item--inert">Settings</span>
  </nav>
  <header class="site-header">
    <h1>Overview</h1>
    <p id="month-label"></p>
  </header>
  <div class="main-content">
    <section class="view" data-view="overview">
      <div id="message"></div>
      <div class="overview-grid">
        <section class="card donut-card"></section>
      </div>
    </section>
    <section class="view" data-view="upload" hidden>
      <section id="upload-card" class="card upload">
        <div id="dropzone-commbank"></div>
        <div id="dropzone-westpac"></div>
        <button id="upload-submit" type="button">Upload</button>
      </section>
    </section>
    <section class="view" data-view="trends" hidden>
      <select id="trends-window"></select>
    </section>
    <section class="view" data-view="monthly" hidden>
      <select id="monthly-select"></select>
    </section>
    <section class="view" data-view="yearly" hidden>
      <select id="yearly-select"></select>
    </section>
    <section class="view" data-view="context" hidden>
      <div id="category-cards"></div>
    </section>
  </div>
`;

let controller;

beforeEach(() => {
  document.body.innerHTML = SHELL_HTML;
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
  vi.restoreAllMocks();
});

function sectionFor(view) {
  return document.querySelector(`section.view[data-view="${view}"]`);
}

function navFor(view) {
  return document.querySelector(`a.nav-item[data-view="${view}"]`);
}

// ---------------------------------------------------------------------------
// Default view
// ---------------------------------------------------------------------------

describe('initViews default state', () => {
  it('shows the overview view by default', () => {
    controller = initViews({ root: document });
    expect(sectionFor('overview').hidden).toBe(false);
  });

  it('hides the upload and context views by default', () => {
    controller = initViews({ root: document });
    expect(sectionFor('upload').hidden).toBe(true);
    expect(sectionFor('context').hidden).toBe(true);
  });

  it('hides the monthly and yearly views by default', () => {
    controller = initViews({ root: document });
    expect(sectionFor('monthly').hidden).toBe(true);
    expect(sectionFor('yearly').hidden).toBe(true);
  });

  it('hides the trends view by default', () => {
    controller = initViews({ root: document });
    expect(sectionFor('trends').hidden).toBe(true);
  });

  it('marks the overview nav item active by default', () => {
    controller = initViews({ root: document });
    expect(navFor('overview').classList.contains('nav-item--active')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Clicking a nav item
// ---------------------------------------------------------------------------

describe('clicking a nav item', () => {
  it('shows the upload view and hides overview/context', () => {
    controller = initViews({ root: document });
    navFor('upload').click();

    expect(sectionFor('upload').hidden).toBe(false);
    expect(sectionFor('overview').hidden).toBe(true);
    expect(sectionFor('context').hidden).toBe(true);
  });

  it('sets .nav-item--active on the clicked item and removes it from others', () => {
    controller = initViews({ root: document });
    navFor('context').click();

    expect(navFor('context').classList.contains('nav-item--active')).toBe(true);
    expect(navFor('overview').classList.contains('nav-item--active')).toBe(false);
    expect(navFor('upload').classList.contains('nav-item--active')).toBe(false);
  });

  it('calls onShow with the view name', () => {
    const onShow = vi.fn();
    controller = initViews({ root: document, onShow });
    onShow.mockClear(); // ignore the initial show(DEFAULT_VIEW) call

    navFor('context').click();

    expect(onShow).toHaveBeenCalledWith('context');
  });

  it('updates the h1 heading text per view', () => {
    controller = initViews({ root: document });
    navFor('context').click();
    expect(document.querySelector('.site-header h1').textContent).toBe('Category context');
  });
});

// ---------------------------------------------------------------------------
// Monthly / Yearly (v2 Pass 1) — nav order + view switching
// ---------------------------------------------------------------------------

describe('nav order', () => {
  it('lists nav items in the exact order Upload, Overview, Trends, Monthly, Yearly, Category context', () => {
    const items = Array.from(document.querySelectorAll('nav > *')).map(
      (el) => el.dataset.view ?? el.textContent.trim(),
    );
    expect(items).toEqual([
      'upload', 'overview', 'trends', 'monthly', 'yearly', 'context', 'History', 'Settings',
    ]);
  });
});

describe('switching to the trends view', () => {
  it('shows trends and hides overview/upload/monthly/yearly/context', () => {
    controller = initViews({ root: document });
    navFor('trends').click();

    expect(sectionFor('trends').hidden).toBe(false);
    expect(sectionFor('overview').hidden).toBe(true);
    expect(sectionFor('upload').hidden).toBe(true);
    expect(sectionFor('monthly').hidden).toBe(true);
    expect(sectionFor('yearly').hidden).toBe(true);
    expect(sectionFor('context').hidden).toBe(true);
  });

  it('sets the h1 heading to "Trends"', () => {
    controller = initViews({ root: document });
    navFor('trends').click();
    expect(document.querySelector('.site-header h1').textContent).toBe('Trends');
  });

  it('marks the trends nav item active', () => {
    controller = initViews({ root: document });
    navFor('trends').click();
    expect(navFor('trends').classList.contains('nav-item--active')).toBe(true);
    expect(navFor('overview').classList.contains('nav-item--active')).toBe(false);
  });

  it('calls onShow with "trends"', () => {
    const onShow = vi.fn();
    controller = initViews({ root: document, onShow });
    onShow.mockClear();
    navFor('trends').click();
    expect(onShow).toHaveBeenCalledWith('trends');
  });
});

describe('switching to the monthly view', () => {
  it('shows monthly and hides overview/upload/yearly/context', () => {
    controller = initViews({ root: document });
    navFor('monthly').click();

    expect(sectionFor('monthly').hidden).toBe(false);
    expect(sectionFor('overview').hidden).toBe(true);
    expect(sectionFor('upload').hidden).toBe(true);
    expect(sectionFor('yearly').hidden).toBe(true);
    expect(sectionFor('context').hidden).toBe(true);
  });

  it('sets the h1 heading to "Monthly"', () => {
    controller = initViews({ root: document });
    navFor('monthly').click();
    expect(document.querySelector('.site-header h1').textContent).toBe('Monthly');
  });

  it('marks the monthly nav item active', () => {
    controller = initViews({ root: document });
    navFor('monthly').click();
    expect(navFor('monthly').classList.contains('nav-item--active')).toBe(true);
    expect(navFor('overview').classList.contains('nav-item--active')).toBe(false);
  });

  it('calls onShow with "monthly"', () => {
    const onShow = vi.fn();
    controller = initViews({ root: document, onShow });
    onShow.mockClear();
    navFor('monthly').click();
    expect(onShow).toHaveBeenCalledWith('monthly');
  });
});

describe('switching to the yearly view', () => {
  it('shows yearly and hides overview/upload/monthly/context', () => {
    controller = initViews({ root: document });
    navFor('yearly').click();

    expect(sectionFor('yearly').hidden).toBe(false);
    expect(sectionFor('overview').hidden).toBe(true);
    expect(sectionFor('upload').hidden).toBe(true);
    expect(sectionFor('monthly').hidden).toBe(true);
    expect(sectionFor('context').hidden).toBe(true);
  });

  it('sets the h1 heading to "Yearly"', () => {
    controller = initViews({ root: document });
    navFor('yearly').click();
    expect(document.querySelector('.site-header h1').textContent).toBe('Yearly');
  });

  it('marks the yearly nav item active', () => {
    controller = initViews({ root: document });
    navFor('yearly').click();
    expect(navFor('yearly').classList.contains('nav-item--active')).toBe(true);
    expect(navFor('overview').classList.contains('nav-item--active')).toBe(false);
  });

  it('calls onShow with "yearly"', () => {
    const onShow = vi.fn();
    controller = initViews({ root: document, onShow });
    onShow.mockClear();
    navFor('yearly').click();
    expect(onShow).toHaveBeenCalledWith('yearly');
  });
});

// ---------------------------------------------------------------------------
// Upload controls live under the Upload view, not Overview
// ---------------------------------------------------------------------------

describe('upload controls placement', () => {
  it('#dropzone-commbank and #upload-submit exist under the Upload view', () => {
    const uploadSection = sectionFor('upload');
    expect(uploadSection.querySelector('#dropzone-commbank')).not.toBeNull();
    expect(uploadSection.querySelector('#upload-submit')).not.toBeNull();
  });

  it('#dropzone-commbank and #upload-submit are NOT inside the Overview view', () => {
    const overviewSection = sectionFor('overview');
    expect(overviewSection.querySelector('#dropzone-commbank')).toBeNull();
    expect(overviewSection.querySelector('#upload-submit')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// destroy
// ---------------------------------------------------------------------------

describe('destroy', () => {
  it('removes click listeners so nav clicks no longer switch views', () => {
    controller = initViews({ root: document });
    controller.destroy();

    navFor('upload').click();

    // Still on overview — the destroyed controller no longer reacts to clicks.
    expect(sectionFor('overview').hidden).toBe(false);
    controller = null;
  });
});
