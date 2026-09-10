# Launches the ConfigCollector backend, Celery worker, and frontend, each in
# its own PowerShell window, for local development on Windows.
#
# Usage: from the repo root, run:
#   .\run-dev.ps1            # real async mode: also starts the worker window
#                             # (needs Redis/Memurai running - see README)
#   .\run-dev.ps1 -Eager     # old quick-testing behavior: no worker/Redis
#                             # needed, but "Start collection" blocks in-request

param(
    [switch]$Eager
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if ($Eager) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\run-backend.ps1 -Eager"
} else {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\run-backend.ps1"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\run-worker.ps1"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\run-beat.ps1"
}
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; .\run-frontend.ps1"

if ($Eager) {
    Write-Host "Started backend (eager mode, http://localhost:8000) and frontend (http://localhost:5173) in separate windows."
    Write-Host "Note: schedules and snapshot retention need Celery beat, which eager mode doesn't start - use non-eager mode to exercise those."
} else {
    Write-Host "Started backend (http://localhost:8000), Celery worker, Celery beat, and frontend (http://localhost:5173) in separate windows."
    Write-Host "Needs Redis (e.g. Memurai) running first - see README if the backend window reports it can't connect."
}
