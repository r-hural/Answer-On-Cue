# Download Piper voice models to data/voices (default: every known voice).
param([string]$Voice)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    Write-Host 'backend/.venv is missing. Run scripts/setup.ps1 first.'
    exit 1
}

$arguments = @('-m', 'app.features.voice.download')
if ($Voice) { $arguments += $Voice }

Push-Location $Backend
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $VenvPython @arguments
    $code = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previous
    Pop-Location
}
if ($code -ne 0) {
    Write-Host 'Not all voices were downloaded.'
    exit 1
}
Write-Host 'The Piper voices are ready.'
exit 0
