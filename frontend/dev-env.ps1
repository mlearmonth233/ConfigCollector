# Shared frontend setup for run-frontend.ps1 and the repo-root run-dev.ps1.
# Dot-source it, then call Ensure-FrontendEnvironment (from the frontend
# folder or with -Path pointing at it):
#   . "$PSScriptRoot\dev-env.ps1"
#   Ensure-FrontendEnvironment

function Ensure-FrontendEnvironment {
    param([string]$Path = $PSScriptRoot)

    Push-Location $Path
    try {
        # Install (or re-install) dependencies whenever node_modules is
        # missing OR package.json / package-lock.json has changed since the
        # last install - same idea as the backend's requirements stamp.
        # Without it, pulling a change that adds a package left the old
        # node_modules in place and Vite failed at startup with "Failed to
        # resolve import ..." until someone ran `npm install` by hand.
        $stampPath = Join-Path "node_modules" ".install-stamp.txt"
        $manifest = (Get-Content "package.json" -Raw)
        if (Test-Path "package-lock.json") { $manifest += (Get-Content "package-lock.json" -Raw) }
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $stamp = [System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($manifest))).Replace("-", "")
        $current = if (Test-Path $stampPath) { (Get-Content $stampPath -Raw).Trim() } else { "" }

        if (-not (Test-Path "node_modules") -or $current -ne $stamp) {
            if (Test-Path "node_modules") {
                Write-Host "package.json changed since the last install - updating frontend dependencies..."
            } else {
                Write-Host "Installing frontend dependencies..."
            }
            npm install
            # $ErrorActionPreference only governs PowerShell-native errors; a
            # failed external command has to be checked explicitly.
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed (exit code $LASTEXITCODE) - see the output above for the actual error."
            }
            $stamp | Out-File -Encoding ascii $stampPath
        } else {
            Write-Host "Frontend dependencies already up to date."
        }

        if (-not (Test-Path ".env.local")) {
            # Optional: the app already defaults to this same URL when unset,
            # but writing it out makes the setting discoverable.
            "VITE_API_BASE_URL=http://localhost:8000" | Out-File -Encoding utf8 .env.local
        }
    } finally {
        Pop-Location
    }
}

function Start-BrowserWhenReady {
    # Opens Chrome (or the default browser) once the dev server actually
    # responds, from a background job so the caller isn't blocked. Gives up
    # quietly after ~30s if the server never comes up.
    param([string]$Url = "http://localhost:5173")

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
    } -ArgumentList $Url | Out-Null
}
