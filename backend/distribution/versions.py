import re
from collections.abc import Iterable

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse(version: str) -> tuple[int, int, int] | None:
    match = _SEMVER.match(version.strip())
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def newest(versions: Iterable[str]) -> str | None:
    best: tuple[tuple[int, int, int], str] | None = None
    for version in versions:
        parsed = parse(version)
        if parsed is not None and (best is None or parsed > best[0]):
            best = (parsed, version.strip().removeprefix("v"))
    return best[1] if best else None
