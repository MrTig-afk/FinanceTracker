# run-backend.ps1 — launch the FinanceTracker FastAPI backend.
#
# Called by the Windows Task Scheduler task defined in financetracker.xml.
# Also callable directly for manual testing:
#   pwsh .\service\run-backend.ps1
#
# This script contains NO secrets.  All sensitive config (API keys, Drive
# credentials, DB path) is read from .env at runtime by the Python app via
# python-dotenv — never by this script.
#
# Only BACKEND_HOST and BACKEND_PORT are read here so that uvicorn is launched
# on the correct interface and port before the Python process reads .env.

# Resolve the repo root as the parent of the directory containing this script.
$repo = Split-Path -Parent $PSScriptRoot

# Activate the Python virtual environment.
$activate = Join-Path $repo "venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Error "venv not found at '$activate'. Run: python -m venv venv && pip install -r requirements.txt"
    exit 1
}
& $activate

# Read BACKEND_HOST and BACKEND_PORT from .env (if present); use defaults otherwise.
# Only these two non-sensitive values are needed before Python starts.
$bindHost = "0.0.0.0"
$bindPort = "8000"
$envFile = Join-Path $repo ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*BACKEND_HOST\s*=\s*(.+)$') { $bindHost = $Matches[1].Trim() }
        if ($line -match '^\s*BACKEND_PORT\s*=\s*(.+)$') { $bindPort = $Matches[1].Trim() }
    }
}

# Change to repo root so Python's relative-path resolution (./data, ./logs, ./output)
# works correctly.
Set-Location $repo

# Start uvicorn.  All other config is loaded from .env inside the Python process.
python -m uvicorn backend.app:app --host $bindHost --port $bindPort
