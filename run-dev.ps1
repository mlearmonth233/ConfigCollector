# Launches the ConfigCollector backend and frontend, each in its own
# PowerShell window, for local development on Windows.
# Usage: from the repo root, run:  .\run-dev.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\run-backend.ps1"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; .\run-frontend.ps1"

Write-Host "Started backend (http://localhost:8000) and frontend (http://localhost:5173) in separate windows."
