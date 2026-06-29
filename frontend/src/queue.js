/**
 * queue.js — client-side upload queue backed by IndexedDB.
 * PRIVACY: This queue stores the owner's raw CSV Blobs in the browser's own
 * IndexedDB — LOCAL to the owner's device, the same trust boundary as the
 * laptop/phone. Queued items are ONLY ever flushed to the owner's own backend
 * at ${API_BASE}/upload. Blobs never leave the device except to /upload.
 * No secrets in this file.
 */

import { buildUploadForm, postUpload } from './upload.js';

/** Retry interval for the periodic background flush. */
export const RETRY_INTERVAL_MS = 30000;

// ---------------------------------------------------------------------------
// Storage interface:
//   async get(id)          -> item | undefined
//   async put(item)        -> void
//   async delete(id)       -> void
//   async getAll()         -> item[]
// ---------------------------------------------------------------------------

/**
 * In-memory Map-backed store.
 * Used by tests AND as a graceful fallback when `indexedDB` is undefined
 * (private/incognito mode, non-browser environments).
 *
 * @param {Array} seed  Optional pre-populated items.
 * @returns {object}    Storage interface.
 */
export function createMemoryStore(seed = []) {
  const map = new Map(seed.map((item) => [item.id, item]));
  return {
    async get(id) {
      return map.get(id);
    },
    async put(item) {
      map.set(item.id, item);
    },
    async delete(id) {
      map.delete(id);
    },
    async getAll() {
      return [...map.values()];
    },
  };
}

/**
 * Real IndexedDB-backed store.
 * Only used in the browser. NEVER imported or instantiated by unit tests.
 * Falls back to createMemoryStore() when indexedDB is undefined.
 *
 * @param {{ dbName?: string, storeName?: string }} options
 * @returns {object}  Storage interface.
 */
export function createIdbStore({
  dbName = 'financetracker',
  storeName = 'uploadQueue',
} = {}) {
  if (typeof indexedDB === 'undefined') {
    // Graceful fallback — queue works for the session; data lost on reload.
    return createMemoryStore();
  }

  // Lazily opened; shared across all calls via the cached promise.
  let dbPromise = null;

  function getDb() {
    if (!dbPromise) {
      dbPromise = new Promise((resolve, reject) => {
        const req = indexedDB.open(dbName, 1);
        req.onupgradeneeded = (e) => {
          const db = e.target.result;
          if (!db.objectStoreNames.contains(storeName)) {
            db.createObjectStore(storeName, { keyPath: 'id' });
          }
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
      });
    }
    return dbPromise;
  }

  return {
    async get(id) {
      const db = await getDb();
      return new Promise((resolve, reject) => {
        const req = db
          .transaction(storeName, 'readonly')
          .objectStore(storeName)
          .get(id);
        req.onsuccess = () => resolve(req.result);
        req.onerror = (e) => reject(e.target.error);
      });
    },

    async put(item) {
      const db = await getDb();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        tx.objectStore(storeName).put(item);
        tx.oncomplete = () => resolve();
        tx.onerror = (e) => reject(e.target.error);
      });
    },

    async delete(id) {
      const db = await getDb();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        tx.objectStore(storeName).delete(id);
        tx.oncomplete = () => resolve();
        tx.onerror = (e) => reject(e.target.error);
      });
    },

    async getAll() {
      const db = await getDb();
      return new Promise((resolve, reject) => {
        const req = db
          .transaction(storeName, 'readonly')
          .objectStore(storeName)
          .getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror = (e) => reject(e.target.error);
      });
    },
  };
}

// ---------------------------------------------------------------------------
// Queue — item shape:
//   { id: string, commbank?: Blob, westpac?: Blob, enqueuedAt: number }
// ---------------------------------------------------------------------------

/**
 * Create the upload queue.
 *
 * Injectable seams keep unit tests off real IndexedDB, real timers, and real
 * network:
 *   store  — storage interface (default: createIdbStore() with memory fallback)
 *   idFn   — id generator (default: crypto.randomUUID(); inject a counter in tests)
 *   now    — timestamp fn (default: Date.now(); inject a fixed value in tests)
 *
 * @param {{ store?, idFn?, now? }} options
 * @returns {{ enqueue, listQueued, remove, flush, start, stop }}
 */
export function createQueue({
  store,
  idFn = () => crypto.randomUUID(),
  now = () => Date.now(),
} = {}) {
  const _store = store ?? createIdbStore();

  let _started = false;
  let _intervalId = null;
  let _onlineHandler = null;

  /**
   * Persist an item to the queue and return its assigned id.
   * @param {{ commbank?: Blob, westpac?: Blob }} item
   * @returns {Promise<string>}
   */
  async function enqueue(item) {
    const id = idFn();
    const enqueuedAt = now();
    const entry = { id, enqueuedAt };
    if (item.commbank) entry.commbank = item.commbank;
    if (item.westpac) entry.westpac = item.westpac;
    await _store.put(entry);
    return id;
  }

  /**
   * Return all persisted items sorted oldest-first (by enqueuedAt, then id).
   * @returns {Promise<Array>}
   */
  async function listQueued() {
    const all = await _store.getAll();
    return all.slice().sort((a, b) => {
      if (a.enqueuedAt !== b.enqueuedAt) return a.enqueuedAt - b.enqueuedAt;
      if (a.id < b.id) return -1;
      if (a.id > b.id) return 1;
      return 0;
    });
  }

  /**
   * Remove a single item from the queue by id.
   * @param {string} id
   * @returns {Promise<void>}
   */
  async function remove(id) {
    await _store.delete(id);
  }

  /**
   * Attempt to POST each queued item to the backend.
   *
   * Resilience contract:
   *  - Items are processed SEQUENTIALLY.
   *  - A failure for one item NEVER aborts the loop or discards the item.
   *  - An item is deleted ONLY after postFn resolves (server-confirmed success).
   *  - A failing item is left in the store and recorded in `kept`.
   *
   * @param {{ postFn?: (form: FormData) => Promise<object> }} options
   * @returns {Promise<{ sent: string[], kept: string[] }>}
   */
  async function flush({ postFn = (form) => postUpload(form) } = {}) {
    const items = await listQueued();
    const sent = [];
    const kept = [];

    for (const item of items) {
      try {
        const files = {};
        if (item.commbank) files.commbank = item.commbank;
        if (item.westpac) files.westpac = item.westpac;
        const form = buildUploadForm(files);
        await postFn(form);
        // Only delete after the server confirmed success.
        await _store.delete(item.id);
        sent.push(item.id);
      } catch {
        // Leave the item in the store — it will be retried.
        kept.push(item.id);
      }
    }

    return { sent, kept };
  }

  /**
   * Attach the 'online' event listener and start the periodic retry interval.
   * Safe to call multiple times — will not register duplicate listeners.
   */
  function start() {
    if (_started) return; // idempotent
    _started = true;

    const doFlush = () => flush().catch(() => {});

    if (typeof window !== 'undefined') {
      _onlineHandler = doFlush;
      window.addEventListener('online', _onlineHandler);
      _intervalId = setInterval(doFlush, RETRY_INTERVAL_MS);
    }
  }

  /**
   * Remove the 'online' listener and clear the retry interval.
   */
  function stop() {
    if (!_started) return;
    _started = false;

    if (typeof window !== 'undefined') {
      if (_onlineHandler !== null) {
        window.removeEventListener('online', _onlineHandler);
        _onlineHandler = null;
      }
      if (_intervalId !== null) {
        clearInterval(_intervalId);
        _intervalId = null;
      }
    }
  }

  return { enqueue, listQueued, remove, flush, start, stop };
}
