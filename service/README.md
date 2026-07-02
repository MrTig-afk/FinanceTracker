# FinanceTracker — always-on service

How to install the always-on servers so they start automatically on login,
restart on unlock if anything died, and heal themselves if a server crashes.

One Task Scheduler task, **`FinanceTracker`**, runs `service/supervisor.py`
under `pythonw.exe`. The supervisor keeps both local servers alive so
Tailscale Serve can expose them to the phone:

- FastAPI backend on `BACKEND_HOST:BACKEND_PORT` from `.env` (default `0.0.0.0:8010`)
- static PWA (`frontend/dist`) on `127.0.0.1:4173`

Every 15 seconds it probes both ports and relaunches whichever server is not
listening. Server output goes to `logs/backend.log` and `logs/web.log`
(gitignored, never leaves this machine).

**Windowless by design:** `pythonw.exe` is a GUI-subsystem binary, so no
console window ever exists. (`powershell.exe -WindowStyle Hidden`, the old
approach, can still show a window when Windows Terminal is the default
console host — and closing that window killed the backend.)

---

## Windows (Task Scheduler)

### Install / migrate

Open PowerShell (no admin needed for a user-level task) and run:

```powershell
pwsh .\service\register-task.ps1
```

The script:
1. Removes the legacy `FinanceTracker-Backend` / `FinanceTracker-Web` tasks if present.
2. Stops any leftover server processes on ports 8010 / 4173 so the supervisor
   can respawn them windowless.
3. Patches the `__REPO_ROOT__` placeholder in `financetracker.xml` with the real path.
4. Registers the task as `FinanceTracker` under your user account and starts it.

### Verify

```powershell
Get-ScheduledTask -TaskName "FinanceTracker" | Select-Object State
# Expected: State = Running
Get-NetTCPConnection -State Listen -LocalPort 8010,4173
# Expected: both ports listening
```

Then open `http://localhost:8010/status` in a browser — you should see `"status": "ok"`.

### Remove

```powershell
pwsh .\service\register-task.ps1 -Unregister
```

### Task settings

| Setting | Value | Why |
|---|---|---|
| Trigger | LogonTrigger | Starts on login (FR-6) |
| Trigger | SessionStateChangeTrigger (SessionUnlock) | Revives the supervisor on unlock if it died; no-op when already running |
| Action | `pythonw.exe service\supervisor.py` | No console window can ever appear |
| RunLevel | LeastPrivilege | No UAC prompt; keeps running while screen is locked |
| ExecutionTimeLimit | PT0S | Runs indefinitely |
| RestartOnFailure | 3 × PT1M | Auto-restart if the supervisor itself crashes |
| MultipleInstances | IgnoreNew | One instance only |
| StartWhenAvailable | true | Catches missed triggers (e.g. machine was off) |

### Manual foreground runs (dev)

`run-backend.ps1` and `run-web.ps1` still start each server in the foreground
for testing; Task Scheduler no longer uses them. The supervisor itself can
also run in a console: `venv\Scripts\python.exe service\supervisor.py`.

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

- The supervisor and launcher scripts contain **no secrets**.  All sensitive
  config (API keys, Drive credentials, DB path) is loaded from `.env`
  at runtime by python-dotenv inside the Python process.
- The backend binds to `BACKEND_HOST`/`BACKEND_PORT` from `.env` (defaults `0.0.0.0:8010`).
- Access from your phone uses Tailscale.  Add your tailnet origin to `CORS_ALLOW_ORIGINS`
  in `.env` (comma-separated) so the phone PWA is allowed by CORS.
