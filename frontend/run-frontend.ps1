# Sets up (if needed) and runs the ConfigCollector frontend on Windows/PowerShell.
# Usage: from the frontend/ folder, run:  .\run-frontend.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# $ErrorActionPreference only governs PowerShell-native errors - a failed
# external command (npm install) does NOT stop the script on its own, so
# it's checked explicitly. Without this, a failed install would go
# unnoticed here and only surface later as a confusing module-not-found
# error once Vite actually tries to start.
if (-not (Test-Path "node_modules")) {
    Write-Host "Installing dependencies..."
    npm install
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed (exit code $LASTEXITCODE) - see the output above for the actual error."
    }
}

if (-not (Test-Path ".env.local")) {
    # Optional: the app already defaults to this same URL when unset, but
    # writing it out makes the setting discoverable and easy to change.
    "VITE_API_BASE_URL=http://localhost:8000" | Out-File -Encoding utf8 .env.local
}

$url = "http://localhost:5173"

# Opens Chrome automatically once the dev server actually responds, without
# blocking `npm run dev` below (which stays in the foreground so Ctrl+C
# still stops it normally). Runs in a background job, not a new window;
# gives up quietly after ~30s if the server never comes up. Falls back to
# whatever the system's default browser is if Chrome isn't installed/found.
Start-Job -ScriptBlock {
    param($url)
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
            if ($resp.StatusCode -eq 200) { break }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    try {
        Start-Process "chrome" $url -ErrorAction Stop
    } catch {
        Start-Process $url
    }
} -ArgumentList $url | Out-Null

Write-Host "Starting frontend at $url (Chrome will open automatically once it's ready)..."
npm run dev
