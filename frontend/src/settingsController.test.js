/**
 * settingsController.test.js — DOM wiring tests for settingsController.js (Feature E).
 * jsdom provides the DOM. No real network — a fake `api` is injected.
 * All fixtures are SYNTHETIC (invented descriptions/categories) — never real
 * transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createSettings } from './settingsController.js';

// ---------------------------------------------------------------------------
// Minimal settings-view markup mirroring the contract in index.html.
// ---------------------------------------------------------------------------

const SETTINGS_HTML = `
  <div id="settings-notifications"></div>
  <p id="settings-notifications-status" role="status"></p>

  <a id="settings-backup-link" href="#" download>Download backup (CSV)</a>
  <input id="settings-reset-input" type="text" />
  <button id="settings-reset-btn" type="button" disabled>Reset all data</button>
  <p id="settings-reset-status" role="status"></p>

  <p id="settings-categoriser-summary">Loading...</p>
  <button id="settings-categoriser-test" type="button">Test OpenRouter</button>
  <p id="settings-categoriser-test-status" role="status"></p>
  <button id="settings-categoriser-retry" type="button">Retry uncategorised</button>
  <p id="settings-categoriser-retry-status" role="status"></p>

  <input id="settings-corrections-toggle" type="checkbox" />
  <div id="settings-corrections-list"></div>
  <p id="settings-corrections-status" role="status"></p>
`;

// SYNTHETIC settings + corrections fixtures.
function synthSettings() {
  return {
    corrections_enabled: true,
    notifications: {
      processed: true,
      processed_recovered: false,
      categorisation_failed: true,
      categorisation_recovered: false,
      parse_error: true,
      drive_backup_failed: false,
      duplicate_noop: false,
      generic_error: true,
      monthly_reminder: false,
    },
  };
}

function synthCorrections() {
  return {
    enabled: true,
    corrections: [
      { id: 1, cleaned_description: 'SYNTH CAFE', category: 'Dining Out', created_at: '2026-06-01' },
      { id: 2, cleaned_description: 'SYNTH RAIL', category: 'Transport', created_at: '2026-06-02' },
    ],
  };
}

function makeApi(overrides = {}) {
  return {
    getSettings: vi.fn().mockResolvedValue(synthSettings()),
    putSettings: vi.fn().mockResolvedValue(synthSettings()),
    getCorrections: vi.fn().mockResolvedValue(synthCorrections()),
    deleteCorrection: vi.fn().mockResolvedValue({ ok: true, removed: 1 }),
    getCategoriserStatus: vi
      .fn()
      .mockResolvedValue({ configured: true, uncategorised_count: 3 }),
    postCategoriserTest: vi
      .fn()
      .mockResolvedValue({ configured: true, reachable: true, rate_limited: false, detail: '' }),
    postCategoriserRetry: vi
      .fn()
      .mockResolvedValue({ ok: true, categorised: 2, remaining: 1 }),
    postReset: vi.fn().mockResolvedValue({ ok: true, cleared: {} }),
    transactionsCsvUrl: vi.fn().mockReturnValue('http://localhost:8000/export/transactions.csv'),
    ...overrides,
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

let controller;

beforeEach(() => {
  document.body.innerHTML = SETTINGS_HTML;
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

describe('notification toggles', () => {
  it('renders one toggle per notification type reflecting the fetched settings', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const toggles = document.querySelectorAll('.settings-notif-toggle');
    expect(toggles.length).toBe(9);

    const byType = {};
    for (const t of toggles) byType[t.dataset.type] = t.checked;
    expect(byType.processed).toBe(true);
    expect(byType.processed_recovered).toBe(false);
    expect(byType.monthly_reminder).toBe(false);
  });

  it('calls putSettings with the single-type partial when a toggle changes', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const toggle = document.querySelector('.settings-notif-toggle[data-type="monthly_reminder"]');
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    await tick();

    expect(api.putSettings).toHaveBeenCalledWith({ notifications: { monthly_reminder: true } });
  });

  it('reverts the toggle and shows an error line when the save fails', async () => {
    const api = makeApi({ putSettings: vi.fn().mockRejectedValue(new Error('boom')) });
    controller = createSettings({ root: document, api });
    await controller.load();

    const toggle = document.querySelector('.settings-notif-toggle[data-type="processed"]');
    expect(toggle.checked).toBe(true);
    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    await tick();

    expect(toggle.checked).toBe(true); // reverted
    expect(document.getElementById('settings-notifications-status').textContent).not.toBe('');
  });
});

// ---------------------------------------------------------------------------
// Learned corrections
// ---------------------------------------------------------------------------

describe('learned corrections', () => {
  it('reflects the corrections_enabled opt-in from fetched settings', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    expect(document.getElementById('settings-corrections-toggle').checked).toBe(true);
  });

  it('calls putSettings({corrections_enabled}) when the opt-in changes', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const optin = document.getElementById('settings-corrections-toggle');
    optin.checked = false;
    optin.dispatchEvent(new Event('change', { bubbles: true }));
    await tick();

    expect(api.putSettings).toHaveBeenCalledWith({ corrections_enabled: false });
  });

  it('renders one row per correction with the description and category', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const rows = document.querySelectorAll('.settings-correction-row');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('SYNTH CAFE');
    expect(rows[0].textContent).toContain('Dining Out');
  });

  it('Remove calls deleteCorrection with the row id and re-renders', async () => {
    const api = makeApi();
    // Second fetch (after delete) returns a shorter list.
    api.getCorrections
      .mockResolvedValueOnce(synthCorrections())
      .mockResolvedValueOnce({
        enabled: true,
        corrections: [
          { id: 2, cleaned_description: 'SYNTH RAIL', category: 'Transport', created_at: '2026-06-02' },
        ],
      });
    controller = createSettings({ root: document, api });
    await controller.load();

    const removeBtn = document.querySelector('.settings-correction-remove');
    removeBtn.click();
    await tick();

    expect(api.deleteCorrection).toHaveBeenCalledWith(1);
    expect(document.querySelectorAll('.settings-correction-row').length).toBe(1);
  });

  it('shows an empty state when there are no corrections', async () => {
    const api = makeApi({
      getCorrections: vi.fn().mockResolvedValue({ enabled: false, corrections: [] }),
    });
    controller = createSettings({ root: document, api });
    await controller.load();

    expect(document.querySelector('.settings-corrections-empty')).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Categoriser health
// ---------------------------------------------------------------------------

describe('categoriser health', () => {
  it('renders the configured flag and uncategorised count on load', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const summary = document.getElementById('settings-categoriser-summary').textContent;
    expect(summary).toContain('Configured: yes');
    expect(summary).toContain('3');
  });

  it('renders "Reachable" when Test returns a reachable connection', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    document.getElementById('settings-categoriser-test').click();
    await tick();

    expect(document.getElementById('settings-categoriser-test-status').textContent).toBe('Reachable');
  });

  it('renders the rate-limited message when Test reports throttling', async () => {
    const api = makeApi({
      postCategoriserTest: vi
        .fn()
        .mockResolvedValue({ configured: true, reachable: false, rate_limited: true, detail: '' }),
    });
    controller = createSettings({ root: document, api });
    await controller.load();

    document.getElementById('settings-categoriser-test').click();
    await tick();

    expect(document.getElementById('settings-categoriser-test-status').textContent).toContain(
      'Rate limited',
    );
  });

  it('renders "Sorted N, M remaining" when Retry succeeds', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    document.getElementById('settings-categoriser-retry').click();
    await tick();

    expect(document.getElementById('settings-categoriser-retry-status').textContent).toBe(
      'Sorted 2, 1 remaining',
    );
  });

  it('shows a visible error line when Test rejects (no throw)', async () => {
    const api = makeApi({ postCategoriserTest: vi.fn().mockRejectedValue(new Error('down')) });
    controller = createSettings({ root: document, api });
    await controller.load();

    document.getElementById('settings-categoriser-test').click();
    await tick();

    expect(document.getElementById('settings-categoriser-test-status').textContent).not.toBe('');
  });
});

// ---------------------------------------------------------------------------
// Data & backup (reset danger zone)
// ---------------------------------------------------------------------------

describe('reset danger zone', () => {
  it('points the backup anchor at the CSV export URL with a download attribute', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const link = document.getElementById('settings-backup-link');
    expect(link.getAttribute('href')).toContain('/export/transactions.csv');
    expect(link.hasAttribute('download')).toBe(true);
  });

  it('keeps the reset button disabled until RESET is typed exactly', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const input = document.getElementById('settings-reset-input');
    const btn = document.getElementById('settings-reset-btn');
    expect(btn.disabled).toBe(true);

    input.value = 'reset';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    expect(btn.disabled).toBe(true);

    input.value = 'RESET';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    expect(btn.disabled).toBe(false);
  });

  it('calls postReset("RESET") and shows a refresh prompt on success', async () => {
    const api = makeApi();
    controller = createSettings({ root: document, api });
    await controller.load();

    const input = document.getElementById('settings-reset-input');
    input.value = 'RESET';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('settings-reset-btn').click();
    await tick();

    expect(api.postReset).toHaveBeenCalledWith('RESET');
    expect(document.getElementById('settings-reset-status').textContent.toLowerCase()).toContain(
      'refresh',
    );
  });

  it('shows an error line and does not throw when postReset rejects', async () => {
    const api = makeApi({ postReset: vi.fn().mockRejectedValue(new Error('nope')) });
    controller = createSettings({ root: document, api });
    await controller.load();

    const input = document.getElementById('settings-reset-input');
    input.value = 'RESET';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('settings-reset-btn').click();
    await tick();

    expect(document.getElementById('settings-reset-status').textContent).not.toBe('');
  });
});

// ---------------------------------------------------------------------------
// Failure handling — load() never throws
// ---------------------------------------------------------------------------

describe('load() resilience', () => {
  it('does not throw and shows error lines when every fetch rejects', async () => {
    const api = makeApi({
      getSettings: vi.fn().mockRejectedValue(new Error('x')),
      getCategoriserStatus: vi.fn().mockRejectedValue(new Error('x')),
      getCorrections: vi.fn().mockRejectedValue(new Error('x')),
    });
    controller = createSettings({ root: document, api });

    await expect(controller.load()).resolves.toBeUndefined();

    expect(document.getElementById('settings-notifications-status').textContent).not.toBe('');
    expect(document.getElementById('settings-corrections-status').textContent).not.toBe('');
    expect(document.getElementById('settings-categoriser-summary').textContent).toContain(
      'Could not load',
    );
  });
});
