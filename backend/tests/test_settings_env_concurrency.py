"""Concurrent saves of settings.env (e2e flake: PermissionError [WinError 5] on os.replace)."""

import os
import threading

from app.features.providers import keys
from app.features.providers.keys import read_env, set_env_value


def test_concurrent_saves_keep_every_value(tmp_path):
    path = tmp_path / "settings.env"
    path.write_bytes(b"# kept\n")
    errors: list[BaseException] = []
    stop = threading.Event()

    def save(n: int) -> None:
        try:
            for value in range(15):
                set_env_value(path, f"AOC_SETTING_{n}", str(value))
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    def read() -> None:
        # Settings are read from the file by other requests meanwhile (an open handle).
        while not stop.is_set():
            try:
                with open(path, "rb") as handle:
                    handle.read()
            except OSError:
                pass

    reader = threading.Thread(target=read)
    reader.start()
    writers = [threading.Thread(target=save, args=(n,)) for n in range(8)]
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join()
    stop.set()
    reader.join()

    assert errors == []
    lines = read_env(path)
    assert lines[0] == "# kept\n"
    assert sorted(lines[1:]) == sorted(f"AOC_SETTING_{n}=14\n" for n in range(8))
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


def test_replace_is_retried_while_the_file_is_held(tmp_path, monkeypatch):
    path = tmp_path / "settings.env"
    real = os.replace
    failures = iter([PermissionError(13, "Access is denied")] * 3)

    def flaky(src, dst):
        error = next(failures, None)
        if error is not None:
            raise error
        real(src, dst)

    monkeypatch.setattr(keys, "REPLACE_PAUSE_S", 0, raising=False)
    monkeypatch.setattr(keys.os, "replace", flaky)
    set_env_value(path, "AOC_SCORING_WEIGHTS", "30")
    assert read_env(path) == ["AOC_SCORING_WEIGHTS=30\n"]
