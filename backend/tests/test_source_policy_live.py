"""Real calls under both source policies. Run by hand: pytest -m live -k source_policy -q
(at most 4 calls: expanded generation + verification, then two golden assessments)."""

import os
from pathlib import Path

import pytest

from app.config import Settings
from app.features.assessment import golden
from app.features.assessment.service import assess
from app.features.languages.profiles import PROFILES
from app.features.providers import registry
from app.features.providers.types import Message
from app.features.providers.usage import InMemoryUsageRecorder
from app.features.questions import prompts, validation

MAX_CALLS = 4
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "assessment" / "golden_source_policy"

_settings = Settings()

# conftest.py un-skips live tests only when a AOC_*_API_KEY environment variable exists;
# keys kept in backend/.env are exported here so that file alone is enough.
for _name, _secret in (
    ("AOC_OPENAI_API_KEY", _settings.openai_api_key),
    ("AOC_ANTHROPIC_API_KEY", _settings.anthropic_api_key),
):
    if _secret is not None and _secret.get_secret_value() and not os.environ.get(_name):
        os.environ[_name] = _secret.get_secret_value()

calls: list[str] = []


class Capped:
    def __init__(self, real) -> None:
        self.real = real

    def generate_structured(self, *args, **kwargs):
        calls.append(kwargs["operation_id"])
        if len(calls) > MAX_CALLS:
            pytest.fail(f"the live tests would make more than {MAX_CALLS} calls")
        return self.real.generate_structured(*args, **kwargs)


@pytest.mark.live
def test_live_expanded_generation():
    subject = golden.load_subjects(GOLDEN_DIR)[0]
    passages = [(p.id, p.text) for p in subject.passages[:1]]
    generator = Capped(
        registry.get_text_generator(_settings, "generation", InMemoryUsageRecorder())
    )
    message = prompts.generate_message(
        "Ostracyzm w Atenach",
        None,
        ["Wyjaśnić cel ostracyzmu"],
        passages,
        [],
        [("open", "analysis")],
        "expanded",
        profile=PROFILES["pl"],
    )
    generated = generator.generate_structured(
        "questions.generate",
        prompts.generate_system("expanded", profile=PROFILES["pl"]),
        [Message(role="user", content=message)],
        prompts.GeneratedBatch,
        operation_id="live:source-policy:generate",
        max_output_tokens=8000,
    )
    (question,) = generated.value.questions[:1]
    result = validation.validate_question(question, {p for p, _ in passages}, [], policy="expanded")
    assert result.question is not None, result.reason
    points = result.question.rubric.essential_points
    assert all(p.basis in ("source", "expanded", "mixed") for p in points)
    assert all(p.passage_ids == [] for p in points if p.basis == "expanded")
    verification = generator.generate_structured(
        "questions.verify",
        prompts.verify_system("expanded", profile=PROFILES["pl"]),
        [
            Message(
                role="user",
                content=prompts.verify_message(
                    passages,
                    [(result.question.text, result.question.rubric)],
                    "expanded",
                    profile=PROFILES["pl"],
                ),
            )
        ],
        prompts.Verification,
        operation_id="live:source-policy:verify",
        max_output_tokens=8000,
    )
    assert [item.index for item in verification.value.items] == [0]


@pytest.mark.live
@pytest.mark.parametrize("case_id", ["hist-expanded", "hist-conflict"])
def test_live_golden_source_policy(case_id):
    questions = golden.load_questions(GOLDEN_DIR)
    case = next(c for c in golden.load_cases(GOLDEN_DIR) if c.id == case_id)
    generator = Capped(
        registry.get_text_generator(_settings, "assessment", InMemoryUsageRecorder())
    )
    result = assess(
        golden.case_input(case, questions),
        generator,
        operation_id=f"live:source-policy:{case_id}",
        profile=PROFILES["pl"],
    )
    assert result.attempts_used <= 2
    assert golden.check_case(case, result) == []
