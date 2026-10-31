import subprocess
from pathlib import Path

import pytest

from app.config import Settings, load_settings
from app.features.voice import catalog
from app.features.voice.download import voice_urls
from app.features.voice.piper_catalog import REPO_URL
from app.features.voice.service import resolve_voice
from distribution import postinstall

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("AOC_VOICE_NAME", "AOC_VOICE_PROVIDER", "AOC_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)


def test_default_voice_is_bass_high():
    assert Settings(_env_file=None).voice_name == "pl_PL-bass-high"
    assert catalog.DEFAULT_PIPER_VOICE == "pl_PL-bass-high"
    assert catalog.DEFAULT_PIPER_VOICE in catalog.PIPER_VOICES
    assert postinstall.VOICE == "pl_PL-bass-high"
    example = (BACKEND / "env-example.txt").read_text(encoding="utf-8")
    assert "AOC_VOICE_NAME=pl_PL-bass-high" in example
    assert "AOC_VOICE_NAME=pl_PL-gosia-medium" not in example


def test_explicit_voice_name_is_kept(tmp_path):
    env_file = tmp_path / "settings.env"
    env_file.write_text("AOC_VOICE_NAME=pl_PL-gosia-medium\n", encoding="utf-8")
    data = tmp_path / "data"
    user = load_settings(base_env_file=None, env_file_path=env_file, data_dir=data)
    assert user.voice_name == "pl_PL-gosia-medium"
    base = load_settings(
        base_env_file=env_file, env_file_path=tmp_path / "missing.env", data_dir=data
    )
    assert base.voice_name == "pl_PL-gosia-medium"


def test_piper_fallback_is_bass_high(settings):
    azure = settings.model_copy(update={"voice_name": "pl-PL-ZofiaNeural"})
    assert resolve_voice(azure, "piper", None) == "pl_PL-bass-high"


def test_postinstall_fetches_bass_high(tmp_path):
    calls = []

    def spy(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    data = tmp_path / "data"
    assert postinstall.ensure_voice(BACKEND, {}, data, spy) is None
    assert calls[0][-3:] == ["-m", "app.features.voice.download", "pl_PL-bass-high"]

    (data / "voices").mkdir(parents=True)
    (data / "voices" / "pl_PL-bass-high.onnx").write_bytes(b"x")
    calls.clear()
    assert postinstall.ensure_voice(BACKEND, {}, data, spy) is None
    assert calls == []


def test_bass_high_download_urls():
    assert voice_urls("pl_PL-bass-high") == (
        f"{REPO_URL}/pl/pl_PL/bass/high/pl_PL-bass-high.onnx",
        f"{REPO_URL}/pl/pl_PL/bass/high/pl_PL-bass-high.onnx.json",
    )
