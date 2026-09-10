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

if ($Eager) {
    $env:CELERY_TASK_ALWAYS_EAGER = "true"
    Write-Host "Eager mode: collection jobs will run synchronously in-process (no worker needed)."
} else {
    $env:CELERY_TASK_ALWAYS_EAGER = "false"

    $redisHost = "localhost"
    $redisPort = 6379
    if ($env:REDIS_URL) {
        try {
            $uri = [Uri]$env:REDIS_URL
            if ($uri.Host) { $redisHost = $uri.Host }
            if ($uri.Port -gt 0) { $redisPort = $uri.Port }
        } catch {
            # Fall back to the localhost:6379 default if REDIS_URL doesn't parse.
        }
    }

    $probe = New-Object System.Net.Sockets.TcpClient
    $connected = $false
    try {
        $result = $probe.BeginConnect($redisHost, $redisPort, $null, $null)
        $connected = $result.AsyncWaitHandle.WaitOne(1500) -and $probe.Connected
    } catch {
        $connected = $false
    } finally {
        $probe.Close()
    }

    if (-not $connected) {
        throw @"
Can't reach Redis at ${redisHost}:${redisPort}.

Real (non-eager) mode needs Redis running so the backend can hand jobs off
to a Celery worker instead of blocking the HTTP request until every device
finishes. On Windows without Docker, the easiest way to get this is Memurai
(https://www.memurai.com/) - a free, native Windows Redis-compatible service.
Install it, make sure the service is running, then start this script again.

Then, in another window, start the worker:
  .\run-worker.ps1

Or, if you just want the old quick-testing behavior (no Redis/worker needed,
but "Start collection" blocks until all devices finish), run:
  .\run-backend.ps1 -Eager
"@
    }

    Write-Host "Async mode: Redis reachable at ${redisHost}:${redisPort}. Make sure .\run-worker.ps1 is running too."
}

Write-Host "Starting backend at http://localhost:8000 (docs at /docs)..."
uvicorn app.main:app --reload
