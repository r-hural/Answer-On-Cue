import pytest
from test_assessment_support import ai, metrics, point, question, sequence

from app.features.assessment.schemas import AssessmentError, AssessmentInput, FactualMistake
from app.features.assessment.service import ASSESS_TASK, assess
from app.features.languages.profiles import PROFILES
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.types import ProviderError


def _inp(**over) -> AssessmentInput:
    base = dict(question=question(), transcript="Fotosynteza to proces…", outcome="answered")
    base.update(over)
    return AssessmentInput(**base)


def _gen(*values) -> FakeTextGenerator:
    return FakeTextGenerator({ASSESS_TASK: sequence(*values)})


@pytest.mark.parametrize("outcome", ["dont_know", "skipped"])
def test_dont_know_and_skip_no_ai_call(outcome):
    generator = _gen()
    result = assess(
        _inp(outcome=outcome, transcript="Nie wiem."),
        generator,
        operation_id="op",
        profile=PROFILES["pl"],
    )
    assert result.status == "no_answer"
    s = result.scores
    assert (s.correctness, s.completeness, s.factual, s.total) == (0, 0, 0, 0)
    assert s.language is None
    assert result.band == "weak"
    assert result.model_answer_display == "auto"
    assert result.ai is None and result.prompt_version is None
    assert result.attempts_used == 0
    assert generator.calls == []


@pytest.mark.parametrize("transcript", [None, "", "   ", " ... ?! "])
def test_empty_transcript_is_no_speech(transcript):
    generator = _gen()
    result = assess(
        _inp(transcript=transcript), generator, operation_id="op", profile=PROFILES["pl"]
    )
    assert result.status == "no_speech"
    assert result.scores is None and result.band is None
    assert result.model_answer_display is None
    assert result.attempts_used == 0
    assert generator.calls == []


def test_zero_speech_ms_is_no_speech():
    generator = _gen()
    inp = _inp(transcript="Fotosynteza.", delivery=metrics(speech_ms=0))
    result = assess(inp, generator, operation_id="op", profile=PROFILES["pl"])
    assert result.status == "no_speech"
    assert result.scores is None and result.band is None
    assert generator.calls == []


def test_valid_output_scored_in_code():
    # completeness = 100 × (0.5 + 0.3 × 0.5) = 65; factual = (35 + 19.5) / 0.8 = 68.125 → Good
    # total = 35 + 19.5 + 0.2 × 90 = 72.5
    generator = _gen(ai(correctness=70, language=90))
    result = assess(_inp(), generator, operation_id="op", weights=(50, 30), profile=PROFILES["pl"])
    assert result.factual_weights == (50, 30)
    assert result.status == "assessed"
    assert result.scores.completeness == pytest.approx(65.0)
    assert result.scores.factual == pytest.approx(68.125)
    assert result.scores.total == pytest.approx(72.5)
    assert result.band == "good"
    assert result.model_answer_display == "on_request"
    assert result.attempts_used == 1
    assert result.question_version_id == 11
    assert result.provenance.operation_id == "op"
    assert result.band_bounds == (30, 60, 80)


def test_band_uses_given_bounds():
    generator = _gen(ai(correctness=70, language=90))
    result = assess(
        _inp(), generator, operation_id="op", bounds=(40, 70, 90), profile=PROFILES["pl"]
    )
    assert result.band == "needs_improvement"  # factual 68.125 → 68 < 70
    assert result.model_answer_display == "one_click"
    assert result.band_bounds == (40, 70, 90)


def test_missing_point_invalid_then_retry_ok():
    bad = ai(points=[point("p1"), point("p3", "missing")])
    generator = _gen(bad, ai())
    result = assess(_inp(), generator, operation_id="op-7", profile=PROFILES["pl"])
    assert result.status == "assessed"
    assert result.attempts_used == 2
    assert [c["operation_id"] for c in generator.calls] == ["op-7", "op-7:r1"]
    retry = generator.calls[1]["messages"][-1]
    assert retry.role == "user"
    assert "p2" in retry.content and "missing" in retry.content
    assert result.provenance.operation_id == "op-7:r1"


@pytest.mark.parametrize(
    "points",
    [
        [point("p1"), point("p2"), point("p2"), point("p3")],
        [point("p1"), point("p2"), point("p3"), point("p9")],
    ],
)
def test_duplicate_or_unknown_point_invalid(points):
    generator = _gen(ai(points=points), ai())
    result = assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"])
    assert result.attempts_used == 2


def test_evidence_rules_invalid():
    no_quote = [point("p1", evidence=""), point("p2"), point("p3")]
    generator = _gen(ai(points=no_quote), ai())
    assert assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"]).attempts_used == 2
    quoted = point("p3").model_copy(update={"status": "missing", "evidence": "cytat"})
    generator = _gen(ai(points=[point("p1"), point("p2"), quoted]), ai())
    assert assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"]).attempts_used == 2


def test_empty_evidence_on_missing_point_is_accepted():
    points = [point("p1"), point("p2"), point("p3", "missing", evidence="  ")]
    result = assess(_inp(), _gen(ai(points=points)), operation_id="op", profile=PROFILES["pl"])
    assert result.attempts_used == 1


def test_schema_invalid_output_retried_once():
    error = ProviderError("invalid_output", "openai", "Answer cut off.")
    calls = []

    def respond(messages):
        calls.append(messages)
        if len(calls) == 1:
            raise error
        return ai()

    generator = FakeTextGenerator({ASSESS_TASK: respond})
    result = assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"])
    assert result.status == "assessed" and result.attempts_used == 2
    assert [c["operation_id"] for c in generator.calls] == ["op", "op:r1"]
    assert calls[0] == calls[1]  # the same request again: there is no output to correct


def test_schema_invalid_output_twice_raises_assessment_error():
    error = ProviderError("invalid_output", "openai", "Answer cut off.")
    generator = FakeTextGenerator({}, error=error)
    with pytest.raises(AssessmentError) as raised:
        assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"])
    assert "schema" in raised.value.errors[0]
    assert len(generator.calls) == 2


def test_invalid_twice_raises_assessment_error():
    bad = ai(points=[point("p1")])
    generator = _gen(bad, bad)
    with pytest.raises(AssessmentError) as raised:
        assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"])
    assert str(raised.value) == "The assessment failed: the model returned an invalid result."
    assert raised.value.errors
    assert len(generator.calls) == 2


def test_provider_error_propagates_without_retry():
    error = ProviderError("timeout", "openai", "Timed out.", retryable=True)
    generator = FakeTextGenerator({}, error=error)
    with pytest.raises(ProviderError) as raised:
        assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"])
    assert raised.value is error
    assert len(generator.calls) == 1


def test_provider_error_on_retry_propagates():
    error = ProviderError("server", "openai", "Server error.")
    calls = []

    def respond(messages):
        calls.append(1)
        if len(calls) == 2:
            raise error
        return ai(points=[point("p1")])

    with pytest.raises(ProviderError):
        assess(
            _inp(),
            FakeTextGenerator({ASSESS_TASK: respond}),
            operation_id="op",
            profile=PROFILES["pl"],
        )


def test_language_required_for_answered():
    generator = _gen(ai(language=None), ai())
    assert assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"]).attempts_used == 2


def test_fluency_without_delivery_invalid():
    bad = ai(fluency=70, fluency_coverage="full")
    generator = _gen(bad, ai())
    assert assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"]).attempts_used == 2


def test_fluency_with_delivery_accepted():
    good = ai(fluency=70, fluency_coverage="partial", fluency_confidence="medium")
    result = assess(_inp(delivery=metrics()), _gen(good), operation_id="op", profile=PROFILES["pl"])
    assert result.attempts_used == 1
    assert result.ai.fluency == 70


@pytest.mark.parametrize("fluency,coverage", [(None, "full"), (None, "partial"), (60, "none")])
def test_coverage_none_requires_null_fluency(fluency, coverage):
    bad = ai(fluency=fluency, fluency_coverage=coverage)
    generator = _gen(bad, ai())
    result = assess(_inp(delivery=metrics()), generator, operation_id="op", profile=PROFILES["pl"])
    assert result.attempts_used == 2


def _mistake(index: int | None) -> FactualMistake:
    return FactualMistake(quote="tlen", correction="Dwutlenek węgla.", major=True, rule_index=index)


@pytest.mark.parametrize("index", [1, 5, -1])
def test_bad_rule_index_invalid(index):
    generator = _gen(ai(factual_mistakes=[_mistake(index)]), ai())
    assert assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"]).attempts_used == 2


def test_valid_rule_index_accepted():
    generator = _gen(ai(factual_mistakes=[_mistake(0)]))
    assert assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"]).attempts_used == 1


@pytest.mark.parametrize("improvements", [[], ["a", "b", "c"]])
def test_improvements_count_invalid(improvements):
    generator = _gen(ai(improvements=improvements), ai())
    assert assess(_inp(), generator, operation_id="op", profile=PROFILES["pl"]).attempts_used == 2


def test_operation_id_passed_through():
    generator = _gen(ai())
    assess(_inp(), generator, operation_id="attempt-42:assess", profile=PROFILES["pl"])
    (call,) = generator.calls
    assert call["operation_id"] == "attempt-42:assess"
    assert call["task"] == "assessment.assess"


def test_interrupted_and_kind_do_not_change_scores():
    plain = assess(_inp(), _gen(ai()), operation_id="op", profile=PROFILES["pl"])
    other = assess(
        _inp(interrupted=True, attempt_kind="retry"),
        _gen(ai()),
        operation_id="op",
        profile=PROFILES["pl"],
    )
    assert plain.scores == other.scores and plain.band == other.band
