# register-task.ps1 — install or remove the FinanceTracker Task Scheduler task.
#
# Usage
# -----
#   Install:   pwsh .\service\register-task.ps1
#   Remove:    pwsh .\service\register-task.ps1 -Unregister
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

$TaskName = "FinanceTracker-Backend"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "FinanceTracker scheduled task '$TaskName' removed."
    exit 0
}

# Resolve the repository root (parent of the service/ directory).
$RepoRoot = Split-Path -Parent $PSScriptRoot

# Read the XML template and replace the __REPO_ROOT__ placeholder with the real path.
$XmlTemplate = Get-Content -Path (Join-Path $PSScriptRoot "financetracker.xml") -Raw
$XmlPatched  = $XmlTemplate -replace [regex]::Escape("__REPO_ROOT__"), $RepoRoot

# Write the patched XML to a temporary file as UTF-16 LE (required by schtasks.exe /XML).
$TempXml = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), ".xml")
[System.IO.File]::WriteAllText(
    $TempXml,
    $XmlPatched,
    [System.Text.Encoding]::Unicode   # UTF-16 LE
)

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Xml (Get-Content $TempXml -Raw) `
        -Force `
        -ErrorAction Stop | Out-Null
    Write-Host "FinanceTracker scheduled task '$TaskName' registered successfully."
    Write-Host "It will auto-start at your next login.  To start it now:"
    Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
}
finally {
    Remove-Item $TempXml -ErrorAction SilentlyContinue
}
