import html
import io
import re
import wave

import pytest

from app.features.providers.fakes import FakeSynthesizer
from app.features.providers.types import ProviderError
from app.features.voice import service, trial
from app.features.voice.trial import SAMPLE_QUESTIONS, render_trial


class FakeEngine(FakeSynthesizer):
    def __init__(self, missing: set[str] | None = None) -> None:
        super().__init__()
        self.missing = missing or set()

    def engine_version(self, voice: str) -> str:
        if voice in self.missing:
            raise ProviderError("unavailable", "piper", "The Piper voice is not installed.")
        return "fake"


@pytest.fixture
def engine(monkeypatch) -> FakeEngine:
    fake = FakeEngine(missing={"pl_PL-brak-medium"})
    monkeypatch.setattr(service, "get_synthesizer", lambda settings, provider: fake)
    return fake


def test_sample_questions():
    assert len(SAMPLE_QUESTIONS) == 12
    assert SAMPLE_QUESTIONS[0] == service.SAMPLE_TEXT
    assert SAMPLE_QUESTIONS[11] == (
        "Dlaczego 1 września 1939 r. uznaje się za początek II wojny światowej?"
    )


def test_trial_renders_page_and_files(settings, tmp_path, engine):
    out = tmp_path / "trial"
    voices = [("piper", "pl_PL-gosia-medium"), ("azure_speech", "pl-PL-ZofiaNeural")]
    index = render_trial(settings, out, voices)
    assert index == out / "index.html"
    wavs = sorted(out.rglob("*.wav"))
    assert len(wavs) == 24
    assert (out / "piper-pl_PL-gosia-medium" / "01.wav").is_file()
    assert (out / "azure_speech-pl-PL-ZofiaNeural" / "12.wav").is_file()
    with wave.open(io.BytesIO(wavs[0].read_bytes())) as wav:
        assert wav.getnframes() > 0
    page = index.read_text(encoding="utf-8")
    for question in SAMPLE_QUESTIONS:
        assert html.escape(question) in page
    sources = re.findall(r'<audio[^>]*src="([^"]+)"', page)
    assert page.count("<audio") == 24
    assert len(sources) == 24
    assert all(not src.startswith("/") and ":" not in src for src in sources)
    assert all((out / src).is_file() for src in sources)
    assert "ntonation" in page  # the listening guide is there
    assert all(call["voice"].rate == 1.0 for call in engine.calls)


def test_trial_skips_unavailable_voices(settings, tmp_path, engine):
    out = tmp_path / "trial"
    voices = [("piper", "pl_PL-gosia-medium"), ("piper", "pl_PL-brak-medium")]
    index = render_trial(settings, out, voices)
    assert len(list(out.rglob("*.wav"))) == 12
    assert not (out / "piper-pl_PL-brak-medium").exists()
    page = index.read_text(encoding="utf-8")
    assert page.count("<audio") == 12
    assert "pl_PL-brak-medium" not in page


def test_default_voices_are_installed_piper_and_configured_azure(settings, monkeypatch):
    voices_dir = settings.effective_voices_dir
    voices_dir.mkdir(parents=True)
    (voices_dir / "pl_PL-darkman-medium.onnx").write_bytes(b"model")
    (voices_dir / "pl_PL-darkman-medium.onnx.json").write_text("{}", encoding="utf-8")
    assert trial.available_voices(settings) == [("piper", "pl_PL-darkman-medium")]


def test_failed_synthesis_leaves_a_note(settings, tmp_path, monkeypatch):
    fake = FakeEngine()
    fake.error = ProviderError("rate_limit", "azure_speech", "Request limit exceeded.")
    monkeypatch.setattr(service, "get_synthesizer", lambda settings, provider: fake)
    index = render_trial(settings, tmp_path / "trial", [("azure_speech", "pl-PL-MarekNeural")])
    page = index.read_text(encoding="utf-8")
    assert page.count("<audio") == 0
    assert "Request limit exceeded." in page


def test_cli_without_voices_fails(settings, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trial, "get_settings", lambda: settings)
    assert trial.main([str(tmp_path / "trial")]) == 1
    assert "No voices available" in capsys.readouterr().out


def test_cli_prints_index_path(settings, tmp_path, monkeypatch, capsys, engine):
    monkeypatch.setattr(trial, "get_settings", lambda: settings)
    monkeypatch.setattr(
        trial, "available_voices", lambda settings, language="pl": [("piper", "pl_PL-gosia-medium")]
    )
    assert trial.main([str(tmp_path / "trial")]) == 0
    assert str(tmp_path / "trial" / "index.html") in capsys.readouterr().out
