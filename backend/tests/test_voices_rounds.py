"""Rounds record the subject's language and are read by that language's voice."""

import json

import pytest
from conftest import local_client
from pydantic import SecretStr
from sqlalchemy import text
from test_adaptive_extension import weak_round as extension_round
from test_adaptive_support import build_world
from test_repetition_retry import GOOD, WEAK, start_retry
from test_repetition_retry import weak_round as retry_round
from test_rounds_api import create, start
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.costs import estimates
from app.features.rounds.models import Round
from app.features.rounds.service import NEED_PREMIUM_VOICE
from app.features.voice.per_language import VOICE_WRONG_LANGUAGE
from app.main import create_app

PREMIUM = {"mode": "premium"}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def make_language(db, subject_id: int, code: str) -> None:
    db.execute(text("UPDATE content_language SET enabled = 1 WHERE code = :c"), {"c": code})
    db.execute(text("UPDATE subject SET language = :c WHERE id = :s"), {"c": code, "s": subject_id})
    db.commit()


def set_row(db, code: str, provider: str | None, name: str | None) -> None:
    db.execute(
        text("UPDATE content_language SET voice_provider = :p, voice_name = :n WHERE code = :c"),
        {"p": provider, "n": name, "c": code},
    )
    db.commit()


def install(settings, voice: str) -> None:
    folder = settings.effective_voices_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{voice}.onnx").write_bytes(b"model")
    (folder / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")


def stored_config(db, round_id: int) -> dict:
    db.expire_all()
    return json.loads(db.get(Round, round_id).config_json)


def round_count(db) -> int:
    return db.execute(text("SELECT count(*) FROM round")).scalar()


def use(client, settings, **update):
    client.app.state.settings = settings.model_copy(update=update)


def test_language_recorded(client, world, db):
    for audio in ({"mode": "off"}, {"mode": "browser"}, PREMIUM):
        state = start(client, world, question_count=1, question_audio=audio)
        assert state["config"]["language"] == "pl"
        assert stored_config(db, state["id"])["language"] == "pl"
        assert client.get(f"/api/rounds/{state['id']}").json()["config"]["language"] == "pl"


def test_polish_premium_unchanged(client, world, settings):
    assert (settings.voice_provider, settings.voice_name, settings.voice_rate) == (
        "piper",
        "pl_PL-bass-high",
        1.0,
    )
    state = start(client, world, question_count=1, question_audio=PREMIUM)
    assert state["config"]["question_audio"] == {
        "mode": "premium",
        "provider": "piper",
        "voice": "pl_PL-bass-high",
        "rate": 1.0,
    }


def test_german_premium_uses_the_german_voice(client, world, db, settings):
    make_language(db, world["subject"].id, "de")
    install(settings, "pl_PL-bass-high")
    response = create(client, world, question_count=1, question_audio=PREMIUM)
    assert response.status_code == 422
    assert response.json()["detail"] == NEED_PREMIUM_VOICE

    install(settings, "de_DE-thorsten-high")
    use(client, settings, voice_rate=1.2)
    state = start(client, world, question_count=1, question_audio=PREMIUM)
    assert state["config"]["language"] == "de"
    assert state["config"]["question_audio"] == {
        "mode": "premium",
        "provider": "piper",
        "voice": "de_DE-thorsten-high",
        "rate": 1.2,
    }

    set_row(db, "de", "azure_speech", "de-DE-ConradNeural")
    state = start(client, world, question_count=1, question_audio=PREMIUM)
    audio = state["config"]["question_audio"]
    assert (audio["provider"], audio["voice"]) == ("azure_speech", "de-DE-ConradNeural")


def test_wrong_language_override_refused(client, world, db):
    make_language(db, world["subject"].id, "de")
    before = round_count(db)
    audio = {"mode": "premium", "provider": "piper", "voice": "pl_PL-bass-high"}
    response = create(client, world, question_count=1, question_audio=audio)
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_WRONG_LANGUAGE
    assert round_count(db) == before


def test_override_provider_gets_the_language_default(client, world, db, settings):
    make_language(db, world["subject"].id, "de")
    for provider, voice in (("openai", "marin"), ("azure_speech", "de-DE-KatjaNeural")):
        audio = {"mode": "premium", "provider": provider}
        state = start(client, world, question_count=1, question_audio=audio)
        assert state["config"]["question_audio"] == {
            "mode": "premium",
            "provider": provider,
            "voice": voice,
            "rate": settings.voice_rate,
        }


def test_openai_global_with_piper_name(client, world, settings):
    use(client, settings, voice_provider="openai", voice_name="pl_PL-bass-high")
    state = start(client, world, question_count=1, question_audio=PREMIUM)
    audio = state["config"]["question_audio"]
    assert (audio["provider"], audio["voice"]) == ("openai", "marin")


def test_old_round_reads_without_language(client, world, db):
    state = start(client, world, question_count=1)
    config = stored_config(db, state["id"])
    del config["language"]
    db.execute(
        text("UPDATE round SET config_json = :c WHERE id = :i"),
        {"c": json.dumps(config), "i": state["id"]},
    )
    db.commit()
    response = client.get(f"/api/rounds/{state['id']}")
    assert response.status_code == 200
    assert response.json()["config"]["language"] is None


def test_client_language_is_overwritten(client, world, db, settings):
    install(settings, "de_DE-thorsten-high")
    state = start(client, world, question_count=1, question_audio=PREMIUM, language="de")
    assert state["config"]["language"] == "pl"
    assert stored_config(db, state["id"])["language"] == "pl"
    assert state["config"]["question_audio"]["voice"] == "pl_PL-bass-high"


@pytest.mark.parametrize(
    ("provider", "voice"),
    [("piper", "pl_PL-bass-high"), ("azure_speech", "pl-PL-MarekNeural"), ("openai", "cedar")],
)
def test_quick_style_premium_equals_the_old_override(world, settings, provider, voice):
    configured = settings.model_copy(
        update={
            "voice_provider": provider,
            "voice_name": voice,
            "voice_rate": 1.2,
            "azure_speech_key": SecretStr("az-test-0123456789"),
            "azure_speech_region": "westeurope",
            "openai_api_key": SecretStr("sk-test-0123456789"),
        }
    )
    client = local_client(create_app(configured))
    old = {"mode": "premium", "provider": provider, "voice": voice, "rate": 1.2}
    now = start(client, world, question_count=1, question_audio=PREMIUM)["config"]
    before = start(client, world, question_count=1, question_audio=old)["config"]
    assert now["question_audio"] == before["question_audio"] == old
    assert now == before


def test_children_keep_the_language(client, db, settings):
    world = build_world(db)
    make_language(db, world.subject.id, "de")
    install(settings, "de_DE-thorsten-high")
    parent = extension_round(client, db, world, question_audio=PREMIUM)
    child = client.post(f"/api/rounds/{parent}/extension")
    assert child.status_code == 201, child.text
    assert child.json()["config"]["language"] == "de"
    assert stored_config(db, child.json()["id"])["language"] == "de"
    assert child.json()["config"]["question_audio"]["voice"] == "de_DE-thorsten-high"

    other = build_world(db, name="Geschichte", per_objective=(3, 3, 3))
    make_language(db, other.subject.id, "de")
    parent = retry_round(client, db, other, [WEAK, GOOD, WEAK])
    child = start_retry(client, parent)
    assert child["config"]["language"] == "de"


def test_budget_uses_the_snapshot_provider(client, world, db, settings, monkeypatch):
    seen = []
    real = estimates.round_estimate

    def recording(*args, **kwargs):
        seen.append(kwargs["voice_provider"])
        return real(*args, **kwargs)

    monkeypatch.setattr(estimates, "round_estimate", recording)
    make_language(db, world["subject"].id, "de")
    install(settings, "de_DE-thorsten-high")
    # The global provider is Azure: a German round must not be priced by it.
    use(client, settings, spending_ceiling_usd=100.0, voice_provider="azure_speech")
    start(client, world, question_count=1, question_audio=PREMIUM)
    start(client, world, question_count=1, question_audio={"mode": "browser"})
    set_row(db, "de", "openai", None)
    start(client, world, question_count=1, question_audio=PREMIUM)
    assert seen == ["piper", "browser", "openai"]
