# Runs the ConfigCollector Celery worker on Windows/PowerShell, so collection
# jobs started against the real (non-eager) backend actually get processed.
#
# Usage: from the backend/ folder, in a separate window from run-backend.ps1:
#   .\run-worker.ps1
#
# Needs Redis (e.g. Memurai on Windows - https://www.memurai.com/) reachable
# at REDIS_URL (defaults to redis://localhost:6379/0, same as the backend).
#
# --pool=solo is required on Windows: Celery's default "prefork" pool needs
# fork(), which Windows doesn't have. This app never needs more than one
# device's SSH session in flight per job anyway (devices are deliberately
# authenticated one at a time and pipelined), so a single-process pool isn't
# a real limitation here.

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

Write-Host "Starting Celery worker (pool=solo)..."
celery -A app.celery_app worker --loglevel=info --pool=solo
