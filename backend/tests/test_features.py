from fastapi import FastAPI

import app.features as features_pkg
from app.main import include_feature_routers


def test_features_without_router_are_skipped(tmp_path, monkeypatch):
    package = tmp_path / "norouter"
    package.mkdir()
    (package / "__init__.py").write_text("")
    monkeypatch.setattr(features_pkg, "__path__", [*features_pkg.__path__, str(tmp_path)])
    names = include_feature_routers(FastAPI())
    assert "health" in names
    assert "norouter" not in names


def test_api_routes_are_prefixed():
    app = FastAPI()
    include_feature_routers(app)
    paths = list(app.openapi()["paths"])
    assert paths
    assert all(path.startswith("/api/") for path in paths)
