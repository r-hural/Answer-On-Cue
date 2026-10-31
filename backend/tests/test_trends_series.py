from datetime import timedelta

from test_trends_support import T0, assessment, metrics, row

from app.features.assessment.aggregate import ItemForSummary, round_knowledge_summary
from app.features.assessment.schemas import AssessmentResult
from app.features.delivery.aggregate import aggregate_round
from app.features.delivery.schemas import AnswerMetrics, AttemptForAggregate
from app.features.trends import series
from app.features.trends.schemas import TrendFilters


def points(rows, **over):
    filters = TrendFilters(subject_id=1, **over)
    selected = series.select_answers(rows, filters)
    return [series.point_for(chosen, filters.include_interrupted) for chosen in selected.values()]


def only(rows, **over):
    (point,) = points(rows, **over)
    return point


def test_main_series_counts_only_independent_first_attempts():
    rows = [
        row(item_id=1, kind="initial", assess=assessment(factual=40)),
        row(item_id=1, kind="immediate", assess=assessment(factual=90)),
        row(item_id=2, kind="initial", assess=assessment(factual=80)),
    ]
    point = only(rows)
    assert (point.factual, point.answers, point.assisted) == (60.0, 2, 0)
    point = only(rows, include_assisted=True)
    assert (point.factual, point.answers, point.assisted) == (70.0, 3, 1)
    point = only(rows, series="immediate")
    assert (point.factual, point.answers) == (90.0, 1)


def test_extension_and_retry_rounds_apart():
    rows = [
        row(round_id=1, item_id=1, at=T0),
        row(round_id=2, item_id=2, round_type="extension", at=T0 + timedelta(hours=1)),
        row(round_id=3, item_id=3, kind="retry", round_type="retry", at=T0 + timedelta(hours=2)),
        row(round_id=4, item_id=4, round_type="retry", at=T0 + timedelta(hours=3)),
    ]
    assert [p.round_id for p in points(rows)] == [1]
    assert [p.round_id for p in points(rows, series="extension")] == [2]
    assert [p.round_id for p in points(rows, series="retry")] == [3]


def test_followups_separate():
    rows = [
        row(item_id=1, assess=assessment(factual=80)),
        row(item_id=2, role="followup", kind="followup", assess=assessment(factual=20)),
    ]
    assert only(rows).factual == 80.0
    assert only(rows, series="followup").factual == 20.0


def test_restarted_answer_out_of_speed_by_default():
    rows = [
        row(item_id=1, kind="initial", outcome="interrupted"),
        row(item_id=1, kind="initial", metr=metrics(preparation_used_ms=2000)),
        row(item_id=2, kind="initial", metr=metrics(preparation_used_ms=8000)),
    ]
    point = only(rows)
    assert (point.answers, point.restarted) == (2, 1)
    assert (point.median_preparation_used_ms, point.speed_answers) == (8000, 1)
    point = only(rows, include_interrupted=True)
    assert (point.median_preparation_used_ms, point.speed_answers) == (5000, 2)


def test_point_matches_round_summary_values():
    rows = [
        row(
            item_id=n,
            assess=assessment(factual=f, language=60 + n),
            metr=metrics(preparation_used_ms=3000 * n, long_pause_count=lp),
        )
        for n, (f, lp) in enumerate([(40, 3), (75, 1)], start=1)
    ]
    rows.append(row(item_id=9, outcome="dont_know"))
    point = only(rows)

    results = [AssessmentResult.model_validate(r.assessment) for r in rows[:2]]
    summary = round_knowledge_summary(
        [ItemForSummary(result=r) for r in results]
        + [ItemForSummary(result=series.no_answer(rows[2]))]
    )
    delivery = aggregate_round(
        [
            AttemptForAggregate(
                metrics=AnswerMetrics.model_validate(r.metrics),
                attempt_kind="initial",
                factual_score=res.scores.factual,
            )
            for r, res in zip(rows[:2], results, strict=True)
        ]
    )
    assert point.primary == summary.primary_score
    assert point.factual == summary.mean_factual
    assert point.language == summary.mean_language
    assert point.language_assessable == 2
    assert point.median_preparation_used_ms == delivery.median_preparation_used_ms
    assert point.median_speech_start_latency_ms == delivery.median_speech_start_latency_ms
    assert point.long_pauses_per_min == delivery.long_pauses_per_speaking_min
    assert point.silence_ratio == delivery.aggregate_silence_ratio
    # "I do not know" counts 0 in the primary score and has no speed value.
    assert point.answers == 3 and point.speed_answers == 2


def test_unavailable_not_zero():
    silent = metrics(
        speech_start_latency_ms=None,
        total_response_latency_ms=None,
        first_to_last_speech_ms=None,
        internal_silence_ms=None,
        silence_ratio=None,
        long_pause_count=None,
        filler_count=None,
    )
    rows = [row(item_id=n, metr=silent) for n in (1, 2)]
    point = only(rows)
    assert point.median_speech_start_latency_ms is None
    assert point.median_total_response_latency_ms is None
    assert point.long_pauses_per_min is None and point.silence_ratio is None
    assert point.filler_per_min is None and point.filler_reliable == 0
    assert point.fluency is None and point.fluency_assessable == 0

    rows.append(row(item_id=3, processing="processing", assess=None))
    point = only(rows)
    assert (point.unresolved, point.provisional, point.answers) == (1, True, 2)
    assert point.factual == 66.0


def test_filler_rate_only_where_reliable():
    rows = [
        row(
            item_id=1,
            metr=metrics(filler_count=3, first_to_last_speech_ms=60000),
            assess=assessment(fluency=70),
        ),
        row(
            item_id=2,
            metr=metrics(filler_count=10, first_to_last_speech_ms=60000),
            assess=assessment(fluency=70, doubts=1),
        ),
        row(
            item_id=3,
            metr=metrics(filler_count=5, first_to_last_speech_ms=60000),
            assess=assessment(coverage="none"),
        ),
    ]
    point = only(rows)
    assert (point.filler_per_min, point.filler_reliable) == (3.0, 1)
    assert only(rows[1:]).filler_per_min is None
    unknown = [row(item_id=1, metr=metrics(filler_count=None), assess=assessment(fluency=70))]
    assert only(unknown).filler_per_min is None
    assert not series.reliable_filler(unknown[0].assessment, unknown[0].metrics)


def test_rates_use_speech_minutes():
    rows = [
        row(
            item_id=1,
            metr=metrics(
                long_pause_count=2,
                first_to_last_speech_ms=60000,
                internal_silence_ms=6000,
                answer_duration_ms=90000,
            ),
        ),
        row(
            item_id=2,
            metr=metrics(
                long_pause_count=1,
                first_to_last_speech_ms=30000,
                internal_silence_ms=3000,
                answer_duration_ms=10000,
            ),
        ),
    ]
    point = only(rows)
    assert point.long_pauses_per_min == 2.0
    assert point.silence_ratio == 0.1  # 9000 / 90000


def test_successful_latency_uses_round_snapshot():
    rows = [row(assess=assessment(factual=65, bounds=(30, 70, 85)), bounds=(30, 70, 85))]
    assert only(rows).median_total_response_latency_successful_ms is None
    rows = [row(assess=assessment(factual=65), bounds=(30, 60, 80))]
    assert only(rows).median_total_response_latency_successful_ms == 4800


def test_changes_marked():
    def rnd(n, bounds=(30, 60, 80), model="m1", detector="d1"):
        return row(
            round_id=n,
            item_id=n,
            at=T0 + timedelta(days=n),
            bounds=bounds,
            assess=assessment(model=model, bounds=bounds),
            metr=metrics(detector=detector),
        )

    rows = [
        rnd(1),
        rnd(2, bounds=(30, 70, 85)),
        rnd(3, bounds=(30, 70, 85), model="m2", detector="d2"),
    ]
    pts = points(rows)
    models = [{r.assessment["provenance"]["model"]} for r in rows]
    detectors = [{r.metrics["detector"]} for r in rows]
    series.mark_changes(pts, models, detectors)
    assert [p.changes for p in pts] == [[], ["band_table"], ["model", "analysis"]]

    pts = points(rows)
    series.mark_changes(pts, [set(), set(), set()], [set(), set(), set()])
    assert [p.changes for p in pts] == [[], ["band_table"], []]


def test_running_rounds_pending_and_empty_selection():
    rows = [
        row(round_id=1, item_id=1, round_status="active"),
        row(round_id=2, item_id=2, round_status="paused"),
        row(round_id=3, item_id=3, outcome="pending"),
        row(round_id=4, item_id=4, kind="followup"),  # a main item with only a follow-up attempt
        row(round_id=5, item_id=5, round_status="abandoned"),
    ]
    pts = points(rows)
    assert [p.round_id for p in pts] == [5]
    assert pts[0].status == "abandoned" and pts[0].provisional is True


def test_skipped_answer_again_is_no_answer():
    rows = [
        row(item_id=1, assess=assessment(factual=50)),
        row(item_id=1, kind="immediate", outcome="skipped"),
        row(item_id=2, kind="immediate", outcome="dont_know"),
    ]
    assert points(rows, series="immediate")[0].answers == 1  # the dont_know, counted 0
    point = only(rows, include_assisted=True)
    assert (point.assisted, point.answers) == (1, 2)


def test_interrupted_immediate_does_not_restart_the_original():
    rows = [
        row(item_id=1, kind="initial"),
        row(item_id=1, kind="immediate", outcome="interrupted"),
        row(item_id=1, kind="immediate"),
    ]
    assert only(rows).restarted == 0
    assert only(rows, series="immediate").restarted == 1
