# FinanceTracker

A private, self-hosted personal finance tracker. It turns monthly Commonwealth Bank and Westpac CSV exports into a categorised spending breakdown, shown on a desktop dashboard and an installable phone PWA with a sidebar, a light and dark theme, and an animated donut.

You run it on your own machine, with your own API keys. Nothing here is a hosted service: you bring the keys and the data, and all of the data stays on your hardware.

<p align="center">
  <img src="screenshots/financetracker-overview-dark.png" alt="FinanceTracker overview screen: dark theme, spending donut and category breakdown (synthetic demo data)" width="340">
</p>

*The screenshot uses synthetic demo data generated in code, no real transactions, in keeping with the rest of the repo.*

## Why it exists

There is no free, fully automated way for an individual in Australia to pull CommBank/Westpac data programmatically (the Consumer Data Right requires an accredited recipient in the middle). FinanceTracker accepts a two minute manual step each month, downloading two CSVs, in exchange for zero running cost and total data control. Everything after the download is automated: parsing, deduping, categorising, storing, and charting.

## Using the app

The monthly loop takes about two minutes:

1. Download last month's CSV export from NetBank and from Westpac online banking.
2. Open the dashboard (or the phone app), go to Upload, and drop each file into its bank's slot. A file dropped in the wrong slot still parses: the app detects the bank from the file's contents. `.xlsx` exports work too.
3. Press "Upload and categorise". The backend parses, de-duplicates, sanitises, categorises, and stores every transaction, and the dashboard updates.

Re-uploading an unchanged file is a safe no-op: no categorisation call, no changed output.

What the screens give you:

- **Overview**: an animated donut of spending by category, the month's total counting up in the centre, a legend with per-category bars, per-account opening and closing balances, and a spend-over-time mini chart.
- **Monthly and Yearly**: breakdowns with period-over-period changes.
- **Trends**: per-category spending across recent months, plus a net-position line built from month-end balances.
- **Search**: full-text search across every stored transaction.
- **Transfers**: money moved between your own accounts appears in both banks' exports and would double-count as spending; matched pairs are netted out automatically, each with a "Not a transfer" undo. An unseen-count badge on the nav tells you when new pairs were caught.
- **Budgets and alerts**: set a monthly cap per category in Settings and get a notification at 80% and 100%. Alerts carry only a category name and a percent, never amounts.
- **Subscription watch**: flags a new recurring payment, a price change on an existing one, and an expected income deposit that did not arrive.
- **Corrections**: click any category to drill into the exact transactions behind the number and fix a wrong category on the spot; corrections can optionally teach the categoriser.
- **Small servo spends**: fuel and convenience stops (BP, 7-Eleven, Ampol, Shell, Caltex, Coles Express, Reddy Express) under $10 are usually a coffee, not fuel, so one toggle reclassifies them from Transport to Dining Out for the month. Travel-only fares are never touched. Fully reversible.

The phone PWA is the same app served over your private Tailscale network, with client-side queue-and-retry: an upload made while the laptop is asleep is held and retried until it lands. Notifications arrive as in-app toasts while the app is open and as push notifications when it is backgrounded. Your theme choice is remembered.

## Privacy model

Privacy is the whole point, and it is a property of the design rather than a promise.

- Raw bank data (CSV inputs, the SQLite database, generated Excel files, run logs) never leaves your machine and is never committed to git.
- The only thing sent off-machine is a sanitised tuple: `(row_index, cleaned_description, amount)`. No account numbers, BSBs, card numbers, balances, names, references, or memos.
- A mandatory sanitiser runs before any network call. It strips identifiers, scrubs names and reference codes embedded in descriptions, removes every digit run, and fails closed: if a field cannot be confidently cleaned, the row is dropped rather than risk sending it.
- The only external endpoint is OpenRouter, used for categorisation. It sees a stream of merchant-and-amount pairs that could belong to anyone.
- Every sanitised payload is written to a local audit log so you can verify exactly what left the machine.
- The always-on service also snapshots the database weekly (local copies only, rolling window).

Git safety is enforced by hooks in `.githooks/`: a pre-commit hook blocks committing any bank data or credentials, and a commit-msg hook blocks attribution trailers.

## How it works

One brain, two windows. A single FastAPI backend on your laptop owns all data and logic. The desktop dashboard and phone PWA are stateless views that read from it, so they can never disagree.

```
  Phone PWA  ─┐                        ┌─ Desktop dashboard
              │  HTTP over Tailscale   │  HTTP over localhost
              └──────────┬─────────────┘
                         v
          FastAPI backend (the single brain)
          parse -> dedupe -> sanitise -> categorise -> store -> excel -> drive
                         │
              ┌──────────┴──────────┐
         SQLite (local)        Outbound calls
         transactions,         OpenRouter (sanitised pairs only),
         categories,           Google Drive (Excel workbook)
         fingerprints
```

Categories are a fixed taxonomy of eight: Groceries, Housing, Dining Out, Transport, Entertainment, Subscriptions, Income, Other. Housing covers rent and utilities. Each category carries editable merchant hints (the Categories screen) that steer the categoriser.

Bank formats: both banks normalise to one internal shape of date, description, and signed amount. CommBank's NetBank export has no header row; Westpac's has one, its account-number column is dropped, and its split debit/credit columns are merged. A file that matches neither format is rejected with a clear message instead of failing silently.

Stack: FastAPI and SQLite backend, Vite PWA frontend, Chart.js, openpyxl for Excel, Google Drive API (service account), OpenRouter (free tier) for categorisation, Tailscale for private networking, Windows Task Scheduler for always-on auto-start.

## Running your own copy

Minimal notes for anyone who wants their own instance.

Requirements: **Python 3.11+**, **Node 18+**, and a free **OpenRouter API key** (create it at openrouter.ai and disable prompt logging/training in the account's privacy settings). Optional, and simply inert while unconfigured: a Google Drive service account (Excel backup to Drive), a VAPID key pair (phone push), and Tailscale (phone access).

```bash
# backend
python -m venv venv
# Windows: venv\Scripts\activate    macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # every variable is explained inline in the template
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8010

# frontend (second terminal)
cd frontend
npm install
cp .env.example .env        # optional local overrides
npm run dev
```

- **Phone access**: put `VITE_API_BASE=https://<machine>.<tailnet>.ts.net:8443` in `frontend/.env.production`, run `npm run build`, serve `frontend/dist`, add your tailnet origin to `CORS_ALLOW_ORIGINS` in `.env`, and map two tailnet-only HTTPS listeners with `tailscale serve` (443 to the static server, 8443 to the backend). Add the site to the phone's Home Screen.
- **Always-on (Windows)**: `pwsh .\service\register-task.ps1` registers a single Task Scheduler task that runs a windowless supervisor, keeping both servers alive and taking the weekly database backup. On macOS use a launchd agent, on Linux a systemd user service, pointed at `service/supervisor.py`.
- **Tests**: `python -m pytest` and `cd frontend && npm test`. All test data is synthetic and the suite makes no network calls.

## License

MIT. See [LICENSE](LICENSE).
