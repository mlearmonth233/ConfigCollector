# Sets up (if needed) and runs the ConfigCollector backend on Windows/PowerShell.
#
# Usage: from the backend/ folder, run:
#   .\run-backend.ps1            # real async mode: dispatches jobs to a Celery
#                                 # worker over Redis (needs Redis/Memurai + run-worker.ps1 running)
#   .\run-backend.ps1 -Eager     # old quick-testing behavior: collection jobs run
#                                 # synchronously in-process, no Redis/worker needed
#
# Async mode is the default because eager mode blocks the whole HTTP request
# on every device's real SSH work - fine for a quick single-device smoke test,
# but a multi-device "Start collection" call will sit there (looking stuck)
# until every device finishes. Real async mode returns immediately and lets
# the frontend's job status bar show progress as devices are collected.
#
# On Windows there's no Docker requirement for this: install Memurai
# (https://www.memurai.com/) - a native Windows Redis-compatible service - and
# it listens on localhost:6379 by default, matching this app's default
# REDIS_URL. Then run .\run-worker.ps1 in another window alongside this one.

param(
    [switch]$Eager
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# $ErrorActionPreference only governs PowerShell-native errors - a failed
# external command (venv creation, pip) does NOT stop the script on its own,
# so each one is checked explicitly via $LASTEXITCODE. Without this, a
# partially-failed `pip install` (e.g. one package fails to build) would go
# unnoticed here and only surface later as a confusing runtime import error
# once uvicorn actually tries to start.
. "$PSScriptRoot\dev-env.ps1"
Ensure-Environment

if ($Eager) {
    $env:CELERY_TASK_ALWAYS_EAGER = "true"
    Write-Host "Eager mode: collection jobs will run synchronously in-process (no worker needed)."
} else {
    $env:CELERY_TASK_ALWAYS_EAGER = "false"
    Assert-RedisReachable
    Write-Host "Async mode: make sure .\run-worker.ps1 is running too."
}

Write-Host "Starting backend at http://localhost:8000 (docs at /docs)..."
uvicorn app.main:app --reload
