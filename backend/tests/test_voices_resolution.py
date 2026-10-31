import itertools

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.languages.profiles import PROFILES, SUPPORTED
from app.features.voice import azure_voices, piper_catalog
from app.features.voice.azure_voices import PINNED as AZURE_PINNED
from app.features.voice.per_language import (
    default_voice,
    language_of_voice,
    voice_for_language,
    voice_matches_language,
)

de, en, fr, pl, uk = (PROFILES[code] for code in ("de", "en", "fr", "pl", "uk"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every case here must resolve without a request: both factories count their calls."""
    calls = []

    def counting():
        calls.append(1)
        return None

    monkeypatch.setattr(piper_catalog, "http_client_factory", counting)
    monkeypatch.setattr(azure_voices, "http_client_factory", counting)
    yield calls
    assert calls == []


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def install(settings, voice: str) -> None:
    folder = settings.effective_voices_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{voice}.onnx").write_bytes(b"model")
    (folder / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")


def set_row(db, code: str, provider: str | None, name: str | None) -> None:
    db.execute(
        text("UPDATE content_language SET voice_provider = :p, voice_name = :n WHERE code = :c"),
        {"p": provider, "n": name, "c": code},
    )
    db.commit()


def globally(settings, provider: str, name: str = "pl_PL-bass-high", **extra):
    return settings.model_copy(update={"voice_provider": provider, "voice_name": name, **extra})


@pytest.mark.parametrize(
    ("provider", "voice", "profile", "expected"),
    [
        ("piper", "de_DE-thorsten-high", de, True),
        ("piper", "pl_PL-bass-high", de, False),
        ("azure_speech", "de-DE-KatjaNeural", de, True),
        ("azure_speech", "de-AT-IngridNeural", de, False),
        ("openai", "marin", uk, True),
        ("browser", "browser", fr, True),
        ("piper", "en_GB", en, False),
        ("espeak", "x", pl, False),
    ],
)
def test_voice_matches_language(provider, voice, profile, expected):
    assert voice_matches_language(provider, voice, profile) is expected


def test_language_of_voice():
    assert language_of_voice("piper", "it_IT-serena-high") == "it"
    assert language_of_voice("azure_speech", "uk-UA-PolinaNeural") == "uk"
    assert language_of_voice("openai", "marin") is None
    assert language_of_voice("piper", "en_US-amy-medium") is None
    assert language_of_voice("browser", "browser") is None


def test_polish_null_row_is_the_global_voice(db, settings):
    cases = [
        (globally(settings, "piper"), ("piper", "pl_PL-bass-high")),
        (
            globally(settings, "azure_speech", "pl-PL-MarekNeural"),
            ("azure_speech", "pl-PL-MarekNeural"),
        ),
        (globally(settings, "openai", "cedar"), ("openai", "cedar")),
        (globally(settings, "browser"), ("browser", "browser")),
    ]
    for current, expected in cases:
        assert voice_for_language(db, current, "pl") == expected


def test_openai_with_a_piper_name(db, settings):
    assert voice_for_language(db, globally(settings, "openai"), "pl") == ("openai", "marin")


def test_polish_global_voice_of_another_language(db, settings):
    current = globally(settings, "azure_speech", "de-DE-KatjaNeural")
    assert voice_for_language(db, current, "pl") == ("browser", "browser")
    install(settings, "pl_PL-bass-high")
    assert voice_for_language(db, current, "pl") == ("piper", "pl_PL-bass-high")


def test_other_language_null_row(db, settings):
    install(settings, "pl_PL-bass-high")
    assert voice_for_language(db, settings, "de") == ("browser", "browser")
    install(settings, "de_DE-thorsten-high")
    assert voice_for_language(db, settings, "de") == ("piper", "de_DE-thorsten-high")


def test_explicit_rows(db, settings):
    set_row(db, "de", "azure_speech", None)
    assert voice_for_language(db, settings, "de") == ("azure_speech", "de-DE-KatjaNeural")
    set_row(db, "de", "openai", None)
    assert voice_for_language(db, settings, "de") == ("openai", settings.openai_voice_default)
    set_row(db, "de", "piper", "de_DE-thorsten-medium")
    assert voice_for_language(db, settings, "de") == ("piper", "de_DE-thorsten-medium")
    set_row(db, "de", "piper", "pl_PL-gosia-medium")
    assert voice_for_language(db, settings, "de") == ("browser", "browser")
    install(settings, "de_DE-thorsten-high")
    assert voice_for_language(db, settings, "de") == ("piper", "de_DE-thorsten-high")
    set_row(db, "de", "espeak", "x")
    assert voice_for_language(db, settings, "de") == ("piper", "de_DE-thorsten-high")


def test_unknown_language(db, settings):
    with pytest.raises(ValueError):
        voice_for_language(db, settings, "xx")


ROWS = (
    [(None, None), ("openai", "marin"), ("browser", "browser")]
    + [("piper", PROFILES[code].default_piper_voice) for code in SUPPORTED]
    + [("azure_speech", voices[0].short_name) for voices in AZURE_PINNED.values()]
)
GLOBALS = [
    ("piper", "pl_PL-bass-high"),
    ("azure_speech", "de-DE-KatjaNeural"),
    ("openai", "marin"),
]


@pytest.mark.parametrize("installed", [False, True], ids=["nothing-installed", "all-installed"])
def test_never_another_language(db, settings, installed):
    if installed:
        for code in SUPPORTED:
            install(settings, PROFILES[code].default_piper_voice)
    for code, (provider, name), (global_provider, global_name) in itertools.product(
        SUPPORTED, ROWS, GLOBALS
    ):
        set_row(db, code, provider, name)
        current = globally(settings, global_provider, global_name)
        chosen, voice = voice_for_language(db, current, code)
        assert voice_matches_language(chosen, voice, PROFILES[code]), (code, provider, name)


def test_default_voice(settings):
    assert default_voice(settings, "piper", pl) == "pl_PL-bass-high"
    gosia = globally(settings, "piper", "pl_PL-gosia-medium")
    assert default_voice(gosia, "piper", pl) == "pl_PL-gosia-medium"
    assert default_voice(gosia, "piper", de) == "de_DE-thorsten-high"
    german_name = globally(settings, "piper", "de_DE-thorsten-high")
    assert default_voice(german_name, "piper", pl) == "pl_PL-bass-high"

    zofia = settings.model_copy(update={"azure_voice_default": "pl-PL-ZofiaNeural"})
    assert default_voice(zofia, "azure_speech", pl) == "pl-PL-ZofiaNeural"
    assert default_voice(zofia, "azure_speech", de) == "de-DE-KatjaNeural"
    marek = globally(settings, "azure_speech", "pl-PL-MarekNeural")
    assert default_voice(marek, "azure_speech", pl) == "pl-PL-MarekNeural"
    assert default_voice(marek, "azure_speech", uk) == "uk-UA-PolinaNeural"
    conrad = settings.model_copy(update={"azure_voice_default": "de-DE-ConradNeural"})
    assert default_voice(conrad, "azure_speech", de) == "de-DE-ConradNeural"
    # A Polish azure_voice_default of another language falls to the locale's first voice.
    assert default_voice(conrad, "azure_speech", pl) == "pl-PL-ZofiaNeural"

    assert default_voice(settings, "openai", de) == settings.openai_voice_default
    cedar = globally(settings, "openai", "cedar")
    assert default_voice(cedar, "openai", de) == "cedar"
    assert default_voice(cedar, "openai", pl) == "cedar"

    assert default_voice(settings, "browser", fr) == "browser"
    with pytest.raises(ValueError):
        default_voice(settings, "espeak", fr)


def test_default_voice_reads_the_azure_cache(settings):
    keyed = settings.model_copy(
        update={
            "azure_speech_key": SecretStr("az-test-0123456789"),
            "azure_speech_region": "westeurope",
        }
    )
    path = azure_voices.cache_path(keyed)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"voices": [{"short_name": "de-DE-SeraphinaMultilingualNeural", "locale": "de-DE",'
        ' "label": "Seraphina (Azure, neural)", "gender": "female"}]}',
        encoding="utf-8",
    )
    assert default_voice(keyed, "azure_speech", de) == "de-DE-SeraphinaMultilingualNeural"


def test_resolution_makes_no_request(db, settings, no_network):
    # Every test here runs under no_network; this one names the rule on its own.
    set_row(db, "de", "azure_speech", None)
    voice_for_language(db, settings, "de")
    voice_for_language(db, settings, "pl")
    for code in SUPPORTED:
        for provider in ("piper", "azure_speech", "openai", "browser"):
            default_voice(settings, provider, PROFILES[code])
    assert no_network == []
