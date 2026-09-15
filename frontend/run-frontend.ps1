# Sets up (if needed) and runs the ConfigCollector frontend on Windows/PowerShell.
# Usage: from the frontend/ folder, run:  .\run-frontend.ps1
#
# To start the whole stack in one window instead, use .\run-dev.ps1 from
# the repo root.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

. "$PSScriptRoot\dev-env.ps1"
Ensure-FrontendEnvironment

$url = "http://localhost:5173"
Start-BrowserWhenReady $url

Write-Host "Starting frontend at $url (Chrome will open automatically once it's ready)..."
npm run dev
