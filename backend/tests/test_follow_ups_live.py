"""One real follow-up generation. Run by hand: pytest -m live -k follow_ups -q (at most 2 calls)."""

import os
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.features.follow_ups import prompts
from app.features.follow_ups import service as follow_ups
from app.features.follow_ups.prompts import ANCHORED_TARGETS
from app.features.languages.profiles import PROFILES
from app.features.providers import registry
from app.features.providers.types import Message
from app.features.providers.usage import InMemoryUsageRecorder
from app.features.questions.schemas import EssentialPoint, QuestionWithRubric, Rubric
from app.features.rounds.schemas import RoundConfig, Scope

MAX_CALLS = 2

_settings = Settings()

# conftest.py un-skips live tests only when a AOC_*_API_KEY environment variable exists;
# keys kept in backend/.env are exported here so that file alone is enough.
for _name, _secret in (
    ("AOC_OPENAI_API_KEY", _settings.openai_api_key),
    ("AOC_ANTHROPIC_API_KEY", _settings.anthropic_api_key),
):
    if _secret is not None and _secret.get_secret_value() and not os.environ.get(_name):
        os.environ[_name] = _secret.get_secret_value()

PASSAGES = [
    (
        11,
        "Ostracyzm był procedurą wprowadzoną w Atenach przez Klejstenesa. Raz w roku zgromadzenie "
        "ludowe decydowało, czy go przeprowadzić; obywatele pisali imię polityka na skorupach "
        "(ostrakonach), a wskazany przez co najmniej 6000 głosów musiał opuścić miasto na "
        "dziesięć lat, zachowując majątek.",
    ),
    (
        12,
        "Celem ostracyzmu była ochrona demokracji przed powrotem tyranii: pozwalał usunąć "
        "polityka zbyt wpływowego, zanim zdobędzie władzę jedynowładczą. Wygnanie nie było karą "
        "za przestępstwo i nie odbierało praw obywatelskich.",
    ),
]
TRANSCRIPT = (
    "Ostracyzm to było głosowanie na skorupach, gdzie Ateńczycy wypędzali polityków, którzy "
    "popełnili przestępstwo, i chyba tracili oni wtedy cały majątek."
)
QUESTION = QuestionWithRubric(
    version_id=1,
    family_id=1,
    version=1,
    text="Czym był ostracyzm w starożytnych Atenach?",
    style="short",
    difficulty="recall",
    primary_topic_id=None,
    objective_id=None,
    status="active",
    suggested_prep_s=15,
    suggested_answer_s=60,
    source_policy="materials",
    rubric=Rubric(
        essential_points=[
            EssentialPoint(id="p1", text="Głosowanie na skorupach", weight=0.4, passage_ids=[11]),
            EssentialPoint(id="p2", text="Wygnanie na dziesięć lat", weight=0.3, passage_ids=[11]),
            EssentialPoint(id="p3", text="Ochrona przed tyranią", weight=0.3, passage_ids=[12]),
        ],
        model_answer="Ostracyzm był głosowaniem na skorupach, które wygnało polityka na 10 lat.",
    ),
    passages=[],
)


@pytest.mark.live
def test_live_follow_up_generation():
    calls = []
    real = registry.get_text_generator(_settings, "generation", InMemoryUsageRecorder())

    class Capped:
        def generate_structured(self, *args, **kwargs):
            calls.append(kwargs["operation_id"])
            if len(calls) > MAX_CALLS:
                pytest.fail(f"the live test would make more than {MAX_CALLS} calls")
            return real.generate_structured(*args, **kwargs)

    message = prompts.user_message(
        QUESTION.text,
        QUESTION.rubric,
        PASSAGES,
        TRANSCRIPT,
        [
            ("Głosowanie na skorupach", "covered", "Uczeń wspomniał o skorupach."),
            ("Wygnanie na dziesięć lat", "missing", "Brak informacji o czasie wygnania."),
            ("Ochrona przed tyranią", "wrong", "Uczeń uznał ostracyzm za karę za przestępstwo."),
        ],
        [("tracili oni wtedy cały majątek", "Wygnany zachowywał majątek.")],
        profile=PROFILES["pl"],
    )
    config = RoundConfig(scope=Scope(kind="subject"))
    allowed = {pid for pid, _ in PASSAGES}

    def check(generated):
        return follow_ups._check(
            generated, main=QUESTION, transcript=TRANSCRIPT, allowed=allowed, config=config
        )

    generated, _, used = follow_ups._ask_model(
        Capped(),
        SimpleNamespace(id=0, tries=1),
        [Message(role="user", content=message)],
        check,
        system=prompts.follow_up_system("materials", profile=PROFILES["pl"]),
    )
    assert used <= MAX_CALLS
    assert any(c in generated.text.lower() for c in "ąćęłńóśźż") or " " in generated.text
    if generated.target in ANCHORED_TARGETS:
        assert generated.anchor_quote and generated.anchor_quote.lower() in TRANSCRIPT.lower()
    cited = {i for p in generated.rubric.essential_points for i in p.passage_ids}
    assert cited and cited <= allowed
