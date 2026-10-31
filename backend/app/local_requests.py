"""Only the app's own page may change state that matters (CSRF, DNS rebinding)."""

import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

FORBIDDEN = "Access denied."
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _host_is_local(value: str) -> bool:
    parts = urlsplit(f"//{value}")
    if parts.hostname not in LOCAL_HOSTS:
        return False
    expected = os.environ.get("AOC_SERVER_PORT")
    return not expected or str(parts.port) == expected


def require_local_request(request: Request) -> None:
    """Header X-AnswerOnCue: 1, a local Host (with the server port) and a local Origin, if any."""
    if request.headers.get("x-answeroncue") != "1":
        raise HTTPException(403, FORBIDDEN)
    if not _host_is_local(request.headers.get("host", "")):
        raise HTTPException(403, FORBIDDEN)
    origin = request.headers.get("origin")
    if origin and not _host_is_local(urlsplit(origin).netloc):
        raise HTTPException(403, FORBIDDEN)
