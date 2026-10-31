import io
import wave
from pathlib import Path

import pytest

from app.config import Settings
from app.features.providers.base import Synthesizer
from app.features.providers.types import ProviderError, VoiceSpec
from app.features.voice.piper_adapter import NOT_INSTALLED_MESSAGE, PiperSynthesizer

VOICE = "pl_PL-gosia-medium"
TEXT = "Jakie znaczenie miała bitwa pod Grunwaldem 15 lipca 1410 roku?"


class FakePiperVoice:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def synthesize_wav(self, text, wav_file, syn_config=None, set_wav_format=True):
        self.calls.append({"text": text, "length_scale": syn_config.length_scale})
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * int(0.3 * 22050))


def _install(voices_dir: Path, voice: str = VOICE) -> None:
    voices_dir.mkdir(parents=True, exist_ok=True)
    (voices_dir / f"{voice}.onnx").write_bytes(b"model")
    (voices_dir / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")


def _synth(tmp_path, fake: FakePiperVoice, loads: list[Path] | None = None) -> PiperSynthesizer:
    def loader(path: Path) -> FakePiperVoice:
        if loads is not None:
            loads.append(path)
        return fake

    _install(tmp_path)
    return PiperSynthesizer(tmp_path, loader=loader)


def test_missing_model_unavailable(tmp_path):
    synth = PiperSynthesizer(tmp_path / "empty")
    assert not synth.is_installed(VOICE)
    with pytest.raises(ProviderError) as info:
        synth.synthesize(TEXT, VoiceSpec(voice=VOICE), operation_id="op")
    assert info.value.kind == "unavailable"
    assert info.value.provider == "piper"
    assert info.value.message == NOT_INSTALLED_MESSAGE
    assert NOT_INSTALLED_MESSAGE == (
        "The Piper voice is not installed. Run scripts/get-piper-voice.ps1."
    )


def test_model_without_config_is_not_installed(tmp_path):
    (tmp_path / f"{VOICE}.onnx").write_bytes(b"model")
    assert not PiperSynthesizer(tmp_path).is_installed(VOICE)


def test_corrupt_model_unavailable(tmp_path):
    def loader(path: Path):
        raise RuntimeError("[ONNXRuntimeError] : 7 : INVALID_PROTOBUF")

    _install(tmp_path)
    synth = PiperSynthesizer(tmp_path, loader=loader)
    with pytest.raises(ProviderError) as info:
        synth.synthesize(TEXT, VoiceSpec(voice=VOICE), operation_id="op")
    assert info.value.kind == "unavailable"
    assert info.value.message == NOT_INSTALLED_MESSAGE


@pytest.mark.parametrize("voice", ["../secret", "a/b", "", "pl_PL gosia"])
def test_bad_voice_id_never_touches_disk(tmp_path, voice):
    synth = PiperSynthesizer(tmp_path, loader=lambda path: pytest.fail("loaded"))
    assert not synth.is_installed(voice)
    with pytest.raises(ProviderError) as info:
        synth.synthesize(TEXT, VoiceSpec(voice=voice), operation_id="op")
    assert info.value.kind == "unavailable"


def test_fake_loader_produces_wav_and_provenance(tmp_path):
    fake = FakePiperVoice()
    synth = _synth(tmp_path, fake)
    assert isinstance(synth, Synthesizer)
    result = synth.synthesize(TEXT, VoiceSpec(voice=VOICE), operation_id="op-1")
    assert result.mime == "audio/wav"
    with wave.open(io.BytesIO(result.audio)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getnframes() == int(0.3 * 22050)
    assert result.provenance.provider == "piper"
    assert result.provenance.model == VOICE
    assert result.provenance.operation_id == "op-1"
    assert result.usage.characters == len(TEXT)
    assert result.usage.audio_seconds == pytest.approx(0.3, abs=0.01)
    assert fake.calls[0]["text"] == TEXT


def test_rate_maps_to_length_scale(tmp_path):
    fake = FakePiperVoice()
    synth = _synth(tmp_path, fake)
    synth.synthesize(TEXT, VoiceSpec(voice=VOICE, rate=1.25), operation_id="op")
    assert fake.calls[0]["length_scale"] == pytest.approx(1 / 1.25)


def test_model_loaded_once(tmp_path):
    fake = FakePiperVoice()
    loads: list[Path] = []
    synth = _synth(tmp_path, fake, loads)
    for _ in range(3):
        synth.synthesize(TEXT, VoiceSpec(voice=VOICE), operation_id="op")
    assert loads == [tmp_path / f"{VOICE}.onnx"]
    assert len(fake.calls) == 3


def test_engine_version_tracks_model_file(tmp_path):
    _install(tmp_path)
    synth = PiperSynthesizer(tmp_path)
    first = synth.engine_version(VOICE)
    assert first.startswith(synth.package_version + "+")
    assert len(first.split("+")[1]) == 12
    (tmp_path / f"{VOICE}.onnx").write_bytes(b"another model, another size")
    assert synth.engine_version(VOICE) != first


def _first_installed_polish_voice() -> tuple[Path, str] | None:
    voices_dir = Settings().effective_voices_dir
    for model in sorted(voices_dir.glob("pl_PL-*.onnx")):
        if model.with_name(model.name + ".json").is_file():
            return voices_dir, model.name.removesuffix(".onnx")
    return None


@pytest.mark.piper
def test_real_model_polish_sentence():
    found = _first_installed_polish_voice()
    if found is None:
        pytest.skip("no Piper pl_PL voice installed (run scripts/get-piper-voice.ps1)")
    voices_dir, voice = found
    result = PiperSynthesizer(voices_dir).synthesize(
        TEXT, VoiceSpec(voice=voice), operation_id="real"
    )
    with wave.open(io.BytesIO(result.audio)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() / wav.getframerate() > 1.0
