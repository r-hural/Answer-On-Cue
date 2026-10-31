import io
import threading
import wave

import pytest

from app.features.providers.fakes import silent_wav
from app.features.voice.cache import VoiceCache, cache_key, normalize_text, wav_duration_ms

BASE = {
    "provider": "piper",
    "voice": "pl_PL-gosia-medium",
    "rate": 1.0,
    "engine_version": "1.8.0+abcdef012345",
    "text": "Jakie znaczenie miała bitwa pod Grunwaldem?",
}


def _key(**changes) -> str:
    return cache_key(**{**BASE, **changes})


def test_key_changes_with_each_field():
    base = _key()
    assert len(base) == 64 and all(c in "0123456789abcdef" for c in base)
    changed = [
        _key(provider="azure_speech"),
        _key(voice="pl_PL-darkman-medium"),
        _key(rate=1.25),
        _key(engine_version="riff-24khz-16bit-mono-pcm"),
        _key(text="Inne pytanie?"),
    ]
    assert base not in changed
    assert len(set(changed)) == len(changed)
    # rate is keyed to two decimals, so float noise does not create a new file
    assert _key(rate=1.0000001) == base


def test_key_nfc_normalized():
    composed = "Wyjaśnij pojęcie: sól"
    decomposed = "Wyjaśnij pojęcie: sól"
    assert composed != decomposed
    assert _key(text=composed) == _key(text=decomposed)
    assert _key(text="  " + composed + "\n") == _key(text=composed)
    assert normalize_text(decomposed) == composed


def test_put_get_roundtrip_with_sidecar(tmp_path):
    cache = VoiceCache(tmp_path / "voice-cache")
    key = _key()
    assert cache.get(key) is None
    audio = silent_wav(0.3)
    path = cache.put(key, audio, {**BASE, "duration_ms": wav_duration_ms(audio)})
    assert path == tmp_path / "voice-cache" / f"{key}.wav"
    assert cache.get(key) == path
    assert path.read_bytes() == audio
    meta = cache.read_meta(key)
    assert meta["provider"] == "piper"
    assert meta["text"] == BASE["text"]
    assert meta["duration_ms"] == 300
    assert "created_at" in meta


def test_atomic_concurrent_put(tmp_path):
    cache = VoiceCache(tmp_path / "voice-cache")
    key = _key()
    audio = silent_wav(1.0)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def put() -> None:
        try:
            barrier.wait()
            for _ in range(20):
                cache.put(key, audio, dict(BASE))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=put) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with wave.open(io.BytesIO(cache.get(key).read_bytes())) as wav:
        assert wav.getnframes() == 16000
    names = sorted(p.name for p in (tmp_path / "voice-cache").iterdir())
    assert names == [f"{key}.json", f"{key}.wav"]


@pytest.mark.parametrize("bad", ["", "abc", "../" + "a" * 61, "A" * 64, "g" * 64, "a" * 65])
def test_invalid_key_rejected(tmp_path, bad):
    cache = VoiceCache(tmp_path / "voice-cache")
    with pytest.raises(ValueError):
        cache.path(bad)
    assert cache.get(bad) is None
    with pytest.raises(ValueError):
        cache.put(bad, silent_wav(), {})
