/**
 * main.js — entry point / bootstrap.
 * Orchestration only: no transform logic (that lives in summary.js).
 * No secrets here. No transaction data.
 */

import { fetchSummary, fetchStatus, postReclassify, postPushSubscribe, postPushUnsubscribe } from './api.js';
import { createDashboard } from './dashboard.js';
import { createFuelToast } from './fuelToast.js';
import { createQueue } from './queue.js';
import { createUploadController } from './uploadController.js';
import { postUpload } from './upload.js';
import { initTheme } from './theme.js';
import { initViews } from './views.js';
import { createCategoryContext } from './categoryContextController.js';
import { createMonthly } from './monthlyController.js';
import { createYearly } from './yearlyController.js';
import { createTrends } from './trendsController.js';
import { createNetPosition } from './netPositionController.js';
import { createSearch } from './searchController.js';
import { createTransfers } from './transfersController.js';
import { createOverviewTrend } from './overviewTrendController.js';
import { createSettings } from './settingsController.js';
import { createPushController } from './push.js';
import { createToast } from './toast.js';
import { createNotificationBridge } from './notifications.js';
import { createCategoryDrawer } from './categoryDrawer.js';
import { createMobileNav } from './mobileNav.js';
import { createNavBadge } from './navBadge.js';
import { initHealthWatch } from './healthWatch.js';

// ---------------------------------------------------------------------------
// Service worker (FR-3 — installable PWA), PRODUCTION ONLY.
// In dev the SW would serve cached .js/.css and hide code changes, so we never
// register it during development and actively remove any worker + caches a prior
// session left behind. Vite statically replaces import.meta.env.PROD, so only one
// branch survives in each build.
// ---------------------------------------------------------------------------
if (import.meta.env.PROD) {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/sw.js')
        .then(() => {
          // Best-effort periodic "is the laptop up?" probe (Chromium installed
          // PWA only; silent no-op elsewhere). See healthWatch.js.
          initHealthWatch().catch(() => {});
        })
        .catch(() => {
          // SW registration failures are non-fatal (e.g. non-HTTPS dev, privacy mode).
        });
    });
  }
} else {
  // Dev: guarantee no stale-caching worker is active, and drop its caches.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker
      .getRegistrations()
      .then((regs) => regs.forEach((r) => r.unregister()))
      .catch(() => {});
  }
  if (self.caches) {
    caches
      .keys()
      .then((keys) => keys.forEach((k) => caches.delete(k)))
      .catch(() => {});
  }
}

document.addEventListener('DOMContentLoaded', () => {
  createMobileNav({ root: document });
  // onChanged fires after a manual category override; reuse load() so the
  // Overview summary/donut (and mini-trend) re-render with the corrected totals.
  const categoryDrawer = createCategoryDrawer({
    root: document,
    onChanged: () => load(),
  });
  const dash = createDashboard(document, {
    onCategorySelect: (category, meta) => categoryDrawer.open(category, meta),
  });
  const overviewTrend = createOverviewTrend({ root: document });
  const navBadge = createNavBadge({ root: document });
  const fuelToast = createFuelToast(document);
  const statusDot = document.getElementById('status-dot');
  const refreshBtn = document.getElementById('refresh');
  const fuelToggle = document.getElementById('fuel-rule-toggle');

  // Apply the stored theme immediately (avoids a flash of the wrong theme);
  // re-sync the donut border whenever the theme flips.
  initTheme({ root: document, onChange: () => dash.applyChartTheme() });

  // Render a summary object, choosing the empty state when there is no data.
  function renderSummary(summary, opts = {}) {
    const isEmpty =
      summary.count === 0 ||
      !summary.totals ||
      Object.keys(summary.totals).length === 0;

    if (isEmpty) {
      dash.showEmpty();
    } else {
      dash.render(summary, opts);
    }
  }

  // -------------------------------------------------------------------------
  // Dashboard load — reused as the post-upload refresh callback (onUploaded).
  // -------------------------------------------------------------------------
  async function load() {
    try {
      const summary = await fetchSummary();
      renderSummary(summary, { pulse: false });
      navBadge.set(summary.transfers_unseen ?? 0);
    } catch (err) {
      dash.showError(err);
    }

    // Best-effort mini spend-over-time bar — never blocks or errors the donut.
    overviewTrend.load().catch(() => {});

    // Best-effort status dot — never blocks or errors the page.
    fetchStatus()
      .then((status) => {
        if (!statusDot) return;
        if (status && status.status === 'ok') {
          statusDot.style.backgroundColor = '#4CAF50';
          statusDot.title = 'Backend online';
        } else {
          statusDot.style.backgroundColor = '#EF5350';
          statusDot.title = 'Backend offline or unreachable';
        }
      })
      .catch(() => {
        // fetchStatus already returns null on failure; belt-and-suspenders.
      });
  }

  // -------------------------------------------------------------------------
  // View switching — Upload / Overview / Monthly / Yearly / Category context /
  // (History, Settings stay inert). Lazy-create the monthly/yearly/category-
  // context controllers on first switch; re-run the dashboard load() whenever
  // Overview is shown. initViews() shows the default (Overview) view
  // synchronously, which fires onShow('overview') and triggers the initial
  // load() below — no separate call needed. Created before the upload
  // controller so the latter can trigger a view switch after a successful
  // upload (see onUploadSuccess below).
  // -------------------------------------------------------------------------
  let categoryContext = null;
  let monthly = null;
  let yearly = null;
  let trends = null;
  let netPosition = null;
  let search = null;
  let transfers = null;
  let settings = null;

  const views = initViews({
    root: document,
    onShow(view) {
      if (view === 'overview') {
        load();
      } else if (view === 'context') {
        if (!categoryContext) {
          categoryContext = createCategoryContext({ root: document });
        }
        categoryContext.load();
      } else if (view === 'trends') {
        if (!trends) trends = createTrends({ root: document });
        trends.load();
        if (!netPosition) netPosition = createNetPosition({ root: document });
        netPosition.load();
      } else if (view === 'search') {
        if (!search) search = createSearch({ root: document });
        search.load();
      } else if (view === 'transfers') {
        // notifyToast is created later in this same handler, but view switches only
        // happen after DOMContentLoaded finishes, so the binding is initialised.
        if (!transfers) {
          transfers = createTransfers({
            root: document,
            toastFn: (spec) => notifyToast.show(spec),
            onSeen: () => navBadge.clear(),
          });
        }
        transfers.load();
      } else if (view === 'monthly') {
        if (!monthly) monthly = createMonthly({ root: document });
        monthly.load();
      } else if (view === 'yearly') {
        if (!yearly) yearly = createYearly({ root: document });
        yearly.load();
      } else if (view === 'settings') {
        if (!settings) settings = createSettings({ root: document });
        settings.load();
      }
      // 'contact' is static — no controller needed.
    },
  });

  // -------------------------------------------------------------------------
  // Brand (logo + name) acts as a "home" button — clicking it returns to
  // Overview. Keyboard-accessible (Enter/Space) since it is not a native link.
  // -------------------------------------------------------------------------
  const brand = document.querySelector('.sidebar-brand');
  if (brand) {
    brand.setAttribute('role', 'button');
    brand.setAttribute('tabindex', '0');
    brand.setAttribute('aria-label', 'Go to Overview');
    brand.addEventListener('click', () => views.show('overview'));
    brand.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        views.show('overview');
      }
    });
  }

  // -------------------------------------------------------------------------
  // Upload queue (FR-4) — IndexedDB-backed with memory fallback.
  // -------------------------------------------------------------------------
  const queue = createQueue(); // default: createIdbStore() with memory fallback
  createUploadController({
    root: document,
    queue,
    onUploaded: load,
    // Targets Overview BY VIEW NAME ('overview'), not by nav position — so the
    // nav reorder (Upload, Overview, Monthly, Yearly, ...) never breaks this.
    onUploadSuccess: () => views.show('overview'),
  });
  queue.start();

  // Drain anything queued from a previous offline session.
  queue.flush({ postFn: (form) => postUpload(form) }).catch(() => {});

  // -------------------------------------------------------------------------
  // Push notifications (v2 Pass 3 — inert scaffold). Degrades gracefully when
  // unsupported / denied / not configured (placeholder VAPID key) — never throws.
  // -------------------------------------------------------------------------
  createPushController({
    root: document,
    api: { subscribe: postPushSubscribe, unsubscribe: postPushUnsubscribe },
  });

  // When the app is FOCUSED, the service worker relays push payloads to the page
  // (instead of raising an OS notification); show them as an in-app toast. When
  // backgrounded, the SW shows an OS notification instead and this never fires.
  const notifyToast = createToast(document, { regionId: 'notify-toast-region' });
  createNotificationBridge({ toast: notifyToast });

  // -------------------------------------------------------------------------
  // Manual refresh (FR-34 v1: no push — manual refresh button satisfies spec).
  // -------------------------------------------------------------------------
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => load());
  }

  // -------------------------------------------------------------------------
  // Small-fuel-stop rule toggle — apply/revert on the backend, then re-render.
  // On failure, resync the checkbox to the backend's actual state via load().
  // -------------------------------------------------------------------------
  if (fuelToggle) {
    fuelToggle.addEventListener('change', async () => {
      const enabled = fuelToggle.checked;
      try {
        const summary = await postReclassify(enabled);
        renderSummary(summary, { pulse: true });
        navBadge.set(summary.transfers_unseen ?? 0);
        // Confirm the user's toggle action with a transient top-right toast,
        // using the real eligible-count / amount from the reclassify response.
        fuelToast.show(enabled, {
          count: summary.fuel_rule_eligible ?? 0,
          amount: summary.fuel_rule_eligible_amount ?? '0.00',
        });
      } catch (err) {
        dash.showError(err);
        load();
      }
    });
  }
});
