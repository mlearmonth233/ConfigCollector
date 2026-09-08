# Sets up (if needed) and runs the ConfigCollector backend on Windows/PowerShell.
# Usage: from the backend/ folder, run:  .\run-backend.ps1
#
# CELERY_TASK_ALWAYS_EAGER=true runs collection jobs synchronously in-process
# instead of dispatching to a Celery worker over Redis - handy for local
# testing without standing up the full docker-compose stack.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# $ErrorActionPreference only governs PowerShell-native errors - a failed
# external command (venv creation, pip) does NOT stop the script on its own,
# so each one is checked explicitly via $LASTEXITCODE. Without this, a
# partially-failed `pip install` (e.g. one package fails to build) would go
# unnoticed here and only surface later as a confusing runtime import error
# once uvicorn actually tries to start.
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

$env:CELERY_TASK_ALWAYS_EAGER = "true"
Write-Host "Starting backend at http://localhost:8000 (docs at /docs)..."
uvicorn app.main:app --reload
