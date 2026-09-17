<#
.SYNOPSIS
  Builds the Packrat Windows installer: dist\installer\Packrat-Setup-<version>-windows.exe

.DESCRIPTION
  1. Builds the frontend (npm ci + npm run build) unless -SkipFrontend.
  2. Creates installer\.venv with the backend requirements plus PyInstaller.
  3. Runs PyInstaller with installer\packrat.spec -> dist\Packrat\ (the app folder).
  4. Compiles installer\packrat.iss with Inno Setup -> the Setup .exe.
     Skipped with a note if Inno Setup is not installed; dist\Packrat\ is
     still usable as a portable folder (run Packrat.exe).

  Needs: Python 3.11-3.13 on PATH, Node.js 20+, and Inno Setup 6
  (https://jrsoftware.org/isdl.php) for step 4.

.EXAMPLE
  .\installer\build-windows.ps1
  .\installer\build-windows.ps1 -Version 0.2.0
  .\installer\build-windows.ps1 -SkipFrontend -Console   # quick rebuild with a console window for debugging
#>
param(
    [string]$Version,
    [switch]$SkipFrontend,
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$installer = $PSScriptRoot
if (-not $Version) { $Version = (Get-Content (Join-Path $installer "VERSION") -Raw).Trim() }
$env:PACKRAT_VERSION = $Version
$env:PACKRAT_BUILD_CONSOLE = if ($Console) { "1" } else { "" }

function Invoke-Checked {
    param([string]$Description, [scriptblock]$Command)
    Write-Host "==> $Description" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit code $LASTEXITCODE)" }
}

if (-not $SkipFrontend) {
    Push-Location (Join-Path $root "frontend")
    try {
        # No VITE_API_BASE_URL: a production build then talks to the same
        # origin it was served from, which is what the bundle needs.
        Remove-Item Env:VITE_API_BASE_URL -ErrorAction SilentlyContinue
        if (Test-Path "package-lock.json") { Invoke-Checked "npm ci" { npm ci } } else { Invoke-Checked "npm install" { npm install } }
        Invoke-Checked "Frontend build" { npm run build }
    } finally { Pop-Location }
}

$venv = Join-Path $installer ".venv"
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    Invoke-Checked "Create build venv" { python -m venv $venv }
}
Invoke-Checked "Install backend requirements" { & $python -m pip install --quiet --upgrade pip }
Invoke-Checked "Install backend requirements" { & $python -m pip install --quiet -r (Join-Path $root "backend\requirements.txt") }
Invoke-Checked "Install desktop build requirements" { & $python -m pip install --quiet -r (Join-Path $installer "requirements-desktop.txt") }

Push-Location $root
try {
    if (Test-Path "dist\Packrat") { Remove-Item "dist\Packrat" -Recurse -Force }
    Invoke-Checked "PyInstaller" { & $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller (Join-Path $installer "packrat.spec") }
} finally { Pop-Location }

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { $iscc = $found.Source }
}
if (-not $iscc) {
    Write-Host "Inno Setup (ISCC.exe) not found - skipping the Setup .exe. dist\Packrat\Packrat.exe runs as a portable copy." -ForegroundColor Yellow
    Write-Host "Install Inno Setup 6 from https://jrsoftware.org/isdl.php and rerun with -SkipFrontend to produce the installer."
    exit 0
}
New-Item -ItemType Directory -Force (Join-Path $root "dist\installer") | Out-Null
Invoke-Checked "Inno Setup" { & $iscc "/DMyAppVersion=$Version" (Join-Path $installer "packrat.iss") }
$out = Get-ChildItem (Join-Path $root "dist\installer\Packrat-Setup-*.exe") | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "Built $($out.FullName) ($([math]::Round($out.Length / 1MB, 1)) MB)" -ForegroundColor Green
