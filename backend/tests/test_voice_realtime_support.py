"""A fake OpenAI Realtime server on loopback for the reader tests (no tests here)."""

import base64
import json
import logging
import threading
from collections.abc import Callable

import openai
from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.sync.server import serve

KEY = "sk-test-realtime0123456789abcdef"
RESPONSE_ID = "resp_fake_1"
CHUNK = 12_000  # bytes of PCM per delta: 250 ms at 24 kHz, 16-bit
# The fake server's own handshake log (it prints the client's headers) stays out of the tests' logs.
SERVER_LOG = logging.getLogger("tests.fake_realtime")
SERVER_LOG.setLevel(logging.WARNING)
USAGE = {
    "input_tokens": 120,
    "output_tokens": 400,
    "total_tokens": 520,
    "input_token_details": {"text_tokens": 120, "audio_tokens": 0, "cached_tokens": 0},
    "output_token_details": {"text_tokens": 20, "audio_tokens": 380},
}


class FakeRealtimeServer:
    """Scripted behaviours, one per connection (the last one repeats):
    ok, stall, error, error_rate, error_server, incomplete, failed, no_audio, drop,
    bad_base64, not_json, no_delta, reject:<status>."""

    def __init__(self, behaviours: str | list[str] = "ok", *, chunks: int = 2) -> None:
        self.behaviours = [behaviours] if isinstance(behaviours, str) else list(behaviours)
        self.chunks = chunks
        self.odd_byte = False
        self.distinct = False  # each connection's audio differs (its PCM byte carries the index)
        self.paths: list[str] = []
        self.authorizations: list[str | None] = []
        self.events: list[list[dict]] = []  # client events per connection
        self.closed = threading.Event()  # a connection ended (the client closed or dropped)
        self.on_response_create: Callable[[], None] | None = None
        self.connections = 0
        self._lock = threading.Lock()
        self._server = None
        self._thread: threading.Thread | None = None

    # ---- lifecycle ----

    def __enter__(self) -> "FakeRealtimeServer":
        self._server = serve(
            self._handle,
            "127.0.0.1",
            0,
            process_request=self._process_request,
            compression=None,
            ping_interval=None,
            logger=SERVER_LOG,
        )
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None

    def client(self) -> openai.OpenAI:
        return openai.OpenAI(
            api_key=KEY, websocket_base_url=f"ws://127.0.0.1:{self.port}/v1", max_retries=0
        )

    # ---- behaviour ----

    def _next(self) -> tuple[int, str]:
        with self._lock:
            index = self.connections
            self.connections += 1
        return index, self.behaviours[min(index, len(self.behaviours) - 1)]

    def _process_request(self, connection, request):
        self.paths.append(request.path)
        self.authorizations.append(request.headers.get("authorization"))
        connection.index, behaviour = self._next()
        connection.behaviour = behaviour
        if behaviour.startswith("reject:"):
            status = int(behaviour.split(":", 1)[1])
            return Response(status, "Rejected", Headers([("Content-Length", "0")]), b"")
        return None

    def _send(self, connection, event: dict) -> None:
        connection.send(json.dumps(event))

    def _handle(self, connection) -> None:
        behaviour = connection.behaviour
        events: list[dict] = []
        self.events.append(events)
        try:
            self._send(connection, {"type": "session.created", "event_id": "e0", "session": {}})
            for raw in connection:
                event = json.loads(raw)
                events.append(event)
                if event.get("type") == "response.create":
                    if self.on_response_create is not None:
                        self.on_response_create()
                    self._respond(connection, behaviour, connection.index)
                    if behaviour == "drop":
                        return
        except Exception:  # noqa: BLE001 - the client closing mid-send ends the handler
            pass
        finally:
            self.closed.set()

    def pcm(self, connection_index: int, index: int) -> bytes:
        """The PCM of one delta, as sent (without the odd byte)."""
        first = connection_index * 16 if self.distinct else 0
        return bytes([(first + index) % 256]) * CHUNK

    def _respond(self, connection, behaviour: str, connection_index: int) -> None:
        if behaviour == "stall":
            return
        if behaviour == "drop":
            connection.close()
            return
        errors = {
            "error": ("invalid_request_error", "x"),
            "error_rate": ("invalid_request_error", "rate_limit_exceeded"),
            "error_server": ("server_error", "x"),
        }
        if behaviour in errors:
            kind, code = errors[behaviour]
            self._send(
                connection,
                {
                    "type": "error",
                    "event_id": "e9",
                    "error": {"type": kind, "code": code, "message": "secret detail"},
                },
            )
            return
        if behaviour == "not_json":
            connection.send("{not json")
            return
        if behaviour == "no_delta":
            self._send(connection, {"type": "response.output_audio.delta", "event_id": "d0"})
            return
        if behaviour != "no_audio":
            for index in range(self.chunks):
                pcm = self.pcm(connection_index, index)
                if self.odd_byte and index == self.chunks - 1:
                    pcm += b"\x01"
                self._send(
                    connection,
                    {
                        "type": "response.output_audio.delta",
                        "event_id": f"d{index}",
                        "response_id": RESPONSE_ID,
                        "item_id": "item_1",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "%%% not base64 %%%"
                        if behaviour == "bad_base64"
                        else base64.b64encode(pcm).decode("ascii"),
                    },
                )
            self._send(
                connection,
                {
                    "type": "response.output_audio.done",
                    "event_id": "a1",
                    "response_id": RESPONSE_ID,
                    "item_id": "item_1",
                    "output_index": 0,
                    "content_index": 0,
                },
            )
            self._send(
                connection,
                {
                    "type": "response.output_audio_transcript.done",
                    "event_id": "t1",
                    "response_id": RESPONSE_ID,
                    "item_id": "item_1",
                    "output_index": 0,
                    "content_index": 0,
                    "transcript": "przeczytany tekst",
                },
            )
        status = {"incomplete": "incomplete", "failed": "failed"}.get(behaviour, "completed")
        self._send(
            connection,
            {
                "type": "response.done",
                "event_id": "r1",
                "response": {
                    "id": RESPONSE_ID,
                    "object": "realtime.response",
                    "status": status,
                    "status_details": None,
                    "output": [],
                    "usage": USAGE,
                },
            },
        )
