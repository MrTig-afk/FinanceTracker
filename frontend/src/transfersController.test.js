/**
 * transfersController.test.js — DOM wiring tests for transfersController.js.
 *
 * Injected fake fetchFn / untagFn (no real network). All fixtures are SYNTHETIC
 * (invented merchants/amounts), never real data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { createTransfers } from './transfersController.js';
import { formatCurrency } from './summary.js';

// ---------------------------------------------------------------------------
// DOM template — mirrors the Transfers view markup in index.html.
// ---------------------------------------------------------------------------

const TRANSFERS_HTML = `
  <p id="transfers-message" class="message-banner"></p>
  <section id="transfers-card" class="card" hidden>
    <div id="transfers-list" class="cat-drawer-body"></div>
  </section>
`;

// ---------------------------------------------------------------------------
// Synthetic canned responses.
// ---------------------------------------------------------------------------

const CANNED_TRANSFERS = {
  count: 1,
  pairs: [
    {
      id: 7,
      amount: '500.00',
      created_at: '2026-06-02T00:00:00Z',
      out: { id: 1, date: '2026-06-01', description: 'SYNTH XFER OUT', amount: '-500.00', bank: 'commbank' },
      in: { id: 2, date: '2026-06-02', description: 'SYNTH XFER IN', amount: '500.00', bank: 'westpac' },
    },
  ],
};

const EMPTY_TRANSFERS = { count: 0, pairs: [] };

const _EMPTY = 'No transfers detected between your accounts.';
const _LOAD_ERROR = 'Could not load transfers.';
const _UNTAG_ERROR = 'Could not untag this pair.';

const $ = (id) => document.getElementById(id);
const pairs = () => [...$('transfers-list').querySelectorAll('.transfer-pair')];
const flush = () => new Promise((r) => setTimeout(r, 0));

let controller;

beforeEach(() => {
  document.body.innerHTML = TRANSFERS_HTML;
});

afterEach(() => {
  if (controller) {
    controller.destroy();
    controller = null;
  }
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

describe('render', () => {
  it('renders one .transfer-pair with two shared-builder rows and a caption', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    controller = createTransfers({ root: document, fetchFn });
    controller.load();
    await flush();

    expect(pairs().length).toBe(1);
    const card = pairs()[0];
    // Two transaction rows, each via buildRowMain (.cat-drawer-row-main present).
    const rows = card.querySelectorAll('.cat-drawer-row');
    expect(rows.length).toBe(2);
    expect(rows[0].querySelector('.cat-drawer-row-main')).not.toBeNull();
    // Caption names both banks + the formatted amount, with a plain arrow.
    const caption = card.querySelector('.transfer-pair-caption').textContent;
    expect(caption).toContain('CommBank -> Westpac');
    expect(caption).toContain(formatCurrency('500.00'));
    // The untag button is present.
    expect(card.querySelector('.transfer-untag').textContent).toBe('Not a transfer');
  });

  it('shows a count summary line in the message banner', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    controller = createTransfers({ root: document, fetchFn });
    controller.load();
    await flush();
    expect($('transfers-message').textContent).toBe('1 matched pair excluded from spending');
  });

  it('uses the plural noun for multiple pairs', async () => {
    const two = {
      count: 2,
      pairs: [
        CANNED_TRANSFERS.pairs[0],
        {
          ...CANNED_TRANSFERS.pairs[0],
          id: 8,
          out: { ...CANNED_TRANSFERS.pairs[0].out, id: 3 },
          in: { ...CANNED_TRANSFERS.pairs[0].in, id: 4 },
        },
      ],
    };
    const fetchFn = vi.fn().mockResolvedValue(two);
    controller = createTransfers({ root: document, fetchFn });
    controller.load();
    await flush();
    expect(pairs().length).toBe(2);
    expect($('transfers-message').textContent).toBe('2 matched pairs excluded from spending');
  });

  it('labels each leg with its direction and bank', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    controller = createTransfers({ root: document, fetchFn });
    controller.load();
    await flush();

    const labels = [...pairs()[0].querySelectorAll('.transfer-leg-label')].map(
      (el) => el.textContent,
    );
    expect(labels).toEqual(['From CommBank', 'To Westpac']);
  });

  it('shows the list card only while there are pairs (no empty gray box)', async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce(CANNED_TRANSFERS)
      .mockResolvedValueOnce(EMPTY_TRANSFERS);
    controller = createTransfers({ root: document, fetchFn });

    controller.load();
    await flush();
    expect($('transfers-card').hidden).toBe(false);

    controller.load();
    await flush();
    expect($('transfers-card').hidden).toBe(true);
  });

  it('renders descriptions via textContent only (no HTML injection)', async () => {
    const injected = {
      count: 1,
      pairs: [
        {
          id: 9,
          amount: '10.00',
          created_at: '2026-06-02',
          out: { id: 1, date: '2026-06-01', description: '<img src=x onerror=alert(1)>', amount: '-10.00', bank: 'commbank' },
          in: { id: 2, date: '2026-06-02', description: 'SYNTH IN', amount: '10.00', bank: 'westpac' },
        },
      ],
    };
    const fetchFn = vi.fn().mockResolvedValue(injected);
    controller = createTransfers({ root: document, fetchFn });
    controller.load();
    await flush();

    // No <img> element was ever created — the string is inert text.
    expect($('transfers-list').querySelector('img')).toBeNull();
    const desc = pairs()[0].querySelector('.cat-drawer-desc').textContent;
    expect(desc).toBe('<img src=x onerror=alert(1)>');
  });
});

// ---------------------------------------------------------------------------
// Empty + error states
// ---------------------------------------------------------------------------

describe('empty + error states', () => {
  it('shows the empty message when there are no pairs', async () => {
    const fetchFn = vi.fn().mockResolvedValue(EMPTY_TRANSFERS);
    controller = createTransfers({ root: document, fetchFn });
    controller.load();
    await flush();
    expect(pairs().length).toBe(0);
    expect($('transfers-message').textContent).toBe(_EMPTY);
  });

  it('shows a fixed error message when fetchFn rejects (no raw error leaked)', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom SYNTH-SECRET stack'));
    controller = createTransfers({ root: document, fetchFn });
    controller.load();
    await flush();
    expect($('transfers-message').textContent).toBe(_LOAD_ERROR);
    expect($('transfers-message').textContent).not.toContain('SYNTH-SECRET');
    expect(pairs().length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Untag action
// ---------------------------------------------------------------------------

describe('untag', () => {
  it('calls untagFn(pair.id) and reloads on success', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    const untagFn = vi.fn().mockResolvedValue({ ok: true, pair_id: 7, restored: 2 });
    controller = createTransfers({ root: document, fetchFn, untagFn });
    controller.load();
    await flush();

    fetchFn.mockResolvedValueOnce(EMPTY_TRANSFERS); // the reload after untag
    pairs()[0].querySelector('.transfer-untag').click();
    await flush();

    expect(untagFn).toHaveBeenCalledTimes(1);
    expect(untagFn).toHaveBeenCalledWith(7);
    // A reload happened (fetchFn called a second time) and the list is now empty.
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(pairs().length).toBe(0);
    expect($('transfers-message').textContent).toBe(_EMPTY);
  });

  it('toasts where each leg went, mapping null to Uncategorised with the retry note', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    const untagFn = vi.fn().mockResolvedValue({
      ok: true,
      pair_id: 7,
      restored: 2,
      restored_to: { out: 'Groceries', in: null },
    });
    const toastFn = vi.fn();
    controller = createTransfers({ root: document, fetchFn, untagFn, toastFn });
    controller.load();
    await flush();

    fetchFn.mockResolvedValueOnce(EMPTY_TRANSFERS);
    pairs()[0].querySelector('.transfer-untag').click();
    await flush();

    expect(toastFn).toHaveBeenCalledTimes(1);
    const spec = toastFn.mock.calls[0][0];
    expect(spec.title).toBe('Not a transfer');
    expect(spec.body).toContain('CommBank leg -> Groceries');
    expect(spec.body).toContain('Westpac leg -> Uncategorised');
    expect(spec.body).toContain('sorted on the next run');
  });

  it('omits the retry note when both legs restore to real categories', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    const untagFn = vi.fn().mockResolvedValue({
      ok: true,
      pair_id: 7,
      restored: 2,
      restored_to: { out: 'Groceries', in: 'Income' },
    });
    const toastFn = vi.fn();
    controller = createTransfers({ root: document, fetchFn, untagFn, toastFn });
    controller.load();
    await flush();

    fetchFn.mockResolvedValueOnce(EMPTY_TRANSFERS);
    pairs()[0].querySelector('.transfer-untag').click();
    await flush();

    const spec = toastFn.mock.calls[0][0];
    expect(spec.body).toContain('CommBank leg -> Groceries');
    expect(spec.body).toContain('Westpac leg -> Income');
    expect(spec.body).not.toContain('sorted on the next run');
  });

  it('does not toast on a failed untag', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    const untagFn = vi.fn().mockRejectedValue(new Error('boom'));
    const toastFn = vi.fn();
    controller = createTransfers({ root: document, fetchFn, untagFn, toastFn });
    controller.load();
    await flush();

    pairs()[0].querySelector('.transfer-untag').click();
    await flush();
    expect(toastFn).not.toHaveBeenCalled();
  });

  it('re-enables the button and shows a fixed error when untag fails', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    const untagFn = vi.fn().mockRejectedValue(new Error('boom SYNTH-SECRET'));
    controller = createTransfers({ root: document, fetchFn, untagFn });
    controller.load();
    await flush();

    const button = pairs()[0].querySelector('.transfer-untag');
    button.click();
    await flush();

    expect(button.disabled).toBe(false); // re-enabled so the owner can retry
    const caption = pairs()[0].querySelector('.transfer-pair-caption');
    const err = caption.querySelector('[role="alert"]');
    expect(err).not.toBeNull();
    expect(err.textContent).toBe(_UNTAG_ERROR);
    expect(err.textContent).not.toContain('SYNTH-SECRET');
    // No reload occurred on failure.
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// destroy()
// ---------------------------------------------------------------------------

describe('destroy()', () => {
  it('detaches the untag listener so a later click does not call untagFn', async () => {
    const fetchFn = vi.fn().mockResolvedValue(CANNED_TRANSFERS);
    const untagFn = vi.fn().mockResolvedValue({ ok: true });
    controller = createTransfers({ root: document, fetchFn, untagFn });
    controller.load();
    await flush();

    const button = pairs()[0].querySelector('.transfer-untag');
    controller.destroy();
    controller = null;

    button.click();
    await flush();
    expect(untagFn).not.toHaveBeenCalled();
  });
});
