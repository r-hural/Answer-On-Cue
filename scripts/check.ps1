# Full quality check. Stops at the first failure.
param([switch]$SkipE2E)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'

function Invoke-Stage([string]$Name, [string]$Dir, [string]$Exe, [string[]]$Arguments) {
    Write-Host "==> $Name"
    Push-Location $Dir
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        Pop-Location
    }
    if ($code -ne 0) {
        Write-Host "CHECK FAILED: $Name"
        exit 1
    }
}

Invoke-Stage 'ruff check' $Root $VenvPython @('-m', 'ruff', 'check', 'backend')
Invoke-Stage 'ruff format' $Root $VenvPython @('-m', 'ruff', 'format', '--check', 'backend')
Invoke-Stage 'pytest' $Backend $VenvPython @('-m', 'pytest', '-q')
Invoke-Stage 'npm lint' $Frontend 'npm.cmd' @('run', 'lint')
Invoke-Stage 'npm test' $Frontend 'npm.cmd' @('test')
Invoke-Stage 'npm build' $Frontend 'npm.cmd' @('run', 'build')
if (-not $SkipE2E) {
    # A fixed port can be held by an unrelated program; ask the OS for a free one unless set.
    if (-not $env:AOC_TEST_PORT) {
        $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
        $probe.Start()
        $env:AOC_TEST_PORT = "$($probe.LocalEndpoint.Port)"
        $probe.Stop()
    }
    Write-Host "e2e port: $env:AOC_TEST_PORT"
    Invoke-Stage 'npm e2e' $Frontend 'npm.cmd' @('run', 'e2e')
}

Write-Host 'CHECK PASSED'
exit 0
