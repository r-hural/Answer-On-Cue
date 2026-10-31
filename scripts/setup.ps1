# First-time setup. Safe to re-run.
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'
$script:Step = 'start'

function Start-Step([string]$Name) {
    $script:Step = $Name
    Write-Host "==> $Name"
}

# Runs a native command; stderr output is not treated as an error, only the exit code is.
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        throw "The command '$Exe $($Arguments -join ' ')' failed with exit code $code"
    }
}

function Test-Python313([string]$Exe, [string[]]$Prefix) {
    try {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $out = & $Exe @Prefix -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        $code = $LASTEXITCODE
        $ErrorActionPreference = $previous
        return ($code -eq 0 -and ($out | Select-Object -First 1) -eq '3.13')
    } catch {
        $ErrorActionPreference = $previous
        return $false
    }
}

function Find-Python313 {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py -and (Test-Python313 $py.Source @('-3.13'))) {
        return @{ Exe = $py.Source; Prefix = @('-3.13') }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and (Test-Python313 $python.Source @())) {
        return @{ Exe = $python.Source; Prefix = @() }
    }
    $fixed = 'C:\Program Files\Python313\python.exe'
    if ((Test-Path $fixed) -and (Test-Python313 $fixed @())) {
        return @{ Exe = $fixed; Prefix = @() }
    }
    return $null
}

try {
    Start-Step 'Looking for Python 3.13'
    $found = Find-Python313
    if (-not $found) {
        throw ("Python 3.13 not found. Tried: 'py -3.13', 'python', 'C:\Program Files\Python313\python.exe'.")
    }

    Start-Step 'Creating the backend/.venv environment'
    if (-not (Test-Path $VenvPython)) {
        Invoke-Native $found.Exe ($found.Prefix + @('-m', 'venv', (Join-Path $Backend '.venv')))
    }

    Start-Step 'Installing Python packages (pip install)'
    Invoke-Native $VenvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
    $requirements = Get-ChildItem (Join-Path $Backend 'requirements') -Filter '*.txt' | Sort-Object Name
    foreach ($file in $requirements) {
        Invoke-Native $VenvPython @('-m', 'pip', 'install', '-r', $file.FullName)
    }

    Start-Step 'Installing npm packages (npm ci)'
    Push-Location $Frontend
    try {
        if (Test-Path 'package-lock.json') {
            Invoke-Native 'npm.cmd' @('ci')
        } else {
            Invoke-Native 'npm.cmd' @('install')
        }

        Start-Step 'Installing Chromium for Playwright (playwright install chromium)'
        Invoke-Native 'npx.cmd' @('playwright', 'install', 'chromium')
    } finally {
        Pop-Location
    }

    Start-Step 'Creating the data/ directory'
    $dataDir = Join-Path $Root 'data'
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir | Out-Null
    }

    Start-Step 'Database migration (alembic upgrade head)'
    Push-Location $Backend
    try {
        Invoke-Native $VenvPython @('-m', 'alembic', 'upgrade', 'head')
    } finally {
        Pop-Location
    }

    Start-Step 'The backend/.env settings file'
    if (-not (Test-Path (Join-Path $Backend '.env'))) {
        Write-Host 'backend/.env is missing. Copy backend/env-example.txt to backend/.env and fill in the keys (optional).'
    }

    Write-Host 'SETUP OK'
    exit 0
} catch {
    Write-Host "SETUP FAILED: $($script:Step): $($_.Exception.Message)"
    exit 1
}
