"""The update installer is started from the server (pythonw, no console) and must really run."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

PROBE = """
$out = [System.IO.Path]::Combine($PSScriptRoot, 'probe-out.txt')
$hash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash
[System.Threading.Thread]::Sleep(1500)
[System.IO.File]::WriteAllText($out, "ran $($hash.Length)")
"""

# Runs in a pythonw child that exits right after the launch, like the server the installer stops.
LAUNCH = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.features.system.router import launch_installer
env = dict(os.environ, PSModulePath=r"C:\\Program Files\\PowerShell\\7\\Modules")
launch_installer(Path(sys.argv[2]), env)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launch")
def test_installer_runs_when_launched_from_pythonw(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    probe = tmp_path / "probe.ps1"
    probe.write_text(PROBE, encoding="ascii")
    launcher = tmp_path / "launch.py"
    launcher.write_text(LAUNCH, encoding="ascii")
    subprocess.run([str(pythonw), str(launcher), str(BACKEND), str(probe)], check=True, timeout=60)

    out = tmp_path / "probe-out.txt"
    deadline = time.monotonic() + 30
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert out.exists(), "powershell.exe did not run the installer script"
    assert out.read_text(encoding="utf-8") == "ran 64"
