from app.features.delivery.aggregate import aggregate_round
from app.features.delivery.schemas import AnswerMetrics, AttemptForAggregate, Thresholds


def _metrics(**over) -> AnswerMetrics:
    base = dict(
        preparation_used_ms=0,
        speech_start_latency_ms=None,
        total_response_latency_ms=None,
        answer_duration_ms=0,
        first_to_last_speech_ms=None,
        speech_ms=0,
        leading_silence_ms=None,
        trailing_silence_ms=None,
        internal_silence_ms=None,
        silence_ratio=None,
        longest_internal_silence_ms=None,
        long_pause_count=None,
        very_long_pause_count=None,
        long_pauses_per_min=None,
        words=None,
        words_per_minute=None,
        filler_count=None,
        filler_per_min=None,
        filler_examples=[],
        pauses=[],
        flags=[],
        unavailable={},
        interrupted=False,
        detector="test-vad",
        thresholds=Thresholds(),
    )
    base.update(over)
    return AnswerMetrics(**base)


def _attempt(kind="initial", factual=None, **over) -> AttemptForAggregate:
    return AttemptForAggregate(metrics=_metrics(**over), attempt_kind=kind, factual_score=factual)


def _five():
    a = _attempt(
        factual=80,
        preparation_used_ms=4000,
        speech_start_latency_ms=2000,
        total_response_latency_ms=6000,
        first_to_last_speech_ms=60000,
        internal_silence_ms=12000,
        longest_internal_silence_ms=3000,
        long_pause_count=2,
        words_per_minute=120.0,
        filler_count=3,
    )
    b = _attempt(
        factual=40,
        preparation_used_ms=5000,
        speech_start_latency_ms=4000,
        total_response_latency_ms=9000,
        first_to_last_speech_ms=30000,
        internal_silence_ms=3000,
        longest_internal_silence_ms=1000,
        long_pause_count=0,
        words_per_minute=100.0,
        filler_count=1,
    )
    c = _attempt(preparation_used_ms=3000)  # no speech, no transcript, unassessed
    d = _attempt(interrupted=True, preparation_used_ms=1, speech_start_latency_ms=1)
    e = _attempt(kind="immediate", preparation_used_ms=1, speech_start_latency_ms=1)
    return [a, b, c, d, e]


def test_round_with_five_attempts():
    s = aggregate_round(_five())
    # included: a, b, c; interrupted d and non-initial e are counted but excluded
    assert (s.count_included, s.count_excluded_interrupted, s.count_excluded_other_kind) == (
        3,
        1,
        1,
    )
    # prep [4000, 5000, 3000] -> 4000
    assert s.median_preparation_used_ms == 4000
    # latency only a, b: [2000, 4000] -> 3000
    assert s.median_speech_start_latency_ms == 3000
    # total [6000, 9000] -> 7500; successful (factual >= 60) is only a -> 6000
    assert s.median_total_response_latency_ms == 7500
    assert s.median_total_response_latency_successful_ms == 6000
    # target 3000: a (2000) met, b (4000) not; c has no latency so denominator is 2
    assert s.target_attainment == {"speech_start_latency_ms": (1, 2)}
    # longest pauses [3000, 1000] -> 2000
    assert s.median_longest_pause_ms == 2000
    # (2 + 0) long pauses over (60000 + 30000) ms = 1.5 min -> 1.33
    assert s.long_pauses_per_speaking_min == 1.33
    # (12000 + 3000) / 90000 = 0.1667 -> 0.167
    assert s.aggregate_silence_ratio == 0.167
    # wpm [120, 100] -> 110.0
    assert s.median_words_per_minute == 110.0
    # (3 + 1) fillers over 1.5 min = 2.67
    assert s.filler_per_min == 2.67


def test_custom_targets_include_total_latency():
    s = aggregate_round(_five(), {"total_response_latency_ms": 7000})
    # totals 6000 (met), 9000 (not)
    assert s.target_attainment == {"total_response_latency_ms": (1, 2)}


def test_all_unavailable_gives_none_not_zero():
    s = aggregate_round([_attempt(), _attempt(first_to_last_speech_ms=0)])
    assert s.count_included == 2
    assert s.median_speech_start_latency_ms is None
    assert s.median_total_response_latency_ms is None
    assert s.median_total_response_latency_successful_ms is None
    assert s.target_attainment == {"speech_start_latency_ms": (0, 0)}
    assert s.median_longest_pause_ms is None
    assert s.long_pauses_per_speaking_min is None
    assert s.aggregate_silence_ratio is None
    assert s.median_words_per_minute is None
    assert s.filler_per_min is None


def test_empty_round():
    s = aggregate_round([])
    assert (s.count_included, s.count_excluded_interrupted, s.count_excluded_other_kind) == (
        0,
        0,
        0,
    )
    assert s.median_preparation_used_ms is None
    assert s.median_speech_start_latency_ms is None
    assert s.long_pauses_per_speaking_min is None
    assert s.aggregate_silence_ratio is None
    assert s.filler_per_min is None


def test_success_threshold_follows_band_table():
    # Default 60: only the factual-80 attempt (total 6000) is successful.
    assert aggregate_round(_five()).median_total_response_latency_successful_ms == 6000
    # A round whose Dobrze band starts at 40: both (6000, 9000) -> median 7500.
    s = aggregate_round(_five(), success_factual_min=40)
    assert s.median_total_response_latency_successful_ms == 7500
