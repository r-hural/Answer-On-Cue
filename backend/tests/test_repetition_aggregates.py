"""The `series` keyword: another series of main answers, the default byte-identical."""

from test_assessment_aggregate import _assessed, _no_answer, _no_speech
from test_delivery_aggregate import _attempt, _five

from app.features.assessment.aggregate import ItemForSummary, round_knowledge_summary
from app.features.delivery.aggregate import aggregate_round


def _items() -> list[ItemForSummary]:
    return [
        ItemForSummary(result=_assessed(80, band="very_good")),
        ItemForSummary(result=_assessed(40, band="needs_improvement")),
        ItemForSummary(result=_no_answer()),
        ItemForSummary(result=_no_speech()),
        ItemForSummary(result=None),
        ItemForSummary(result=_assessed(90, band="very_good"), attempt_kind="immediate"),
        ItemForSummary(result=_assessed(70, band="good"), attempt_kind="retry"),
        ItemForSummary(result=_assessed(20, band="weak"), attempt_kind="retry"),
        ItemForSummary(result=_assessed(60), role="followup", attempt_kind="followup"),
        ItemForSummary(result=_assessed(10, band="weak"), attempt_kind="retry", interrupted=True),
    ]


def test_series_default_unchanged():
    items = _items()
    assert round_knowledge_summary(items) == round_knowledge_summary(items, series="initial")
    assert round_knowledge_summary(items).model_dump_json() == (
        round_knowledge_summary(items, series="initial").model_dump_json()
    )
    attempts = _five()
    assert aggregate_round(attempts).model_dump_json() == (
        aggregate_round(attempts, series="initial").model_dump_json()
    )
    default = round_knowledge_summary(items)
    assert default.primary_score == (80 + 40 + 0) / 3  # the original main answers only


def test_series_retry_and_immediate():
    items = _items()
    retry = round_knowledge_summary(items, series="retry")
    assert retry.primary_score == (70 + 20) / 2
    assert retry.band_distribution == {"weak": 1, "needs_improvement": 0, "good": 1, "very_good": 0}
    assert (retry.assessed, retry.unresolved, retry.provisional) == (2, 0, False)
    immediate = round_knowledge_summary(items, series="immediate")
    assert immediate.primary_score == 90
    assert immediate.band_distribution["very_good"] == 1

    attempts = [
        _attempt("initial", factual=50, preparation_used_ms=1000),
        _attempt("immediate", factual=80, preparation_used_ms=3000),
        _attempt("immediate", factual=40, preparation_used_ms=5000),
        _attempt("retry", factual=70, preparation_used_ms=7000),
        _attempt("immediate", factual=90, preparation_used_ms=9000, interrupted=True),
    ]
    summary = aggregate_round(attempts, series="immediate")
    assert summary.count_included == 2
    assert summary.count_excluded_other_kind == 2
    assert summary.count_excluded_interrupted == 1
    assert summary.median_preparation_used_ms == 4000
    retry_summary = aggregate_round(attempts, series="retry")
    assert (retry_summary.count_included, retry_summary.median_preparation_used_ms) == (1, 7000)
