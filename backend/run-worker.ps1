# Runs the ConfigCollector Celery worker on Windows/PowerShell, so collection
# jobs started against the real (non-eager) backend actually get processed.
#
# Usage: from the backend/ folder, in a separate window from run-backend.ps1:
#   .\run-worker.ps1
#
# Needs Redis (e.g. Memurai on Windows - https://www.memurai.com/) reachable
# at REDIS_URL (defaults to redis://localhost:6379/0, same as the backend).
#
# --pool=threads is used because Celery's default "prefork" pool needs
# fork(), which Windows doesn't have. Unlike --pool=solo (concurrency 1),
# threads can actually run more than one task at once, which this app
# depends on: a job's pipelining only overlaps device N+1's authentication
# with device N's (usually slower) command-running phase if the worker can
# pick up device N+1's task while device N's task is still executing -
# with a single-task pool, the "next" task would just sit queued until the
# current one fully finished, silently turning pipelining back into strict
# one-at-a-time processing. Devices are still only ever dispatched, and
# authenticate, one at a time by design (see tasks.py) - this concurrency
# is what lets that overlap actually happen rather than being wasted.

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

Write-Host "Starting Celery worker (pool=threads, concurrency=8)..."
celery -A app.celery_app worker --loglevel=info --pool=threads --concurrency=8
