# Run the local server on 127.0.0.1.
param([switch]$Build)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'

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
        Write-Host "The command '$Exe $($Arguments -join ' ')' exited with code $code"
        exit 1
    }
}

if (-not (Test-Path $VenvPython)) {
    Write-Host 'backend/.venv is missing. Run scripts/setup.ps1 first.'
    exit 1
}

$port = 8000
if ($env:AOC_PORT) { $port = [int]$env:AOC_PORT }

$taken = $false
$listener = $null
try {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse('127.0.0.1'), $port)
    $listener.Start()
} catch {
    $taken = $true
} finally {
    if ($listener) { $listener.Stop() }
}
if ($taken) {
    Write-Host "Port $port is in use. Close the program using it or set a different AOC_PORT."
    exit 1
}

if ($Build -or -not (Test-Path (Join-Path $Frontend 'dist\index.html'))) {
    Write-Host '==> Building the interface (npm run build)'
    Push-Location $Frontend
    try { Invoke-Native 'npm.cmd' @('run', 'build') } finally { Pop-Location }
}

Push-Location $Backend
try {
    Write-Host '==> Database migration (alembic upgrade head)'
    Invoke-Native $VenvPython @('-m', 'alembic', 'upgrade', 'head')
    Write-Host "==> Server: http://127.0.0.1:$port"
    Invoke-Native $VenvPython @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$port")
} finally {
    Pop-Location
}
