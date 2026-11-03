"""Is the Visual C++ runtime new enough for onnxruntime (and so Piper)?

onnxruntime is built with a newer MSVC than the 2022-era runtimes many PCs still have; with an
MSVCP140.dll older than 14.40 its import crashes the whole process (access violation, no Python
error). So the check runs before Piper is imported, and reads the version of the very DLL that
Windows resolves for this process, not a fixed path.
"""

import sys
from functools import lru_cache

MIN_VERSION = (14, 40)
REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
OUTDATED_MESSAGE = (
    "Piper voices need a newer Microsoft Visual C++ Redistributable (x64, 14.40 or later; "
    "this computer has {found}). Install it from " + REDIST_URL + ", or choose another voice "
    "in Settings."
)


def _dll_version(name: str) -> tuple[tuple[int, int, int, int] | None, str]:
    """(version, path) of the DLL as this process would load it; (None, "") when it is missing."""
    import ctypes
    from ctypes import wintypes

    try:
        handle = ctypes.WinDLL(name)
    except OSError:
        return None, ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    buffer = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetModuleFileNameW(handle._handle, buffer, len(buffer)):
        return None, ""
    path = buffer.value

    version = ctypes.WinDLL("version")
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None, path
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return None, path
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None, path
    # VS_FIXEDFILEINFO: signature, struc version, then dwFileVersionMS and dwFileVersionLS.
    fields = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 4)).contents
    ms, ls = fields[2], fields[3]
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF), path


def problem_for(found: tuple[int, int, int, int] | None) -> str | None:
    """The message for a runtime too old (or missing) for onnxruntime; None when it is fine."""
    if found is not None and found[:2] >= MIN_VERSION:
        return None
    shown = ".".join(str(part) for part in found) if found else "none"
    return OUTDATED_MESSAGE.format(found=shown)


@lru_cache(maxsize=1)
def runtime_problem() -> str | None:
    """Checked once per process: installing the redistributable needs an app restart anyway."""
    if sys.platform != "win32":
        return None
    found, _path = _dll_version("msvcp140.dll")
    return problem_for(found)
