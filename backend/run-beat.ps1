# Runs Celery beat on Windows/PowerShell - drives schedules (recurring
# collection runs) and snapshot retention (auto-deleting old configs).
# Doesn't run any collection itself, just periodically enqueues work for
# run-worker.ps1's worker to pick up - schedules just sit there and nothing
# ever gets purged without this also running.
#
# Usage: from the backend/ folder, in a separate window from run-backend.ps1
# and run-worker.ps1:
#   .\run-beat.ps1
#
# Needs Redis (e.g. Memurai on Windows - https://www.memurai.com/) reachable
# at REDIS_URL (defaults to redis://localhost:6379/0, same as the backend).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Assert-LastExitCode([string]$step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$step failed (exit code $LASTEXITCODE) - see the output above for the actual error."
    }
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    Assert-LastExitCode "Virtual environment creation"
}

$activate = ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    throw "Could not find $activate - venv creation may have failed."
}
& $activate

Write-Host "Installing/updating dependencies..."
pip install -r requirements.txt
Assert-LastExitCode "pip install"

Write-Host "Starting Celery beat..."
celery -A app.celery_app beat --loglevel=info
