# Sets up (if needed) and runs the ConfigCollector frontend on Windows/PowerShell.
# Usage: from the frontend/ folder, run:  .\run-frontend.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Install (or re-install) dependencies whenever node_modules is missing OR
# package.json / package-lock.json has changed since the last install -
# same idea as the backend's dev-env.ps1 requirements stamp. Without the
# stamp check, pulling a change that adds a package (react-markdown for the
# Help page, @xterm for the Terminal) left the old node_modules in place
# and Vite failed at startup with "Failed to resolve import ..." until
# someone remembered to run `npm install` by hand.
#
# $ErrorActionPreference only governs PowerShell-native errors - a failed
# external command (npm install) does NOT stop the script on its own, so
# it's checked explicitly. Without this, a failed install would go
# unnoticed here and only surface later as a confusing module-not-found
# error once Vite actually tries to start.
$stampPath = Join-Path "node_modules" ".install-stamp.txt"
$manifest = (Get-Content "package.json" -Raw)
if (Test-Path "package-lock.json") { $manifest += (Get-Content "package-lock.json" -Raw) }
$sha = [System.Security.Cryptography.SHA256]::Create()
$stamp = [System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($manifest))).Replace("-", "")
$current = if (Test-Path $stampPath) { (Get-Content $stampPath -Raw).Trim() } else { "" }

if (-not (Test-Path "node_modules") -or $current -ne $stamp) {
    if (Test-Path "node_modules") {
        Write-Host "package.json changed since the last install - updating dependencies..."
    } else {
        Write-Host "Installing dependencies..."
    }
    npm install
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed (exit code $LASTEXITCODE) - see the output above for the actual error."
    }
    $stamp | Out-File -Encoding ascii $stampPath
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
