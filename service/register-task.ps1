# register-task.ps1 — install or remove the FinanceTracker Task Scheduler task.
#
# Usage
# -----
#   Install:   pwsh .\service\register-task.ps1
#   Remove:    pwsh .\service\register-task.ps1 -Unregister
#
# Installs ONE task, "FinanceTracker", which runs service/supervisor.py under
# pythonw.exe (no console window) at login and on session unlock.  The
# supervisor keeps both the backend (8010) and the PWA server (4173) alive.
#
# Installing also migrates from the old two-task setup: the legacy
# "FinanceTracker-Backend" / "FinanceTracker-Web" tasks are removed, and any
# leftover server processes on the two ports are stopped so the supervisor can
# respawn them windowless.
#
# This script must be run from PowerShell (pwsh or powershell.exe) with sufficient
# permissions to register a scheduled task for the current user.  No elevation is
# required for a user-level task; however, if you see an "Access Denied" error,
# run PowerShell as Administrator once.
#
# No secrets are stored in the task definition — all sensitive config lives in .env
# and is loaded at runtime by the Python app.

param(
    [switch]$Unregister
)

$TaskName    = "FinanceTracker"
$LegacyTasks = @("FinanceTracker-Backend", "FinanceTracker-Web")
$Ports       = @(8010, 4173)

function Remove-TaskIfPresent($name) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
        Write-Host "Removed scheduled task '$name'."
    }
}

if ($Unregister) {
    Remove-TaskIfPresent $TaskName
    foreach ($legacy in $LegacyTasks) { Remove-TaskIfPresent $legacy }
    exit 0
}

# Migrate: drop the legacy per-server tasks (replaced by the supervisor).
foreach ($legacy in $LegacyTasks) { Remove-TaskIfPresent $legacy }

# Stop any leftover server processes still holding the ports (e.g. started by
# the legacy tasks, possibly attached to a visible console) so the supervisor
# respawns them windowless.
foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($procId in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "Stopped leftover process $procId on port $port."
        } catch {}
    }
}

# Resolve the repository root (parent of the service/ directory).
$RepoRoot = Split-Path -Parent $PSScriptRoot

# Read the XML template and replace the __REPO_ROOT__ placeholder with the real path.
$XmlTemplate = Get-Content -Path (Join-Path $PSScriptRoot "financetracker.xml") -Raw
$XmlPatched  = $XmlTemplate -replace [regex]::Escape("__REPO_ROOT__"), $RepoRoot

Register-ScheduledTask `
    -TaskName $TaskName `
    -Xml $XmlPatched `
    -Force `
    -ErrorAction Stop | Out-Null
Write-Host "FinanceTracker scheduled task '$TaskName' registered successfully."

Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
Write-Host "Task started. Both servers will be listening within a few seconds."
