# FinanceTracker — backend service

How to install the always-on backend so it starts automatically on login and
restarts itself if it crashes.

---

## Windows (Task Scheduler)

### Install

Open PowerShell (no admin needed for a user-level task) and run:

```powershell
pwsh .\service\register-task.ps1
```

The script:
1. Resolves the repository root from its own location.
2. Patches the `__REPO_ROOT__` placeholder in `financetracker.xml` with the real path.
3. Registers the task as `FinanceTracker-Backend` under your user account.

The task will auto-start at next login.  To start it immediately without rebooting:

```powershell
Start-ScheduledTask -TaskName "FinanceTracker-Backend"
```

### Verify

```powershell
Get-ScheduledTask -TaskName "FinanceTracker-Backend" | Select-Object State
# Expected: State = Running
```

Then open `http://localhost:8000/status` in a browser — you should see `"status": "ok"`.

### Remove

```powershell
pwsh .\service\register-task.ps1 -Unregister
```

### Task settings

| Setting | Value | Why |
|---|---|---|
| Trigger | LogonTrigger | Starts on login (FR-6) |
| RunLevel | LeastPrivilege | No UAC prompt; keeps running while screen is locked |
| ExecutionTimeLimit | PT0S | Runs indefinitely |
| RestartOnFailure | 3 × PT1M | Auto-restart on crash |
| MultipleInstances | IgnoreNew | One instance only |
| StartWhenAvailable | true | Catches missed triggers (e.g. machine was off) |

---

## macOS equivalent (launchd LaunchAgent)

The macOS equivalent is a **launchd LaunchAgent plist** placed in
`~/Library/LaunchAgents/`.  Set `RunAtLoad = true` (auto-start when the user
logs in) and `KeepAlive = true` (auto-restart on exit).  A minimal plist
structure:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.financetracker.backend</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/repo/venv/bin/python</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>backend.app:app</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/repo</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/financetracker.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/financetracker.err</string>
</dict>
</plist>
```

Load with `launchctl load ~/Library/LaunchAgents/com.financetracker.backend.plist`.

---

## Notes

- The launcher script (`run-backend.ps1` / the plist) contains **no secrets**.
  All sensitive config (API keys, Drive credentials, DB path) is loaded from `.env`
  at runtime by python-dotenv inside the Python process.
- The backend binds to `BACKEND_HOST`/`BACKEND_PORT` from `.env` (defaults `0.0.0.0:8000`).
- Access from your phone uses Tailscale.  Add your tailnet origin to `CORS_ALLOW_ORIGINS`
  in `.env` (comma-separated) so the phone PWA is allowed by CORS.
