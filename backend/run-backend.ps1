# Sets up (if needed) and runs the ConfigCollector backend on Windows/PowerShell.
# Usage: from the backend/ folder, run:  .\run-backend.ps1
#
# CELERY_TASK_ALWAYS_EAGER=true runs collection jobs synchronously in-process
# instead of dispatching to a Celery worker over Redis - handy for local
# testing without standing up the full docker-compose stack.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

$activate = ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    throw "Could not find $activate - venv creation may have failed."
}
& $activate

Write-Host "Installing/updating dependencies..."
pip install -r requirements.txt

$env:CELERY_TASK_ALWAYS_EAGER = "true"
Write-Host "Starting backend at http://localhost:8000 (docs at /docs)..."
uvicorn app.main:app --reload
