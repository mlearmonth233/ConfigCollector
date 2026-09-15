# Shared venv-setup/dependency-install logic for run-backend.ps1,
# run-worker.ps1 and run-beat.ps1 - dot-sourced by each, e.g.:
#   . "$PSScriptRoot\dev-env.ps1"
#   Ensure-Environment
#
# All three scripts point at the same .venv and are meant to be started
# side-by-side (that's the whole point of splitting backend/worker/beat).
# Each used to independently run `pip install -r requirements.txt` with no
# coordination - two concurrent pip processes writing into the same
# site-packages directory race on any package upgrade, and a package with
# compiled DLLs (e.g. cryptography, which asyncssh requires an upgraded
# version of) is especially prone to failing with a confusing mid-install
# OSError rather than any kind of "wait your turn" message. A named,
# session-wide Mutex now serializes the actual `pip install` call across
# whichever of these three scripts gets there first; a requirements.txt
# hash stamp then lets the others skip installing entirely once it's done,
# so steady-state startup (nothing changed) never even touches the lock.

function Assert-LastExitCode([string]$step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$step failed (exit code $LASTEXITCODE) - see the output above for the actual error."
    }
}

function Ensure-Environment {
    if (-not (Test-Path "$PSScriptRoot\.venv")) {
        Write-Host "Creating virtual environment..."
        python -m venv "$PSScriptRoot\.venv"
        Assert-LastExitCode "Virtual environment creation"
    }

    $activate = "$PSScriptRoot\.venv\Scripts\Activate.ps1"
    if (-not (Test-Path $activate)) {
        throw "Could not find $activate - venv creation may have failed."
    }
    & $activate

    $reqPath = "$PSScriptRoot\requirements.txt"
    $reqHash = (Get-FileHash $reqPath -Algorithm SHA256).Hash
    $stampPath = "$PSScriptRoot\.venv\install-stamp.txt"

    if ((Test-Path $stampPath) -and ((Get-Content $stampPath -Raw).Trim() -eq $reqHash)) {
        Write-Host "Dependencies already up to date."
        return
    }

    # Only one of run-backend/run-worker/run-beat should actually run pip
    # install at a time - the rest wait here, then re-check the stamp
    # below (whoever held the lock before them may have just finished
    # installing this exact requirements.txt, in which case they skip too).
    $mutex = New-Object System.Threading.Mutex($false, "ConfigCollectorPipInstall")
    Write-Host "Waiting for dependency install lock (another run-*.ps1 script may be installing)..."
    [void]$mutex.WaitOne()
    try {
        if ((Test-Path $stampPath) -and ((Get-Content $stampPath -Raw).Trim() -eq $reqHash)) {
            Write-Host "Dependencies already up to date (installed by another script while we waited)."
            return
        }

        Write-Host "Installing/updating dependencies..."
        pip install -r $reqPath
        Assert-LastExitCode "pip install"
        Set-Content -Path $stampPath -Value $reqHash
    } finally {
        $mutex.ReleaseMutex()
    }
}
