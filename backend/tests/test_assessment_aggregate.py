import pytest
from test_assessment_support import ai

from app.features.assessment.aggregate import ItemForSummary, round_knowledge_summary
from app.features.assessment.schemas import DEFAULT_BAND_BOUNDS, AssessmentResult, Scores


def _assessed(total: float, band="good", language: float | None = 80, fluency=None, **s):
    scores = dict(correctness=total, completeness=total, factual=total, language=language)
    scores.update(s)
    coverage = "none" if fluency is None else "partial"
    return AssessmentResult(
        status="assessed",
        scores=Scores(total=total, **scores),
        band=band,
        model_answer_display="on_request",
        ai=ai(fluency=fluency, fluency_coverage=coverage),
        band_bounds=DEFAULT_BAND_BOUNDS,
        factual_weights=(30, 50),
        prompt_version="assessment-v1",
        question_version_id=1,
        provenance=None,
        attempts_used=1,
    )


def _no_answer():
    return AssessmentResult(
        status="no_answer",
        scores=Scores(correctness=0, completeness=0, factual=0, language=None, total=0),
        band="weak",
        model_answer_display="auto",
        ai=None,
        band_bounds=DEFAULT_BAND_BOUNDS,
        factual_weights=(30, 50),
        prompt_version=None,
        question_version_id=1,
        provenance=None,
        attempts_used=0,
    )


def _no_speech():
    return AssessmentResult(
        status="no_speech",
        scores=None,
        band=None,
        model_answer_display=None,
        ai=None,
        band_bounds=DEFAULT_BAND_BOUNDS,
        factual_weights=(30, 50),
        prompt_version=None,
        question_version_id=1,
        provenance=None,
        attempts_used=0,
    )


def test_primary_score_and_counts():
    items = [
        ItemForSummary(result=_assessed(80, "very_good")),
        ItemForSummary(result=_assessed(50, "needs_improvement")),
        ItemForSummary(result=_no_answer()),
        ItemForSummary(result=_no_speech()),
        ItemForSummary(result=None),
        ItemForSummary(result=_assessed(95, "very_good"), interrupted=True),
    ]
    summary = round_knowledge_summary(items)
    # (80 + 50 + 0) / 3; no_speech and unresolved are excluded, interrupted is excluded
    assert summary.primary_score == pytest.approx(130 / 3)
    assert summary.mean_factual == pytest.approx(130 / 3)
    assert (summary.assessed, summary.no_answer, summary.no_speech, summary.unresolved) == (
        2,
        1,
        1,
        1,
    )
    assert summary.provisional is True
    assert summary.band_distribution == {
        "weak": 1,
        "needs_improvement": 1,
        "good": 0,
        "very_good": 1,
    }


def test_language_mean_counts_assessable_only():
    items = [
        ItemForSummary(result=_assessed(80, language=90)),
        ItemForSummary(result=_assessed(60, language=70)),
        ItemForSummary(result=_no_answer()),
    ]
    summary = round_knowledge_summary(items)
    assert summary.mean_language == pytest.approx(80.0)  # (90 + 70) / 2
    assert summary.language_assessable == 2
    assert summary.primary_score == pytest.approx(140 / 3)
    assert summary.provisional is False


def test_fluency_mean_counts_assessable_only():
    items = [
        ItemForSummary(result=_assessed(80, fluency=70)),
        ItemForSummary(result=_assessed(80, fluency=None)),
        ItemForSummary(result=_assessed(80, fluency=50)),
    ]
    summary = round_knowledge_summary(items)
    assert summary.mean_fluency == pytest.approx(60.0)
    assert summary.fluency_assessable == 2


def test_followups_and_other_kinds_excluded_from_primary():
    items = [
        ItemForSummary(result=_assessed(40, "needs_improvement")),
        ItemForSummary(result=_assessed(100, "very_good"), role="followup"),
        ItemForSummary(result=_assessed(100, "very_good"), attempt_kind="immediate"),
        ItemForSummary(result=_assessed(100, "very_good"), attempt_kind="retry"),
        ItemForSummary(result=_assessed(100, "very_good"), attempt_kind="extension"),
        ItemForSummary(result=None, role="followup"),
    ]
    summary = round_knowledge_summary(items)
    assert summary.primary_score == 40
    assert summary.assessed == 1 and summary.unresolved == 0
    assert summary.band_distribution["very_good"] == 0
    assert summary.provisional is False


def test_empty_round_all_none():
    summary = round_knowledge_summary([])
    assert summary.primary_score is None
    assert summary.mean_correctness is None and summary.mean_completeness is None
    assert summary.mean_factual is None and summary.mean_language is None
    assert summary.mean_fluency is None
    assert summary.language_assessable == 0 and summary.fluency_assessable == 0
    assert summary.band_distribution == dict.fromkeys(
        ["weak", "needs_improvement", "good", "very_good"], 0
    )
    assert (summary.assessed, summary.no_answer, summary.no_speech, summary.unresolved) == (
        0,
        0,
        0,
        0,
    )
    assert summary.provisional is False


def test_only_unresolved_is_provisional_with_no_score():
    summary = round_knowledge_summary([ItemForSummary(result=None)])
    assert summary.primary_score is None
    assert summary.unresolved == 1 and summary.provisional is True
