import importlib
import importlib.util
import os
import pkgutil
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import app.features as features_pkg
from app.config import Settings, get_settings, load_settings


def include_feature_routers(app: FastAPI) -> list[str]:
    included: list[str] = []
    for info in sorted(pkgutil.iter_modules(features_pkg.__path__), key=lambda m: m.name):
        if not info.ispkg:
            continue
        module_name = f"{features_pkg.__name__}.{info.name}.router"
        if importlib.util.find_spec(module_name) is None:
            continue
        module = importlib.import_module(module_name)
        app.include_router(module.router, prefix="/api")
        included.append(info.name)
    return included


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="AnswerOnCue", version="0.1.0")
    app.state.settings = settings
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    include_feature_routers(app)
    _mount_frontend(app, settings)
    return app


def reload_settings(app: FastAPI) -> Settings:
    """Re-read the settings files (e.g. after a key change) and use them from now on."""
    current: Settings = app.state.settings
    settings = load_settings(
        data_dir=current.data_dir,
        frontend_dist=current.frontend_dist,
        env_file_path=current.env_file_path,
        base_env_file=current.base_env_file,
        database_url=current.database_url,
        voices_dir=current.voices_dir,
    )
    app.state.settings = settings
    return settings


def _is_plain_relative_path(path: str) -> bool:
    """Reject anything that could leave dist or name a UNC/drive path, before touching disk."""
    if not path or "\\" in path or ":" in path or path.startswith("/"):
        return False
    posix = PurePosixPath(path)
    return not posix.anchor and ".." not in posix.parts and not PureWindowsPath(path).anchor


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist.resolve()
    index = dist / "index.html"

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    if not index.is_file():

        @app.get("/", include_in_schema=False)
        def missing_dist() -> PlainTextResponse:
            return PlainTextResponse(
                "The interface has not been built. Run: scripts/dev.ps1 -Build",
                media_type="text/plain; charset=utf-8",
            )

        return

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if _is_plain_relative_path(path):
            candidate = Path(os.path.normpath(dist / path))
            if candidate.is_relative_to(dist) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
