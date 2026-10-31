import os

import pytest
from fastapi.testclient import TestClient

from app.config import APP_DIR, Settings
from app.features.providers import keys
from app.main import create_app

DEVELOPER_ENV = (APP_DIR.parent / ".env").resolve()


@pytest.fixture(autouse=True)
def _guard_developer_env(monkeypatch):
    # A test whose Settings keep the default base_env_file would edit the real backend/.env
    # (deleting a key removes it there too), so any read or write of that file fails the test.
    def _checked(original):
        def wrapper(path, *args):
            if path.resolve() == DEVELOPER_ENV:
                pytest.fail(f"test touched the developer's {DEVELOPER_ENV}")
            return original(path, *args)

        return wrapper

    monkeypatch.setattr(keys, "read_env", _checked(keys.read_env))
    monkeypatch.setattr(keys, "_write", _checked(keys._write))


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Never read the developer's real backend/.env: tests must not depend on local keys.
    return Settings(
        _env_file=None,
        base_env_file=None,
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "dist",
        env_file_path=tmp_path / ".env",
    )


def local_client(app, **kwargs) -> TestClient:
    """A client that calls like the app's own page, so guarded changes are accepted."""
    return TestClient(app, base_url="http://127.0.0.1", headers={"X-AnswerOnCue": "1"}, **kwargs)


@pytest.fixture
def client(settings) -> TestClient:
    return local_client(create_app(settings))


def pytest_collection_modifyitems(config, items):
    has_key = any(
        k.startswith("AOC_") and k.endswith("_API_KEY") and v for k, v in os.environ.items()
    )
    if has_key:
        return
    skip = pytest.mark.skip(reason="no AOC_*_API_KEY set")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
