import pytest
from pydantic import ValidationError

from app.features.rounds.schemas import ActionIn, QuestionAudio, RoundConfig, Scope

SUBJECT = {"kind": "subject"}


def test_defaults():
    config = RoundConfig(scope=Scope(kind="subject"))
    assert config.round_type == "standard"
    assert config.source_policy == "materials"
    assert config.feedback_timing == "practice"
    assert config.question_count == 10
    assert (config.prep_seconds, config.answer_seconds) == (30, 120)
    assert (config.style, config.difficulty) == ("mixed", "mixed")
    assert config.question_audio == QuestionAudio(mode="off")
    assert config.band_bounds is None


def test_zero_preparation_allowed_zero_answer_rejected():
    assert RoundConfig(scope=SUBJECT, prep_seconds=0).prep_seconds == 0
    with pytest.raises(ValidationError):
        RoundConfig(scope=SUBJECT, answer_seconds=0)


@pytest.mark.parametrize(
    "field,value",
    [("question_count", 0), ("question_count", 31), ("prep_seconds", 601), ("answer_seconds", 4)],
)
def test_ranges(field, value):
    with pytest.raises(ValidationError):
        RoundConfig(scope=SUBJECT, **{field: value})


def test_unknown_fields_and_round_types_rejected():
    with pytest.raises(ValidationError):
        RoundConfig(scope=SUBJECT, follow_ups=True)
    with pytest.raises(ValidationError):
        RoundConfig(scope=SUBJECT, round_type="benchmark")
    with pytest.raises(ValidationError):
        RoundConfig(scope=SUBJECT, source_policy="web")


def test_scope_kinds_need_their_ids():
    with pytest.raises(ValidationError, match="topic"):
        Scope(kind="topics")
    with pytest.raises(ValidationError, match="document"):
        Scope(kind="documents", topic_ids=[1])
    assert Scope(kind="topics", topic_ids=[3]).topic_ids == [3]
    assert Scope(kind="documents", document_ids=[4]).document_ids == [4]


def test_json_round_trip():
    config = RoundConfig(
        scope=Scope(kind="topics", topic_ids=[1, 2]),
        feedback_timing="exam",
        question_count=5,
        style="open",
        difficulty="apply",
        prep_seconds=0,
        question_audio=QuestionAudio(mode="premium", provider="piper", voice="v", rate=1.1),
        band_bounds=(30, 60, 80),
    )
    assert RoundConfig.model_validate_json(config.model_dump_json()) == config


def test_start_answer_needs_reason():
    with pytest.raises(ValidationError):
        ActionIn(action="start_answer", from_phase="preparation")
    assert ActionIn(action="start_answer", from_phase="preparation", start_reason="timer")
