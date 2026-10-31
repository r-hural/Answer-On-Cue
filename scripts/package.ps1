# Builds the release folder: release-out\v<version>\answeroncue-<version>.zip and friends.
# -Python: the Python that runs the packaging (default: backend\.venv\Scripts\python.exe).
param([switch]$SkipBuild, [string]$Version = '', [string]$Out = '', [string]$Python = '')

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'
if ($Python) { $VenvPython = $Python }
if (-not $Out) {
    $OutVersion = if ($Version) { $Version } else { (Get-Content (Join-Path $Root 'VERSION') -Raw).Trim() }
    $Out = Join-Path $Root "release-out\v$OutVersion"
}

function Invoke-Native([string]$Dir, [string]$Exe, [string[]]$Arguments) {
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
        Write-Host "PACKAGE FAILED: $Exe exited with $code"
        exit 1
    }
}

if (-not $SkipBuild) {
    Write-Host '==> Building the interface (npm run build)'
    Invoke-Native $Frontend 'npm.cmd' @('run', 'build')
}

Write-Host '==> Packaging (distribution.package)'
$packageArgs = @('-m', 'distribution.package', '--out', $Out)
if ($Version) { $packageArgs += @('--version', $Version) }
Invoke-Native $Backend $VenvPython $packageArgs
exit 0
