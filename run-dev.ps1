# Starts the whole Packrat stack for local development on Windows - backend,
# Celery worker, Celery beat and the frontend - in THIS PowerShell window.
# Ctrl+C stops everything.
#
# Usage, from the repo root:
#   .\run-dev.ps1            # everything in this one window (needs Redis/Memurai)
#   .\run-dev.ps1 -Eager     # no worker/beat/Redis; jobs run inside the backend
#   .\run-dev.ps1 -Panes     # Windows Terminal: one tab, split into panes
#   .\run-dev.ps1 -Windows   # the old behaviour: a separate window per process
#
# In the default mode the four processes share this console. Every line
# from the app says which process wrote it - the backend logs as
# [api:pid], the worker as [worker:pid], beat as [beat:pid] - and Vite's
# output is unmistakable, so the interleaving reads fine. The same lines
# also go to backend\logs\packrat-*.log, one file per process, if you want
# them separated (Settings > Troubleshooting in the app shows them too).

param(
    [switch]$Eager,
    [switch]$Panes,
    [switch]$Windows
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

# ---------------------------------------------------------------------------
# Old behaviour: one PowerShell window per process.
# ---------------------------------------------------------------------------
if ($Windows) {
    $eagerFlag = if ($Eager) { " -Eager" } else { "" }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backend'; .\run-backend.ps1$eagerFlag"
    if (-not $Eager) {
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backend'; .\run-worker.ps1"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backend'; .\run-beat.ps1"
    }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontend'; .\run-frontend.ps1"
    Write-Host "Started each process in its own window. Close a window to stop that process."
    return
}

# ---------------------------------------------------------------------------
# Windows Terminal panes: one tab, backend left, frontend right, worker and
# beat underneath. Falls back to the single-window mode below if wt.exe
# isn't installed.
# ---------------------------------------------------------------------------
if ($Panes) {
    $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
    if ($wt) {
        $eagerFlag = if ($Eager) { " -Eager" } else { "" }
        $cmd = "new-tab --title Packrat -d `"$backend`" powershell -NoExit -Command .\run-backend.ps1$eagerFlag"
        $cmd += " ; split-pane -V -d `"$frontend`" powershell -NoExit -Command .\run-frontend.ps1"
        if (-not $Eager) {
            $cmd += " ; split-pane -H -d `"$backend`" powershell -NoExit -Command .\run-beat.ps1"
            $cmd += " ; move-focus left ; split-pane -H -d `"$backend`" powershell -NoExit -Command .\run-worker.ps1"
        }
        Start-Process $wt.Source -ArgumentList "-w 0 $cmd"
        Write-Host "Opened a Windows Terminal tab with one pane per process."
        return
    }
    Write-Host "Windows Terminal (wt.exe) not found - running everything in this window instead."
}

# ---------------------------------------------------------------------------
# Default: everything in this window.
# ---------------------------------------------------------------------------

# Set up once, here, rather than letting four scripts race to install the
# same dependencies. Activating the venv puts uvicorn/celery on this
# session's PATH, which the child processes inherit.
. (Join-Path $backend "dev-env.ps1")
. (Join-Path $frontend "dev-env.ps1")
Ensure-Environment
Ensure-FrontendEnvironment -Path $frontend

if ($Eager) {
    $env:CELERY_TASK_ALWAYS_EAGER = "true"
    Write-Host "Eager mode: jobs run inside the backend; no worker, beat or Redis."
} else {
    $env:CELERY_TASK_ALWAYS_EAGER = "false"
    Assert-RedisReachable
}

$processes = @()

function Start-Piece {
    param([string]$Name, [string]$Exe, [string]$Arguments, [string]$WorkingDirectory)
    Write-Host ("Starting {0,-8} {1} {2}" -f $Name, $Exe, $Arguments) -ForegroundColor DarkCyan
    # -NoNewWindow attaches the child to this console: its output appears
    # here, and Ctrl+C in this window reaches it directly.
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru
    $script:processes += [pscustomobject]@{ Name = $Name; Process = $p }
}

function Stop-Everything {
    foreach ($piece in $script:processes) {
        if (-not $piece.Process.HasExited) {
            # /T takes the whole tree (uvicorn's reloader child, vite under
            # node) so nothing is left holding ports 8000 or 5173.
            & taskkill.exe /PID $piece.Process.Id /T /F 2>&1 | Out-Null
        }
    }
}

Write-Host ""
Write-Host "Packrat dev stack - one window, Ctrl+C stops everything." -ForegroundColor Cyan
Write-Host "  Backend   http://localhost:8000  (API docs at /docs)"
Write-Host "  Frontend  http://localhost:5173  (opens in your browser when ready)"
Write-Host "  Logs      backend\logs\packrat-*.log"
Write-Host ""

try {
    Start-Piece -Name "api" -Exe "uvicorn" -Arguments "app.main:app --reload" -WorkingDirectory $backend
    if (-not $Eager) {
        Start-Piece -Name "worker" -Exe "celery" -Arguments "-A app.celery_app worker --loglevel=info --pool=threads --concurrency=8" -WorkingDirectory $backend
        Start-Piece -Name "beat" -Exe "celery" -Arguments "-A app.celery_app beat --loglevel=info" -WorkingDirectory $backend
    }
    # node directly rather than `npm run dev`: npm goes through cmd.exe,
    # which answers Ctrl+C with "Terminate batch job (Y/N)?" and holds the
    # shutdown hostage until someone types Y.
    Start-Piece -Name "frontend" -Exe "node" -Arguments "node_modules\vite\bin\vite.js" -WorkingDirectory $frontend
    Start-BrowserWhenReady "http://localhost:5173"

    # Watch the pieces. If one dies on its own (port already in use, a
    # crash on startup) the rest are stopped too, so a broken stack doesn't
    # sit half-running while the error scrolls out of view.
    while ($true) {
        Start-Sleep -Seconds 1
        $dead = $processes | Where-Object { $_.Process.HasExited } | Select-Object -First 1
        if ($dead) {
            Write-Host ""
            Write-Host ("{0} exited with code {1} - stopping the rest. Check the output above (and backend\logs) for the reason." -f $dead.Name, $dead.Process.ExitCode) -ForegroundColor Yellow
            break
        }
    }
} finally {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor DarkCyan
    Stop-Everything
    Get-Job | Remove-Job -Force -ErrorAction SilentlyContinue
}
