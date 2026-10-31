# AnswerOnCue uninstaller (Windows PowerShell 5.1, no admin needed).
# ASCII only.

function Uninstall-AnswerOnCue {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'

    $Root = $env:AOC_INSTALL_ROOT
    if (-not $Root) { $Root = $PSScriptRoot }
    if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA 'AnswerOnCue' }
    $DataDir = Join-Path $Root 'data'
    $AppName = 'AnswerOnCue'

    function Start-Step([string]$Name) { Write-Host "==> $Name" }

    function Remove-Path([string]$Path) {
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            if (-not (Test-Path -LiteralPath $Path)) { return }
            try {
                Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
                return
            } catch {
                Start-Sleep -Seconds 1
            }
        }
        if (Test-Path -LiteralPath $Path) { Write-Host "Could not remove: $Path" }
    }

    try {
        # 1. Stop the server if it answers.
        Start-Step 'Stopping the server'
        $port = 8770
        $statePath = Join-Path $DataDir 'launcher.json'
        if (Test-Path -LiteralPath $statePath) {
            try {
                $saved = (Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json).port
                if ($saved) { $port = [int]$saved }
            } catch {
                Write-Host 'Could not read launcher.json.'
            }
        }
        $health = "http://127.0.0.1:$port/api/health"
        $running = $false
        try {
            $reply = Invoke-RestMethod -Uri $health -TimeoutSec 2
            $running = ($reply.app -eq 'answeroncue')
        } catch {
            $running = $false
        }
        if ($running) {
            try {
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/system/shutdown" -Headers @{ 'X-AnswerOnCue' = '1' } -TimeoutSec 10 | Out-Null
            } catch {
                Write-Host "Shutting down through the API failed: $($_.Exception.Message)"
            }
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $deadline) {
                try {
                    Invoke-RestMethod -Uri $health -TimeoutSec 2 | Out-Null
                    Start-Sleep -Milliseconds 500
                } catch {
                    break
                }
            }
        }

        # 2. Shortcuts, registry entry, program files.
        Start-Step 'Removing shortcuts and program files'
        $desktop = $env:AOC_DESKTOP_DIR
        if (-not $desktop) { $desktop = [Environment]::GetFolderPath('Desktop') }
        $programs = $env:AOC_PROGRAMS_DIR
        if (-not $programs) { $programs = [Environment]::GetFolderPath('Programs') }
        Remove-Path (Join-Path $desktop "$AppName.lnk")
        Remove-Path (Join-Path $programs $AppName)
        if ($env:AOC_UNINSTALL_SKIP_REGISTRY -ne '1') {
            Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AnswerOnCue' -Recurse -Force -ErrorAction SilentlyContinue
        }
        foreach ($name in @('app', 'uv', 'python', 'cache', 'updates')) {
            Remove-Path (Join-Path $Root $name)
        }
        Remove-Path (Join-Path $Root 'uninstall.ps1')

        # 3. Data, only on request.
        $keep = $env:AOC_UNINSTALL_KEEP_DATA
        if ($keep -eq '1') {
            $removeData = $false
        } elseif ($keep -eq '0') {
            $removeData = $true
        } else {
            $answer = Read-Host 'Also delete the data (subjects, recordings, API keys)? Type YES to delete'
            $removeData = ($answer.Trim().ToUpperInvariant() -eq 'YES')
        }
        if ($removeData) {
            Start-Step 'Removing data'
            Remove-Path $DataDir
        } else {
            Write-Host "The data was kept in: $DataDir"
        }

        # 4. The root itself, only when empty.
        if ((Test-Path -LiteralPath $Root) -and -not (Get-ChildItem -LiteralPath $Root -Force)) {
            Remove-Item -LiteralPath $Root -Force
        }
        Write-Host 'UNINSTALL OK'
    } catch {
        Write-Host "UNINSTALL FAILED: $($_.Exception.Message)"
        $global:AnswerOnCueExitCode = 1
    }
}

Uninstall-AnswerOnCue
