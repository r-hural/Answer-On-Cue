"""Steps run by the installer inside the new version's venv (stdlib only)."""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from distribution import launcher

VOICE = "pl_PL-bass-high"
BACKUPS_KEPT = 3
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AnswerOnCue"
APP_NAME = "AnswerOnCue"
CSIDL_DESKTOPDIRECTORY = 0x10
CSIDL_PROGRAMS = 0x02


class PostinstallError(Exception):
    pass


def _db_path(data_dir: Path) -> Path:
    return data_dir / "answeroncue.db"


def backup_database(data_dir: Path, from_version: str | None) -> Path | None:
    database = _db_path(data_dir)
    if not database.is_file():
        return None
    backups = data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = backups / f"answeroncue-{from_version or 'none'}-{stamp}.db"
    source = sqlite3.connect(database)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    for old in sorted(backups.glob("answeroncue-*.db"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return target


def restore_database(data_dir: Path, backup: Path) -> None:
    database = _db_path(data_dir)
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    shutil.copyfile(backup, database)


def migrate(
    backend_dir: Path, env: dict[str, str], run: Callable[..., object] = subprocess.run
) -> None:
    try:
        result = run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(backend_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise PostinstallError(f"Could not run the migration: {exc}") from exc
    if result.returncode != 0:
        tail = (getattr(result, "stderr", "") or "").strip().splitlines()[-5:]
        raise PostinstallError("The database migration failed. " + " ".join(tail))


def ensure_voice(
    backend_dir: Path,
    env: dict[str, str],
    data_dir: Path,
    run: Callable[..., object] = subprocess.run,
) -> str | None:
    if (data_dir / "voices" / f"{VOICE}.onnx").is_file():
        return None
    warning = (
        f"Could not download the voice {VOICE}. "
        "The app still works; you can download the voice later."
    )
    try:
        result = run(
            [sys.executable, "-m", "app.features.voice.download", VOICE],
            cwd=str(backend_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:  # noqa: BLE001 - a missing voice must never fail an install
        return warning
    return None if result.returncode == 0 else warning


ShortcutWriter = Callable[[Path, str, str, Path, str], None]

CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"


def com_shortcut_writer(
    link: Path, target: str, arguments: str, workdir: Path, description: str
) -> None:
    """Saves a .lnk through IShellLinkW/IPersistFile, which take Unicode paths.

    WScript.Shell's Save() converts the path to the ANSI code page, so on a non-Polish Windows
    "AnswerOnCue.lnk" (or a Polish user name) turns into a folder that does not exist.
    """
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("data1", wintypes.DWORD),
            ("data2", wintypes.WORD),
            ("data3", wintypes.WORD),
            ("data4", ctypes.c_ubyte * 8),
        ]

    ole32 = ctypes.OleDLL("ole32")

    def guid(text: str) -> GUID:
        value = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value))
        return value

    def method(index: int, *argtypes: object) -> Callable[..., int]:
        # A COM method prototype: ctypes passes the interface pointer ("this") itself.
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, *argtypes)(index, f"m{index}")

    release = ctypes.WINFUNCTYPE(ctypes.c_ulong)(2, "Release")
    query = method(0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    set_string = ctypes.c_wchar_p
    link.parent.mkdir(parents=True, exist_ok=True)
    # S_FALSE (already initialised) is fine; a thread already in MTA mode works for this too.
    ctypes.windll.ole32.CoInitializeEx(None, 2)
    shell_link = ctypes.c_void_p()
    ole32.CoCreateInstance(
        ctypes.byref(guid(CLSID_SHELL_LINK)),
        None,
        1,  # CLSCTX_INPROC_SERVER
        ctypes.byref(guid(IID_SHELL_LINK_W)),
        ctypes.byref(shell_link),
    )
    persist = ctypes.c_void_p()
    try:
        method(20, set_string)(shell_link, target)  # SetPath
        method(11, set_string)(shell_link, arguments)  # SetArguments
        method(9, set_string)(shell_link, str(workdir))  # SetWorkingDirectory
        method(7, set_string)(shell_link, description)  # SetDescription
        query(shell_link, ctypes.byref(guid(IID_PERSIST_FILE)), ctypes.byref(persist))
        method(6, set_string, wintypes.BOOL)(persist, str(link), True)  # IPersistFile.Save
    finally:
        if persist:
            release(persist)
        release(shell_link)


def write_shortcuts(
    backend_dir: Path,
    desktop: Path,
    programs: Path,
    root: Path,
    writer: ShortcutWriter = com_shortcut_writer,
) -> list[Path]:
    pythonw = backend_dir.parent / ".venv" / "Scripts" / "pythonw.exe"
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        r"System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    menu = programs / APP_NAME
    specs = [
        (desktop / f"{APP_NAME}.lnk", str(pythonw), "-m distribution.launcher", backend_dir),
        (menu / f"{APP_NAME}.lnk", str(pythonw), "-m distribution.launcher", backend_dir),
        (
            menu / f"Uninstall {APP_NAME}.lnk",
            str(powershell),
            f'-NoProfile -ExecutionPolicy Bypass -File "{root / "uninstall.ps1"}"',
            root,
        ),
    ]
    try:
        for link, target, arguments, workdir in specs:
            writer(link, target, arguments, workdir, APP_NAME)
    except (OSError, AttributeError) as exc:  # AttributeError: no ctypes.windll off Windows
        raise PostinstallError(f"Could not create the shortcuts. {exc}") from exc
    return [spec[0] for spec in specs]


def winreg_writer(key_path: str, values: dict[str, str | int]) -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        for name, value in values.items():
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)


def register_uninstall(
    root: Path,
    version: str,
    writer: Callable[[str, dict[str, str | int]], None] = winreg_writer,
) -> None:
    uninstall = root / "uninstall.ps1"
    writer(
        UNINSTALL_KEY,
        {
            "DisplayName": APP_NAME,
            "DisplayVersion": version,
            "Publisher": "AnswerOnCue",
            "InstallLocation": str(root),
            "UninstallString": (
                f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{uninstall}"'
            ),
            "NoModify": 1,
            "NoRepair": 1,
        },
    )


def prune_versions(root: Path, keep: set[str]) -> list[str]:
    app_dir = root / "app"
    removed: list[str] = []
    if not app_dir.is_dir():
        return removed
    for entry in sorted(app_dir.iterdir()):
        if not entry.is_dir() or entry.name in keep:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(entry.name)
    return removed


def write_status(
    data_dir: Path, ok: bool, version: str, from_version: str | None, message: str | None
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "version": version,
        "from_version": from_version,
        "message": message,
        "at": datetime.now(UTC).isoformat(),
    }
    (data_dir / "update-status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def shell_folder(csidl: int) -> Path:
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, 0, buffer)
    return Path(buffer.value)


def _folder(argument: str | None, env_name: str, csidl: int) -> Path:
    if argument:
        return Path(argument)
    if os.environ.get(env_name):
        return Path(os.environ[env_name])
    return shell_folder(csidl)


def main(
    argv: list[str] | None = None,
    *,
    run: Callable[..., object] = subprocess.run,
    registry_writer: Callable[[str, dict[str, str | int]], None] = winreg_writer,
    shortcut_writer: ShortcutWriter = com_shortcut_writer,
) -> int:
    parser = argparse.ArgumentParser(prog="distribution.postinstall")
    parser.add_argument("--root")
    parser.add_argument("--version", required=True)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--desktop")
    parser.add_argument("--programs")
    parser.add_argument("--release-source")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else launcher.install_root()
    data_dir = root / "data"
    backend_dir = Path(__file__).resolve().parents[1]
    version = args.version
    state = launcher._load_state(data_dir)
    from_version = state.get("version")
    env = launcher.server_env(root, state)

    backup = backup_database(data_dir, from_version)
    try:
        migrate(backend_dir, env, run)
    except PostinstallError as exc:
        if backup is not None:
            restore_database(data_dir, backup)
        write_status(data_dir, False, version, from_version, str(exc))
        print(f"POSTINSTALL FAILED: {exc}")
        return 2

    warning = ensure_voice(backend_dir, env, data_dir, run)
    if warning:
        print(f"WARNING: {warning}")
    try:
        desktop = _folder(args.desktop, "AOC_DESKTOP_DIR", CSIDL_DESKTOPDIRECTORY)
        programs = _folder(args.programs, "AOC_PROGRAMS_DIR", CSIDL_PROGRAMS)
        write_shortcuts(backend_dir, desktop, programs, root, shortcut_writer)
    except (PostinstallError, OSError) as exc:
        print(f"WARNING: {exc}")
    if os.environ.get("AOC_UNINSTALL_SKIP_REGISTRY") != "1":
        try:
            register_uninstall(root, version, registry_writer)
        except OSError as exc:
            print(f"WARNING: could not write the uninstall entry: {exc}")

    previous = from_version if from_version and from_version != version else state.get("previous")
    state["version"] = version
    state["previous"] = previous
    release_source = args.release_source or os.environ.get("AOC_RELEASE_SOURCE")
    if release_source:
        state["release_source"] = release_source
    launcher._save_state(data_dir, state)

    keep = {version}
    if previous:
        keep.add(previous)
    prune_versions(root, keep)
    write_status(data_dir, True, version, from_version, warning)
    print(f"POSTINSTALL OK {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
