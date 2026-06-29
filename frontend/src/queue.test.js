/**
 * queue.test.js — unit tests for queue.js.
 * No real IndexedDB — createMemoryStore() is injected for all tests.
 * No real timers for the interval path (tested via the online event instead).
 * No real network — postFn is injected or fetch is stubbed where needed.
 * All fixtures are synthetic, built inline. No real transaction data.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createMemoryStore, createQueue, RETRY_INTERVAL_MS } from './queue.js';

// ---------------------------------------------------------------------------
// Synthetic CSV Blob — no real transaction data, no real CSV files.
// ---------------------------------------------------------------------------

const SYNTH_CSV = 'date,amount,desc\n01-06-2026,-5.00,SYNTH\n';

function csvBlob() {
  // Plain Blob — no filename. isCsvFile checks MIME type for unnamed Blobs.
  return new Blob([SYNTH_CSV], { type: 'text/csv' });
}

// ---------------------------------------------------------------------------
// Counter-based id factory for deterministic ids in tests.
// ---------------------------------------------------------------------------

function makeIdFn() {
  let n = 0;
  return () => `test-id-${++n}`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FIXED_NOW = 1_000_000;

function makeResolvePostFn() {
  return vi.fn().mockResolvedValue({ processed: 1, errors: [] });
}

function makeRejectPostFn() {
  return vi.fn().mockRejectedValue(new Error('network'));
}

// ---------------------------------------------------------------------------
// RETRY_INTERVAL_MS constant
// ---------------------------------------------------------------------------

describe('RETRY_INTERVAL_MS', () => {
  it('is exported and equals 30000 ms', () => {
    expect(RETRY_INTERVAL_MS).toBe(30_000);
  });
});

// ---------------------------------------------------------------------------
// createMemoryStore — basic interface
// ---------------------------------------------------------------------------

describe('createMemoryStore', () => {
  it('getAll returns empty array when seeded with nothing', async () => {
    const store = createMemoryStore();
    expect(await store.getAll()).toEqual([]);
  });

  it('put + get round-trips an item', async () => {
    const store = createMemoryStore();
    const item = { id: 'x', enqueuedAt: 1 };
    await store.put(item);
    expect(await store.get('x')).toEqual(item);
  });

  it('delete removes the item', async () => {
    const store = createMemoryStore();
    await store.put({ id: 'y', enqueuedAt: 2 });
    await store.delete('y');
    expect(await store.get('y')).toBeUndefined();
  });

  it('getAll returns all stored items', async () => {
    const store = createMemoryStore();
    await store.put({ id: 'a', enqueuedAt: 1 });
    await store.put({ id: 'b', enqueuedAt: 2 });
    const all = await store.getAll();
    expect(all).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// enqueue — persists to injected store with deterministic id and timestamp
// ---------------------------------------------------------------------------

describe('enqueue', () => {
  it('persists the item to the injected store (visible via listQueued)', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    await queue.enqueue({ commbank: csvBlob() });

    const items = await queue.listQueued();
    expect(items).toHaveLength(1);
  });

  it('returns the id assigned by the injected idFn', async () => {
    const store = createMemoryStore();
    const idFn = makeIdFn();
    const queue = createQueue({ store, idFn, now: () => FIXED_NOW });

    const id = await queue.enqueue({ commbank: csvBlob() });
    expect(id).toBe('test-id-1');
  });

  it('records enqueuedAt from the injected now function', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    await queue.enqueue({ commbank: csvBlob() });

    const [item] = await queue.listQueued();
    expect(item.enqueuedAt).toBe(FIXED_NOW);
  });

  it('stores the commbank blob when only commbank is supplied', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    const blob = csvBlob();

    await queue.enqueue({ commbank: blob });

    const [item] = await queue.listQueued();
    expect(item.commbank).toBe(blob);
    expect(item.westpac).toBeUndefined();
  });

  it('stores both blobs when both bank files are supplied', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    const cb = csvBlob();
    const wb = csvBlob();

    await queue.enqueue({ commbank: cb, westpac: wb });

    const [item] = await queue.listQueued();
    expect(item.commbank).toBe(cb);
    expect(item.westpac).toBe(wb);
  });

  it('each enqueue call uses successive ids from idFn', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    const id1 = await queue.enqueue({ commbank: csvBlob() });
    const id2 = await queue.enqueue({ westpac: csvBlob() });

    expect(id1).toBe('test-id-1');
    expect(id2).toBe('test-id-2');
  });
});

// ---------------------------------------------------------------------------
// listQueued — ordering
// ---------------------------------------------------------------------------

describe('listQueued', () => {
  it('returns items sorted oldest-first by enqueuedAt', async () => {
    const store = createMemoryStore();
    let t = 0;
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => ++t });

    await queue.enqueue({ commbank: csvBlob() }); // enqueuedAt = 1
    await queue.enqueue({ commbank: csvBlob() }); // enqueuedAt = 2

    const items = await queue.listQueued();
    expect(items[0].enqueuedAt).toBe(1);
    expect(items[1].enqueuedAt).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// flush — happy path: postFn resolves
// ---------------------------------------------------------------------------

describe('flush — postFn resolves', () => {
  it('returns { sent: [id], kept: [] }', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    const postFn = makeResolvePostFn();

    const id = await queue.enqueue({ commbank: csvBlob() });
    const result = await queue.flush({ postFn });

    expect(result.sent).toContain(id);
    expect(result.kept).toHaveLength(0);
  });

  it('removes the item from the store after success', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    const postFn = makeResolvePostFn();

    await queue.enqueue({ commbank: csvBlob() });
    await queue.flush({ postFn });

    expect(await queue.listQueued()).toHaveLength(0);
  });

  it('calls postFn with a FormData argument', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    const postFn = makeResolvePostFn();

    await queue.enqueue({ commbank: csvBlob() });
    await queue.flush({ postFn });

    expect(postFn).toHaveBeenCalledOnce();
    expect(postFn.mock.calls[0][0]).toBeInstanceOf(FormData);
  });
});

// ---------------------------------------------------------------------------
// flush — failure path: postFn rejects
// ---------------------------------------------------------------------------

describe('flush — postFn rejects', () => {
  it('returns { sent: [], kept: [id] }', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    const postFn = makeRejectPostFn();

    const id = await queue.enqueue({ commbank: csvBlob() });
    const result = await queue.flush({ postFn });

    expect(result.sent).toHaveLength(0);
    expect(result.kept).toContain(id);
  });

  it('leaves the item in the store after failure', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    const postFn = makeRejectPostFn();

    await queue.enqueue({ commbank: csvBlob() });
    await queue.flush({ postFn });

    expect(await queue.listQueued()).toHaveLength(1);
  });

  it('does NOT delete the item until postFn resolves (never-delete-on-reject)', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });
    // First flush fails; second flush succeeds.
    const postFn = vi.fn()
      .mockRejectedValueOnce(new Error('first failure'))
      .mockResolvedValueOnce({ processed: 1 });

    await queue.enqueue({ commbank: csvBlob() });

    await queue.flush({ postFn });
    expect(await queue.listQueued()).toHaveLength(1); // still there

    await queue.flush({ postFn });
    expect(await queue.listQueued()).toHaveLength(0); // gone after success
  });
});

// ---------------------------------------------------------------------------
// flush — mixed: one item fails, another succeeds (loop must not abort)
// ---------------------------------------------------------------------------

describe('flush — mixed success/failure', () => {
  it('only the failed item remains after flush', async () => {
    const store = createMemoryStore();
    let idCounter = 0;
    const queue = createQueue({
      store,
      idFn: () => `id-${++idCounter}`,
      now: () => FIXED_NOW,
    });

    // Enqueue two items; postFn rejects for first, resolves for second.
    const idA = await queue.enqueue({ commbank: csvBlob() }); // id-1
    const idB = await queue.enqueue({ westpac: csvBlob() });  // id-2

    const postFn = vi.fn()
      .mockRejectedValueOnce(new Error('A failed'))
      .mockResolvedValueOnce({ processed: 1 });

    const result = await queue.flush({ postFn });

    expect(result.kept).toContain(idA);
    expect(result.sent).toContain(idB);

    const remaining = await queue.listQueued();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].id).toBe(idA);
  });

  it('both items are attempted (loop does not abort on first failure)', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    await queue.enqueue({ commbank: csvBlob() });
    await queue.enqueue({ westpac: csvBlob() });

    const postFn = vi.fn()
      .mockRejectedValueOnce(new Error('first'))
      .mockResolvedValueOnce({});

    await queue.flush({ postFn });

    // postFn must have been called twice regardless of the first failure.
    expect(postFn).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// Persistence: new queue instance reading the same store sees enqueued items
// ---------------------------------------------------------------------------

describe('persistence across queue instances (same store)', () => {
  it('queue2 created with the same store sees items enqueued by queue1', async () => {
    // Simulates a page reload: queue1 enqueues, then queue2 picks up the store.
    const sharedStore = createMemoryStore();

    const queue1 = createQueue({
      store: sharedStore,
      idFn: makeIdFn(),
      now: () => FIXED_NOW,
    });
    await queue1.enqueue({ commbank: csvBlob() });

    // queue2 is a new instance reading the same in-memory store.
    const queue2 = createQueue({
      store: sharedStore,
      idFn: makeIdFn(),
      now: () => FIXED_NOW,
    });
    const items = await queue2.listQueued();
    expect(items).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// remove — deletes a specific item by id
// ---------------------------------------------------------------------------

describe('remove', () => {
  it('removes only the specified item', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    const id1 = await queue.enqueue({ commbank: csvBlob() });
    await queue.enqueue({ westpac: csvBlob() });

    await queue.remove(id1);

    const remaining = await queue.listQueued();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].id).not.toBe(id1);
  });
});

// ---------------------------------------------------------------------------
// start / stop — online event registration
// ---------------------------------------------------------------------------

describe('start / stop — online event', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('dispatching the online event after start() triggers flush and drains the queue', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    // Enqueue a valid CSV blob so flush has something to send.
    await queue.enqueue({ commbank: csvBlob() });

    // Stub fetch so the default postFn (postUpload) succeeds.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ processed: 1, errors: [] }),
    }));

    queue.start();
    window.dispatchEvent(new Event('online'));

    // Allow the async flush chain to settle through the microtask queue.
    await new Promise((resolve) => setTimeout(resolve, 50));

    const remaining = await queue.listQueued();
    expect(remaining).toHaveLength(0);

    queue.stop();
  });

  it('double start() registers only one online handler (idempotent)', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    let flushCalls = 0;
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => {
      flushCalls++;
      return { ok: true, status: 200, json: async () => ({}) };
    }));

    // Pre-load an item so flush has something to do.
    await queue.enqueue({ commbank: csvBlob() });

    queue.start();
    queue.start(); // second call must be a no-op

    flushCalls = 0; // reset counter after setup
    window.dispatchEvent(new Event('online'));

    await new Promise((resolve) => setTimeout(resolve, 50));

    // A single online event must trigger at most one flush cycle.
    // With one item, postFn (fetch) should be called at most once.
    expect(flushCalls).toBeLessThanOrEqual(1);

    queue.stop();
  });

  it('stop() removes the online listener (no flush after stop)', async () => {
    const store = createMemoryStore();
    const queue = createQueue({ store, idFn: makeIdFn(), now: () => FIXED_NOW });

    let called = false;
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => {
      called = true;
      return { ok: true, status: 200, json: async () => ({}) };
    }));

    await queue.enqueue({ commbank: csvBlob() });

    queue.start();
    queue.stop(); // immediately detach

    window.dispatchEvent(new Event('online'));
    await new Promise((resolve) => setTimeout(resolve, 50));

    // fetch must not have been called because the handler was removed.
    expect(called).toBe(false);
  });
});
