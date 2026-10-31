import json
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.features.recording import vad
from app.main import create_app
from tests.test_recording_api import upgrade

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "audio"
INFO = json.loads((FIXTURES / "fixtures.json").read_text(encoding="utf-8"))["files"]


def pcm_of(name: str) -> bytes:
    with wave.open(str(FIXTURES / name), "rb") as w:
        return w.readframes(w.getnframes())


def assert_matches_offsets(result, name: str) -> None:
    (start, end), *_ = INFO[name]["speech"]
    assert len(result.speech_intervals) == 1, result.speech_intervals
    got_start, got_end = result.speech_intervals[0]
    assert abs(got_start - start) <= 150
    assert abs(got_end - end) <= 200
    assert abs(result.leading_silence_ms - 2000) <= 150
    assert result.speech_ms == got_end - got_start
    assert result.trailing_silence_ms == result.duration_ms - got_end


def test_speech_mid_interval_matches_offsets():
    result = vad.analyze_pcm(pcm_of("speech_mid.wav"))
    assert result.engine == "webrtcvad-2/30ms"
    assert_matches_offsets(result, "speech_mid.wav")
    assert result.duration_ms == INFO["speech_mid.wav"]["duration_ms"]


def test_speech_noisy_same_within_tolerance():
    assert_matches_offsets(vad.analyze_pcm(pcm_of("speech_noisy.wav")), "speech_noisy.wav")


def test_click_only_no_speech_after_smoothing():
    result = vad.analyze_pcm(pcm_of("click_only.wav"))
    assert all(end - start < 90 for start, end in result.speech_intervals)
    assert result.speech_ms == 0
    assert result.leading_silence_ms == result.duration_ms


def test_silence_no_intervals_not_error():
    result = vad.analyze_pcm(pcm_of("silence.wav"))
    assert result.speech_intervals == []
    assert result.speech_ms == 0
    assert result.leading_silence_ms == result.duration_ms == 3000
    assert result.trailing_silence_ms == 3000


def test_merge_gaps_under_300ms():
    # 10 frames of 30 ms: speech 0-150, gap 270 ms, speech 420-570, gap 300 ms, speech 870-1020.
    decisions = [True] * 5 + [False] * 9 + [True] * 5 + [False] * 10 + [True] * 5
    assert vad.smooth(decisions, 30) == [(0, 570), (870, 1020)]


def test_drop_runs_under_90ms():
    decisions = [True] * 2 + [False] * 20 + [True] * 3 + [False] * 20 + [True]
    assert vad.smooth(decisions, 30) == [(660, 750)]
    assert vad.smooth([False] * 10, 30) == []


def test_energy_fallback_used_when_webrtcvad_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "webrtcvad", None)
    result = vad.analyze_pcm(pcm_of("speech_mid.wav"))
    assert result.engine == "energy-v1"
    assert_matches_offsets(result, "speech_mid.wav")
    assert vad.analyze_pcm(pcm_of("silence.wav")).speech_intervals == []
    assert vad.analyze_pcm(pcm_of("click_only.wav")).speech_ms == 0


@pytest.fixture
def api(tmp_path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", frontend_dist=tmp_path / "dist")
    upgrade(settings.effective_database_url)
    return TestClient(create_app(settings))


def test_analyze_endpoint_saves_result(api):
    data = (FIXTURES / "speech_mid.wav").read_bytes()
    recording_id = api.post(
        "/api/recordings",
        files={"file": ("a.wav", data, "audio/wav")},
        data={"purpose": "answer"},
    ).json()["id"]
    response = api.post(f"/api/recordings/{recording_id}/analyze")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "engine",
        "speech_intervals",
        "speech_ms",
        "leading_silence_ms",
        "trailing_silence_ms",
        "duration_ms",
    }
    assert body["engine"] == "webrtcvad-2/30ms"
    assert len(body["speech_intervals"]) == 1

    from app.db.session import SessionLocal
    from app.features.recording.models import Recording

    settings = api.app.state.settings
    with SessionLocal(settings.effective_database_url) as session:
        row = session.get(Recording, recording_id)
        assert row.vad_engine == "webrtcvad-2/30ms"
        assert json.loads(row.vad_json) == body
    assert api.post("/api/recordings/9999/analyze").status_code == 404
