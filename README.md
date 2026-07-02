# FinanceTracker

A private, self-hosted personal finance tracker. It turns monthly Commonwealth Bank and Westpac CSV exports into a categorised spending breakdown, shown on a desktop dashboard and an installable phone PWA with a sidebar, a light and dark theme, and an animated donut.

You run it on your own machine, with your own API keys. Nothing here is a hosted service: this repo is the scaffolding. You bring the keys and the data, and all of the data stays on your hardware.

## Why it exists

There is no free, fully automated way for an individual in Australia to pull CommBank/Westpac data programmatically (the Consumer Data Right requires an accredited recipient in the middle). FinanceTracker accepts a two minute manual step each month, downloading two CSVs, in exchange for zero running cost and total data control. Everything after the download is automated: parsing, deduping, categorising, storing, and charting.

## Privacy model

Privacy is the whole point, and it is a property of the design rather than a promise.

- Raw bank data (CSV inputs, the SQLite database, generated Excel files, run logs) never leaves your machine and is never committed to git.
- The only thing sent off-machine is a sanitised tuple: `(row_index, cleaned_description, amount)`. No account numbers, BSBs, card numbers, balances, names, references, or memos.
- A mandatory sanitiser runs before any network call. It strips identifiers, regex-scrubs names and reference codes embedded in descriptions, removes every digit run, and fails closed: if a field cannot be confidently cleaned, the row is dropped rather than risk sending it.
- The only external endpoint is OpenRouter, used for categorisation. It sees a stream of merchant-and-amount pairs that could belong to anyone.
- Every sanitised payload is written to a local audit log so you can verify exactly what left the machine.

Git safety is enforced by hooks in `.githooks/`: a pre-commit hook blocks committing any bank data or credentials, and a commit-msg hook blocks attribution trailers.

## Architecture

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

## Stack

FastAPI and SQLite backend, Vite PWA frontend (desktop dashboard and phone), Chart.js for the donut chart, openpyxl for Excel, Google Drive API (service account) for the workbook, OpenRouter (free tier) for categorisation, Tailscale for private networking, Windows Task Scheduler for always-on auto-start.

## Repository layout

```
backend/
  data_source/     per-bank CSV parsers (CommBank, Westpac profiles)
  idempotency/     file and transaction fingerprinting
  sanitiser/       reduce to (index, clean description, amount); fail closed
  analyser/        OpenRouter client (default plus fallback model)
  store/           SQLite access layer and taxonomy
  excel_builder/   openpyxl workbook
  drive_uploader/  Google Drive service-account upload
  app.py           FastAPI endpoints (/upload, /status, /summary)
  pipeline.py      orchestration
frontend/          Vite PWA: sidebar shell, light/dark theming, animated donut, upload UI, queue-and-retry
service/           Windows Task Scheduler auto-start scripts
```

## Getting started

Prerequisites: Python 3.11+, Node 18+, and a free OpenRouter API key. A Google Drive service account is optional (Excel upload is config gated and is skipped if not configured).

### 1. Backend

```bash
python -m venv venv
# Windows:  venv\Scripts\activate
# macOS/Linux:  source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill in the values (see Configuration below)

python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env      # optional: override VITE_API_BASE if the backend is elsewhere
npm run dev
```

### 3. Use it

Open the dashboard, upload your CommBank and Westpac CSVs, and the backend parses, sanitises, categorises, stores, and returns a breakdown. Re-running on unchanged files is a no-op (no categorisation call, no changed output).

## Dashboard

The interface is an app shell with a sidebar and a light and dark theme toggle (your choice is remembered). The Overview screen shows:

- An animated donut of spending by category, with the month's total counting up in the centre and a legend whose bars fill in per category.
- A "small servo spends" toggle. Fuel and convenience stops (BP, 7-Eleven, Ampol, Shell, Caltex, Coles Express, Reddy Express) under $10 are usually a coffee or a snack, not fuel, so one switch reclassifies those from Transport to Dining Out for the month. Anything over $10 stays Transport, and travel-only fares (Opal, Myki, SkyBus) are never touched. The change is saved locally and is fully reversible.

The phone PWA is the same view over Tailscale, with client-side queue-and-retry, so an upload made while the laptop is asleep is held and retried until it lands.

## Configuration

All backend config lives in `.env` (gitignored). Copy `.env.example` and fill it in. The important values:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter key (free tier). Disable data retention in your OpenRouter account. |
| `OPENROUTER_MODEL` | Default free model slug. Copy the exact slug from the model's page on openrouter.ai. |
| `OPENROUTER_FALLBACK_MODEL` | Fallback free model, used on error, 429, or unparseable JSON. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to a Drive service-account key file (optional). Upload is skipped if absent. |
| `DRIVE_FOLDER_ID` | Target Drive folder for the workbook (optional). |
| `SQLITE_PATH`, `INBOX_DIR`, `OUTPUT_DIR`, `LOG_DIR` | Local paths, all gitignored. |
| `BACKEND_HOST`, `BACKEND_PORT` | Bind address and port. |

The LLM model is config, not code. Swap models by editing `.env`, no code change.

Frontend config is non-secret. The only value is `VITE_API_BASE` (defaults to `http://localhost:8000`). Never put a key or credential in any frontend file.

## Bank CSV formats

Both banks normalise to one internal shape: `date`, `description`, `amount` (signed, debit negative, credit positive).

- CommBank (NetBank desktop export): no header row, columns are `date, amount (signed), description, balance`.
- Westpac: header row present, the leading account-number column is dropped, and split debit/credit columns are merged into one signed amount.

Format knowledge lives in per-bank profiles, so a wrong assumption is a one-place fix. You do not have to match a file to the right upload box either: the app detects which bank a CSV is from by its contents, so a file dropped in the wrong slot still parses correctly, and a file that matches neither format is rejected with a clear message instead of failing silently.

## Categories

A fixed, editable taxonomy: Groceries, Utilities, Rent, Dining Out, Transport, Entertainment, Subscriptions, Income, Other.

## Tests

```bash
# backend
python -m pytest

# frontend
cd frontend && npm test
```

Tests use synthetic data generated in code, never real transactions. The suite makes no live network calls: the OpenRouter client is mocked.

## Always-on service (Windows)

`service/` contains a Task Scheduler definition and a small supervisor (`service/supervisor.py`, run under `pythonw.exe` so no console window appears) that keeps the backend and the built PWA server running: it starts on login and on unlock, and relaunches either server if it stops. Install it once with `pwsh .\service\register-task.ps1` (remove with `-Unregister`). On macOS or Linux you would use launchd or systemd instead.

## Scope and roadmap

This is v1: the core upload to breakdown loop, plus the redesigned dashboard, the small-fuel-stop reclassification toggle, and content-based bank detection. Planned next is a "category context" screen where you keep per-category merchant hints that get prepended to the categorisation prompt. Later versions add a yearly and month-over-month history view, category trend charts, phone push notifications, and budget alerts.

## License

MIT. See [LICENSE](LICENSE).
