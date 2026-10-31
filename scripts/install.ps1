# AnswerOnCue installer (Windows PowerShell 5.1, no admin needed).
#   irm https://github.com/__RELEASES_REPO__/releases/latest/download/install.ps1 | iex
# This file is ASCII only on purpose: Windows PowerShell reads BOM-less scripts in the ANSI code page.
# Everything lives in one function that is called at the end, so `iex` works. Decisions that can be
# tested live in Python (backend\distribution\postinstall.py).

function Install-AnswerOnCue {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $UvVersion = '0.12.23'
    $ReleasesRepo = '__RELEASES_REPO__'
    $script:Step = 'start'
    $script:Transcribing = $false

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    } catch {
        # Newer hosts already negotiate TLS 1.2; nothing to do.
    }

    $Root = $env:AOC_INSTALL_ROOT
    if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA 'AnswerOnCue' }
    $DataDir = Join-Path $Root 'data'
    $UpdatesDir = Join-Path $Root 'updates'
    $AppDir = Join-Path $Root 'app'
    $IsUpdate = ($env:AOC_UPDATE -eq '1')
    $DryRun = ($env:AOC_INSTALL_DRY_RUN -eq '1')

    function Start-Step([string]$Name) {
        $script:Step = $Name
        Write-Host "==> $Name"
    }

    function Write-Utf8([string]$Path, [string]$Text) {
        [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
    }

    # Runs a native command; stderr output is not an error, only the exit code is.
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
            throw "command '$Exe' failed with exit code $code"
        }
    }

    function Get-Download([string]$Url, [string]$Target) {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Target -Headers @{ 'User-Agent' = 'answeroncue-installer' }
    }

    function Read-Checksum([string]$Path) {
        $line = (Get-Content -LiteralPath $Path -TotalCount 1).Trim()
        return ($line -split '\s+')[0].ToLowerInvariant()
    }

    function Assert-Checksum([string]$File, [string]$ChecksumFile) {
        $expected = Read-Checksum $ChecksumFile
        $actual = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "checksum mismatch for file $(Split-Path -Leaf $File)"
        }
    }

    function Write-UpdateFailure([string]$Message) {
        if (-not $IsUpdate) { return }
        try {
            $from = $null
            $statePath = Join-Path $DataDir 'launcher.json'
            if (Test-Path -LiteralPath $statePath) {
                $from = (Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json).version
            }
            $status = [ordered]@{
                ok = $false
                version = "$script:Version"
                from_version = $from
                message = $Message
                at = (Get-Date).ToUniversalTime().ToString('o')
            }
            New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
            Write-Utf8 (Join-Path $DataDir 'update-status.json') ($status | ConvertTo-Json)
        } catch {
            Write-Host "Could not write update-status.json: $($_.Exception.Message)"
        }
    }

    function Start-Launcher([string]$Version, [bool]$NoBrowser) {
        $backend = Join-Path (Join-Path $AppDir $Version) 'backend'
        $pythonw = Join-Path (Join-Path (Join-Path (Join-Path $AppDir $Version) '.venv') 'Scripts') 'pythonw.exe'
        $launcherArgs = @('-m', 'distribution.launcher')
        if ($NoBrowser) { $launcherArgs += '--no-browser' }
        $env:AOC_INSTALL_ROOT = $Root
        Start-Process -FilePath $pythonw -ArgumentList $launcherArgs -WorkingDirectory $backend
    }

    $script:Version = ''
    try {
        New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'logs') | Out-Null
        New-Item -ItemType Directory -Force -Path $UpdatesDir | Out-Null
        try {
            Start-Transcript -Append -Path (Join-Path (Join-Path $DataDir 'logs') 'install.log') | Out-Null
            $script:Transcribing = $true
        } catch {
            Write-Host 'Could not write install.log (continuing without a transcript).'
        }

        # 1. Resolve the source and the version.
        Start-Step 'Resolving the release to install'
        $source = $env:AOC_RELEASE_SOURCE
        $baseUrl = $null
        if ($source) {
            $candidates = @()
            foreach ($file in Get-ChildItem -LiteralPath $source -Filter 'answeroncue-*.zip' -File) {
                if ($file.Name -match '^answeroncue-(\d+\.\d+\.\d+)\.zip$') {
                    $candidates += [pscustomobject]@{ Version = $Matches[1]; Sort = [version]$Matches[1] }
                }
            }
            if ($candidates.Count -eq 0) { throw "there is no answeroncue-<version>.zip file in the folder $source" }
            $script:Version = ($candidates | Sort-Object -Property Sort -Descending | Select-Object -First 1).Version
        } else {
            $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$ReleasesRepo/releases/latest" -Headers @{ 'Accept' = 'application/vnd.github+json'; 'User-Agent' = 'answeroncue-installer' }
            $script:Version = ([string]$release.tag_name).TrimStart('v')
            if ($script:Version -notmatch '^\d+\.\d+\.\d+$') { throw "invalid version number: $($release.tag_name)" }
            $baseUrl = "https://github.com/$ReleasesRepo/releases/download/v$($script:Version)"
        }
        $Version = $script:Version
        $zipName = "answeroncue-$Version.zip"
        $VersionDir = Join-Path $AppDir $Version
        $Backend = Join-Path $VersionDir 'backend'
        $Venv = Join-Path $VersionDir '.venv'
        $VenvPython = Join-Path (Join-Path $Venv 'Scripts') 'python.exe'
        $Uv = Join-Path (Join-Path $Root 'uv') 'uv.exe'
        Write-Host "Version: $Version"

        $alreadyDone = (Test-Path -LiteralPath (Join-Path $VersionDir '.complete')) -and (-not $IsUpdate) -and (-not $DryRun)

        if (-not $alreadyDone) {
            # Download (or copy) and verify the package first; the running version is not touched.
            Start-Step 'Downloading and verifying the package'
            $zipPath = Join-Path $UpdatesDir $zipName
            $shaPath = "$zipPath.sha256"
            if ($source) {
                Copy-Item -LiteralPath (Join-Path $source $zipName) -Destination $zipPath -Force
                Copy-Item -LiteralPath (Join-Path $source "$zipName.sha256") -Destination $shaPath -Force
            } else {
                Get-Download "$baseUrl/$zipName" $zipPath
                Get-Download "$baseUrl/$zipName.sha256" $shaPath
            }
            Assert-Checksum $zipPath $shaPath

            Start-Step 'Extracting'
            New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
            $partial = "$VersionDir.partial"
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Recurse -Force }
            if (Test-Path -LiteralPath $VersionDir) { Remove-Item -LiteralPath $VersionDir -Recurse -Force }
            Expand-Archive -LiteralPath $zipPath -DestinationPath $partial -Force
            Move-Item -LiteralPath $partial -Destination $VersionDir

            $env:UV_PYTHON_INSTALL_DIR = Join-Path $Root 'python'
            $env:UV_CACHE_DIR = Join-Path (Join-Path $Root 'cache') 'uv'
            $env:UV_PYTHON_PREFERENCE = 'only-managed'

            if ($DryRun) {
                Start-Step 'Dry run: the uv commands were only printed'
                Write-Host "DRY RUN: download uv $UvVersion to $(Split-Path -Parent $Uv)"
                Write-Host "DRY RUN: $Uv python install 3.13"
                Write-Host "DRY RUN: $Uv venv --python 3.13 $Venv"
                foreach ($req in Get-ChildItem -LiteralPath (Join-Path $Backend 'requirements') -Filter '*.txt') {
                    Write-Host "DRY RUN: $Uv pip install --python $VenvPython -r $($req.FullName)"
                }
                Write-Host "INSTALL DRY RUN OK $Version"
                return
            }

            # 3. uv, pinned and verified.
            if (-not (Test-Path -LiteralPath $Uv)) {
                Start-Step "Fetching uv $UvVersion"
                $uvZipName = 'uv-x86_64-pc-windows-msvc.zip'
                $uvBase = "https://github.com/astral-sh/uv/releases/download/$UvVersion"
                $uvZip = Join-Path $UpdatesDir $uvZipName
                Get-Download "$uvBase/$uvZipName" $uvZip
                Get-Download "$uvBase/$uvZipName.sha256" "$uvZip.sha256"
                Assert-Checksum $uvZip "$uvZip.sha256"
                Expand-Archive -LiteralPath $uvZip -DestinationPath (Split-Path -Parent $Uv) -Force
            }

            # 4. Python 3.13, private to the app. Never --default, never PATH.
            Start-Step 'Installing Python 3.13 (uv python install)'
            Invoke-Native $Uv @('python', 'install', '3.13')

            # 6. venv and runtime requirements.
            Start-Step 'Creating the environment and installing libraries (venv and requirements)'
            Invoke-Native $Uv @('venv', '--python', '3.13', $Venv)
            $reqArgs = @('pip', 'install', '--python', $VenvPython)
            foreach ($req in Get-ChildItem -LiteralPath (Join-Path $Backend 'requirements') -Filter '*.txt') {
                $reqArgs += @('-r', $req.FullName)
            }
            Invoke-Native $Uv $reqArgs
        }

        # 7. Update mode: only now stop the old server.
        if ($IsUpdate -and $env:AOC_SERVER_PORT) {
            Start-Step 'Stopping the running version'
            try {
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($env:AOC_SERVER_PORT)/api/system/shutdown" -Headers @{ 'X-AnswerOnCue' = '1' } -TimeoutSec 10 | Out-Null
            } catch {
                Write-Host "Shutting down through the API failed: $($_.Exception.Message)"
            }
            if ($env:AOC_WAIT_PID) {
                $old = Get-Process -Id ([int]$env:AOC_WAIT_PID) -ErrorAction SilentlyContinue
                if ($old) {
                    try {
                        Wait-Process -Id ([int]$env:AOC_WAIT_PID) -Timeout 60 -ErrorAction Stop
                    } catch {
                        Stop-Process -Id ([int]$env:AOC_WAIT_PID) -Force -ErrorAction SilentlyContinue
                    }
                }
            }
        }

        # 8. Migrations, voice, shortcuts, uninstall entry, pruning.
        Start-Step 'Configuring the app (postinstall)'
        $env:AOC_INSTALL_ROOT = $Root
        $env:PYTHONUTF8 = '1'
        $postArgs = @('-m', 'distribution.postinstall', '--version', $Version)
        if ($IsUpdate) { $postArgs += '--update' }
        Push-Location $Backend
        try {
            Invoke-Native $VenvPython $postArgs
        } catch {
            Pop-Location
            if ($IsUpdate) {
                $statePath = Join-Path $DataDir 'launcher.json'
                if (Test-Path -LiteralPath $statePath) {
                    $old = (Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json).version
                    if ($old) { Start-Launcher $old $true }
                }
            }
            throw
        }
        Pop-Location

        # 9. Finish.
        Start-Step 'Finishing'
        Copy-Item -LiteralPath (Join-Path $VersionDir 'uninstall.ps1') -Destination (Join-Path $Root 'uninstall.ps1') -Force
        Set-Content -LiteralPath (Join-Path $VersionDir '.complete') -Value $Version -Encoding ASCII
        Start-Launcher $Version $IsUpdate
        Remove-Item -LiteralPath (Join-Path $UpdatesDir 'update.lock') -Force -ErrorAction SilentlyContinue
        Write-Host "INSTALL OK $Version"
    } catch {
        $message = $_.Exception.Message
        Write-Host "INSTALL FAILED: $($script:Step): $message"
        Write-UpdateFailure "$($script:Step): $message"
        Remove-Item -LiteralPath (Join-Path $UpdatesDir 'update.lock') -Force -ErrorAction SilentlyContinue
        $global:AnswerOnCueExitCode = 1
    } finally {
        if ($script:Transcribing) {
            try { Stop-Transcript | Out-Null } catch { }
        }
    }
}

Install-AnswerOnCue
