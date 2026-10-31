import pytest
from pydantic import ValidationError
from test_assessment_support import ai, point, rubric

from app.features.assessment.schemas import (
    BAND_LABELS,
    DEFAULT_BAND_BOUNDS,
    AssessmentResult,
    Scores,
)
from app.features.assessment.scoring import (
    classify_band,
    completeness_from_points,
    compute_scores,
    display_value,
    model_answer_display,
    validate_bounds,
)
from app.features.providers.types import Provenance


def test_completeness_weights():
    # 100 × (0.5 × 1.0 + 0.3 × 0.5 + 0.2 × 0.0) = 100 × 0.65 = 65.0
    points = [point("p1", "covered"), point("p2", "partial"), point("p3", "missing")]
    assert completeness_from_points(points, rubric()) == pytest.approx(65.0)


def test_weights_normalized_defensively():
    # weights 5/3/2 normalize to 0.5/0.3/0.2: same 65.0 as above
    points = [point("p1", "covered"), point("p2", "partial"), point("p3", "missing")]
    assert completeness_from_points(points, rubric((5, 3, 2))) == pytest.approx(65.0)


def test_wrong_point_gets_no_credit():
    # 100 × (0.5 × 0.0 + 0.3 × 1.0 + 0.2 × 1.0) = 50.0
    points = [point("p1", "wrong"), point("p2", "covered"), point("p3", "covered")]
    assert completeness_from_points(points, rubric()) == pytest.approx(50.0)


def test_factual_and_total_formulas():
    # completeness 65 (as above)
    # factual = (0.5 × 70 + 0.3 × 65) / 0.8 = (35 + 19.5) / 0.8 = 68.125
    # total = 0.5 × 70 + 0.3 × 65 + 0.2 × 90 = 35 + 19.5 + 18 = 72.5
    scores = compute_scores(ai(correctness=70, language=90), rubric(), weights=(50, 30))
    assert scores.correctness == 70
    assert scores.completeness == pytest.approx(65.0)
    assert scores.factual == pytest.approx(68.125)
    assert scores.language == 90
    assert scores.total == pytest.approx(72.5)


def test_display_value_rounds_half_up():
    assert display_value(68.125) == 68
    assert display_value(29.5) == 30
    assert display_value(29.49) == 29
    assert display_value(72.5) == 73


def test_band_boundaries_on_display_value():
    assert classify_band(29.49) == "weak"  # → 29
    assert classify_band(29.5) == "needs_improvement"  # → 30
    assert classify_band(59.5) == "good"  # → 60
    assert classify_band(79.49) == "good"  # → 79
    assert classify_band(80) == "very_good"
    assert classify_band(0) == "weak"
    assert classify_band(100) == "very_good"
    assert classify_band(None) is None


def test_custom_bounds():
    bounds = (40, 70, 90)
    assert classify_band(39.4, bounds) == "weak"
    assert classify_band(40, bounds) == "needs_improvement"
    assert classify_band(69.6, bounds) == "good"
    assert classify_band(89, bounds) == "good"
    assert classify_band(90, bounds) == "very_good"
    assert validate_bounds(bounds) == bounds
    assert validate_bounds(DEFAULT_BAND_BOUNDS) == (30, 60, 80)


@pytest.mark.parametrize("bounds", [(60, 30, 80), (0, 60, 80), (30, 60, 100), (30, 30, 80)])
def test_invalid_bounds_rejected(bounds):
    with pytest.raises(ValueError):
        validate_bounds(bounds)
    with pytest.raises(ValueError):
        classify_band(50, bounds)


def test_model_answer_display_rule():
    assert model_answer_display(None, "no_answer") == "auto"
    assert model_answer_display("weak", "no_answer") == "auto"
    assert model_answer_display("weak", "assessed") == "auto"
    assert model_answer_display("needs_improvement", "assessed") == "one_click"
    assert model_answer_display("good", "assessed") == "on_request"
    assert model_answer_display("very_good", "assessed") == "on_request"
    assert model_answer_display(None, "no_speech") is None


@pytest.mark.parametrize(
    "field,value", [("correctness", 101), ("correctness", -1), ("language", 120), ("fluency", -5)]
)
def test_ai_assessment_rejects_out_of_range_scores(field, value):
    with pytest.raises(ValidationError):
        ai(**{field: value})


def test_ai_assessment_has_no_completeness_field():
    assert "completeness" not in ai().model_dump()


def test_result_round_trips_json():
    result = AssessmentResult(
        status="assessed",
        scores=compute_scores(ai(), rubric()),
        band="good",
        model_answer_display="on_request",
        ai=ai(),
        band_bounds=DEFAULT_BAND_BOUNDS,
        factual_weights=(30, 50),
        prompt_version="assessment-v1",
        question_version_id=11,
        provenance=Provenance(provider="openai", model="fake", operation_id="op-1"),
        attempts_used=1,
    )
    again = AssessmentResult.model_validate_json(result.model_dump_json())
    assert again == result
    assert isinstance(again.scores, Scores)
    assert again.band_bounds == (30, 60, 80)


def test_display_value_absorbs_float_noise():
    assert display_value(29.499999999999996) == 30
    assert classify_band(29.499999999999996) == "needs_improvement"


def test_default_weights_30_50():
    # factual = (0.3 × 70 + 0.5 × 65) / 0.8 = (21 + 32.5) / 0.8 = 66.875
    # total = 21 + 32.5 + 0.2 × 90 = 71.5
    scores = compute_scores(ai(correctness=70, language=90), rubric())
    assert scores.factual == pytest.approx(66.875)
    assert scores.total == pytest.approx(71.5)


def test_correct_but_half_complete_is_not_top_band():
    # the bio-partial golden case: correctness 100, completeness 50 → 68.75, Good
    points = [point("p1"), point("p2", "missing"), point("p3", "missing"), point("p4")]
    scores = compute_scores(
        ai(correctness=100, points=points), rubric(weights=(0.25, 0.25, 0.25, 0.25))
    )
    assert scores.completeness == pytest.approx(50.0)
    assert scores.factual == pytest.approx(68.75)
    assert classify_band(scores.factual) == "good"


@pytest.mark.parametrize("weights", [(40, 30), (5, 75), (75, 5), (0, 80)])
def test_invalid_weights_rejected(weights):
    with pytest.raises(ValueError):
        compute_scores(ai(), rubric(), weights=weights)


def test_band_labels_are_english():
    assert BAND_LABELS == {
        "weak": "Weak",
        "needs_improvement": "Needs improvement",
        "good": "Good",
        "very_good": "Very good",
    }
