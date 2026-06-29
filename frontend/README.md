# FinanceTracker — Frontend

Read-only dashboard: donut chart of spending by category + per-category totals
table + Net figure + month label. Reads the owner's own backend over localhost
or Tailscale. No secrets belong here.

## Running

```bash
npm install
npm run dev        # serves on http://localhost:5173 (default API: http://localhost:8000)
```

## Testing

```bash
npm test           # vitest run (one-shot, exits)
```

## Building

```bash
npm run build      # output to dist/ (gitignored)
npm run preview    # preview the build locally
```

## Configuration

| Variable           | Default                   | Purpose                                 |
|--------------------|---------------------------|-----------------------------------------|
| `VITE_API_BASE`    | `http://localhost:8000`   | Backend URL (Tailscale host or same-origin) |

Set `VITE_API_BASE` in a local `.env` file (see `.env.example`) when the
backend is on a different host (e.g. Tailscale). Never put secrets here.

## Scope

This view is **read-only** (FR-32 – FR-34). Upload UI and PWA manifest /
service worker are deferred to the next section (§7.1).
