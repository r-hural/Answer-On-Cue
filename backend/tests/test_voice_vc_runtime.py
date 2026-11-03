"""An old VC++ runtime makes importing onnxruntime kill the process; Piper must refuse first."""

import sys
from pathlib import Path

import pytest

from app.features.providers.types import ProviderError, VoiceSpec
from app.features.voice import piper_adapter, vc_runtime
from app.features.voice.piper_adapter import PiperSynthesizer

VOICE = "pl_PL-bass-high"
TEXT = "Jakie znaczenie miała bitwa pod Grunwaldem?"
OLD = "too old"


def _install(voices_dir: Path, voice: str = VOICE) -> None:
    voices_dir.mkdir(parents=True, exist_ok=True)
    (voices_dir / f"{voice}.onnx").write_bytes(b"model")
    (voices_dir / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")


@pytest.mark.parametrize(
    ("found", "ok"),
    [
        ((14, 34, 31938, 0), False),  # the machine that crashed
        ((14, 39, 99999, 0), False),
        ((14, 40, 33810, 0), True),
        ((14, 51, 36247, 0), True),
        ((15, 0, 0, 0), True),
        (None, False),  # no MSVCP140.dll at all
    ],
)
def test_problem_for_versions(found, ok):
    message = vc_runtime.problem_for(found)
    assert (message is None) is ok
    if not ok:
        assert vc_runtime.REDIST_URL in message
        assert ("14.34.31938.0" in message) is (found == (14, 34, 31938, 0))
        assert "choose another voice" in message


def test_old_runtime_refuses_before_loading(tmp_path):
    _install(tmp_path)
    synth = PiperSynthesizer(
        tmp_path, loader=lambda path: pytest.fail("loaded"), runtime_check=lambda: OLD
    )
    with pytest.raises(ProviderError) as info:
        synth.synthesize(TEXT, VoiceSpec(voice=VOICE), operation_id="op")
    assert info.value.kind == "unavailable"
    assert info.value.provider == "piper"
    assert info.value.message == OLD


def test_missing_voice_still_says_not_installed(tmp_path):
    synth = PiperSynthesizer(tmp_path, runtime_check=lambda: OLD)
    with pytest.raises(ProviderError) as info:
        synth.synthesize(TEXT, VoiceSpec(voice=VOICE), operation_id="op")
    assert info.value.message == piper_adapter.NOT_INSTALLED_MESSAGE


def test_speak_with_old_runtime_is_503_and_server_lives(client, settings, monkeypatch):
    _install(settings.effective_voices_dir)
    monkeypatch.setattr(piper_adapter, "runtime_problem", lambda: OLD)
    response = client.post("/api/voice/speak", json={"text": TEXT, "provider": "piper"})
    assert response.status_code == 503
    assert response.json()["detail"] == OLD
    assert client.get("/api/health").status_code == 200


@pytest.mark.skipif(sys.platform != "win32", reason="reads a Windows DLL")
def test_reads_the_loaded_msvcp140():
    found, path = vc_runtime._dll_version("msvcp140.dll")
    assert found is not None and found[0] == 14
    assert path.lower().endswith("msvcp140.dll")
