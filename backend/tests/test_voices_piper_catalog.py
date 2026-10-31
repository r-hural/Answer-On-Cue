import json
import os
import threading
import time

import httpx2
import pytest

from app.features.languages.profiles import PROFILES
from app.features.voice import download, downloads, piper_catalog
from app.features.voice.catalog import PIPER_VOICES
from app.features.voice.download import DownloadError, download_voice, voice_urls
from app.features.voice.piper_catalog import (
    CATALOG_FILE,
    MAX_CATALOG_BYTES,
    PINNED,
    REPO_URL,
    VOICES_JSON_URL,
    PiperEntry,
    label,
    load_catalog,
    offered,
    refresh,
)

MODEL = b"\x08" * 1_100_000
CONFIG = json.dumps({"audio": {"sample_rate": 22050}}).encode()

FACTS = {
    "pl_PL-gosia-medium": ("pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx", 63201294),
    "pl_PL-darkman-medium": ("pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx", 63201294),
    "pl_PL-mc_speech-medium": (
        "pl/pl_PL/mc_speech/medium/pl_PL-mc_speech-medium.onnx",
        63201294,
    ),
    "pl_PL-bass-high": ("pl/pl_PL/bass/high/pl_PL-bass-high.onnx", 114204024),
    "en_GB-cori-high": ("en/en_GB/cori/high/en_GB-cori-high.onnx", 114219352),
    "de_DE-thorsten-high": ("de/de_DE/thorsten/high/de_DE-thorsten-high.onnx", 113895201),
    "fr_FR-siwis-medium": ("fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx", 63201294),
    "es_ES-davefx-medium": ("es/es_ES/davefx/medium/es_ES-davefx-medium.onnx", 63201294),
    "it_IT-serena-high": ("it/it_IT/serena/high/it_IT-serena-high.onnx", 114204024),
    "uk_UA-tetiana-high": ("uk/uk_UA/tetiana/high/uk_UA-tetiana-high.onnx", 114204024),
}


def entry_json(key: str, *, speakers: int = 1, size: int = 63201294) -> dict:
    prefix, name, quality = key.split("-")
    family = prefix.split("_")[0]
    model = f"{family}/{prefix}/{name}/{quality}/{key}.onnx"
    return {
        "key": key,
        "name": name,
        "language": {
            "code": prefix,
            "family": family,
            "region": prefix.split("_")[1],
            "name_native": "x",
            "name_english": "x",
            "country_english": "x",
        },
        "quality": quality,
        "num_speakers": speakers,
        "speaker_id_map": {},
        "files": {
            model: {"size_bytes": size, "md5_digest": "0" * 32},
            model + ".json": {"size_bytes": 5000, "md5_digest": "0" * 32},
            f"{family}/{prefix}/{name}/{quality}/MODEL_CARD": {
                "size_bytes": 300,
                "md5_digest": "0" * 32,
            },
        },
        "aliases": [],
    }


def voices_json(entries: list[dict]) -> str:
    return json.dumps({entry["key"]: entry for entry in entries})


def full_list(*extra: dict) -> str:
    return voices_json([entry_json(key) for key in PINNED] + list(extra))


EXTRA = (
    entry_json("en_GB-alba-medium"),
    entry_json("en_GB-vctk-medium", speakers=109),
    entry_json("en_US-amy-medium"),
    entry_json("de_DE-kerstin-low"),
)


def client(handler) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def write_cache(folder, text: str | bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / CATALOG_FILE
    if isinstance(text, str):
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(text)


def age(path, days: float) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


@pytest.fixture(autouse=True)
def _reset():
    piper_catalog._loaded.clear()
    downloads._states.clear()
    yield
    piper_catalog._loaded.clear()
    downloads._states.clear()


def test_pinned_covers_every_profile():
    for profile in PROFILES.values():
        entry = PINNED[profile.default_piper_voice]
        assert entry.prefix == profile.piper_prefix
        assert entry.num_speakers == 1
    assert {key: (e.model_path, e.model_bytes) for key, e in PINNED.items()} == FACTS
    assert list(PINNED) == list(FACTS)


def test_load_without_cache_is_pinned(tmp_path):
    assert load_catalog(tmp_path) == PINNED
    assert load_catalog(tmp_path / "missing") == PINNED


def test_load_valid_cache(tmp_path):
    write_cache(tmp_path, full_list(*EXTRA))
    catalog = load_catalog(tmp_path)
    assert "en_GB-alba-medium" in catalog
    assert catalog["en_GB-vctk-medium"].num_speakers == 109
    assert "de_DE-kerstin-low" in catalog
    assert "en_US-amy-medium" not in catalog
    assert set(PINNED) <= set(catalog)


@pytest.mark.parametrize(
    "body",
    ["{not json", json.dumps([1, 2]), b"{" + b" " * (MAX_CATALOG_BYTES + 1) + b"}"],
    ids=["invalid", "list", "too-big"],
)
def test_bad_cache_falls_back(tmp_path, body):
    write_cache(tmp_path, body)
    assert load_catalog(tmp_path) == PINNED


def test_cache_without_a_default_falls_back(tmp_path):
    keys = [key for key in PINNED if key != "uk_UA-tetiana-high"]
    write_cache(tmp_path, voices_json([entry_json(key) for key in keys]))
    assert load_catalog(tmp_path) == PINNED


def test_unsafe_path_is_left_out(tmp_path):
    bad = entry_json("en_GB-alba-medium")
    bad["files"] = {"../../x/en_GB-alba-medium.onnx": {"size_bytes": 1}}
    write_cache(tmp_path, full_list(bad))
    assert "en_GB-alba-medium" not in load_catalog(tmp_path)


def test_offered(tmp_path):
    write_cache(tmp_path, full_list(*EXTRA))
    catalog = load_catalog(tmp_path)
    english = offered(catalog, PROFILES["en"])
    assert [e.key for e in english] == ["en_GB-cori-high", "en_GB-alba-medium"]
    assert [e.key for e in offered(catalog, PROFILES["pl"])] == list(PIPER_VOICES)
    assert [e.key for e in offered(PINNED, PROFILES["pl"])] == list(PIPER_VOICES)
    assert [e.key for e in offered(catalog, PROFILES["de"])] == ["de_DE-thorsten-high"]
    assert label(english[0]) == "Cori (Piper, local, high quality)"
    assert label(english[1]) == "Alba (Piper, local)"
    jenny = PiperEntry("en_GB-jenny_dioco-medium", "en_GB", "jenny_dioco", "medium", 1, "x", None)
    assert label(jenny) == "Jenny Dioco (Piper, local)"
    assert label(PINNED["pl_PL-mc_speech-medium"]) == "MC Speech (Piper, local)"


def test_refresh_fetches_when_missing_or_stale(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx2.Response(200, text=full_list(*EXTRA))

    folder = tmp_path / "voices"
    assert refresh(folder, http_client=client(handler)) is True
    assert requests == [VOICES_JSON_URL]
    assert "en_GB-alba-medium" in load_catalog(folder)

    age(folder / CATALOG_FILE, 1)
    assert refresh(folder, http_client=client(handler)) is False
    assert len(requests) == 1

    age(folder / CATALOG_FILE, 8)
    assert refresh(folder, http_client=client(handler)) is True
    assert len(requests) == 2


def _status(code):
    return lambda request: httpx2.Response(code)


def _down(request):
    raise httpx2.ConnectError("down", request=request)


def _without_thorsten(request):
    keys = [key for key in PINNED if key != "de_DE-thorsten-high"]
    return httpx2.Response(200, text=voices_json([entry_json(key) for key in keys]))


def _too_big(request):
    return httpx2.Response(200, content=b"{" + b" " * (MAX_CATALOG_BYTES + 1) + b"}")


@pytest.mark.parametrize(
    "handler",
    [_status(500), _down, _without_thorsten, _too_big],
    ids=["500", "network", "no-default", "too-big"],
)
def test_refresh_failures_keep_the_old_file(tmp_path, handler):
    old = full_list(entry_json("en_GB-alba-medium")).encode()
    write_cache(tmp_path, old)
    age(tmp_path / CATALOG_FILE, 30)
    assert refresh(tmp_path, http_client=client(handler)) is False
    assert (tmp_path / CATALOG_FILE).read_bytes() == old
    assert [p.name for p in tmp_path.iterdir()] == [CATALOG_FILE]


def test_urls_from_the_catalog(tmp_path):
    model = f"{REPO_URL}/en/en_GB/cori/high/en_GB-cori-high.onnx"
    assert voice_urls("en_GB-cori-high") == (model, model + ".json")
    write_cache(tmp_path, full_list(entry_json("en_GB-alba-medium")))
    alba = f"{REPO_URL}/en/en_GB/alba/medium/en_GB-alba-medium.onnx"
    assert voice_urls("en_GB-alba-medium", tmp_path) == (alba, alba + ".json")
    with pytest.raises(DownloadError) as info:
        voice_urls("en_GB-alba-medium")
    assert str(info.value) == "Unknown Piper voice: en_GB-alba-medium."


def test_download_a_german_voice(tmp_path, settings, monkeypatch):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if str(request.url).endswith(".onnx.json"):
            return httpx2.Response(200, content=CONFIG)
        return httpx2.Response(200, content=MODEL)

    dest = tmp_path / "voices"
    path = download_voice("de_DE-thorsten-high", dest, http_client=client(handler))
    base = f"{REPO_URL}/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx"
    assert requests == [base, base + ".json"]
    assert path.read_bytes() == MODEL
    assert (dest / "de_DE-thorsten-high.onnx.json").read_bytes() == CONFIG

    gate = threading.Event()
    seen = []

    def stub(voice, dest_dir, **kwargs):
        seen.append((voice, dest_dir))
        gate.wait(5)

    monkeypatch.setattr(download, "download_voice", stub)
    state = downloads.start(settings, "uk_UA-tetiana-high")
    assert state.status == "running"
    gate.set()
    for _ in range(200):
        if downloads.get_state(settings, "uk_UA-tetiana-high").status != "running":
            break
        time.sleep(0.01)
    assert seen == [("uk_UA-tetiana-high", settings.effective_voices_dir)]


def test_loading_makes_no_request(tmp_path, monkeypatch):
    calls = []

    def counting():
        calls.append(1)
        return None

    monkeypatch.setattr(piper_catalog, "http_client_factory", counting)
    write_cache(tmp_path, full_list(*EXTRA))
    for folder in (tmp_path, tmp_path / "empty"):
        offered(load_catalog(folder), PROFILES["en"])
    voice_urls("en_GB-cori-high")
    voice_urls("en_GB-alba-medium", tmp_path)
    assert calls == []
