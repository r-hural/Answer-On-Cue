from statistics import median

from app.features.delivery.schemas import (
    AttemptForAggregate,
    RoundDeliverySummary,
)

DEFAULT_TARGETS = {"speech_start_latency_ms": 3000}
SUCCESS_FACTUAL_MIN = 60  # default lower bound of the Good band


def _median_int(values: list[int | float]) -> int | None:
    return round(median(values)) if values else None


def _values(attempts: list[AttemptForAggregate], name: str) -> list:
    out = [getattr(a.metrics, name) for a in attempts]
    return [v for v in out if v is not None]


def aggregate_round(
    attempts: list[AttemptForAggregate],
    targets: dict[str, int] | None = None,
    *,
    success_factual_min: int = SUCCESS_FACTUAL_MIN,
    series: str = "initial",
) -> RoundDeliverySummary:
    """success_factual_min: pass the round's band-table snapshot (Good lower bound).
    series: the attempt kind counted (another kind is counted as excluded)."""
    targets = DEFAULT_TARGETS if targets is None else targets
    interrupted = [a for a in attempts if a.metrics.interrupted]
    other_kind = [a for a in attempts if not a.metrics.interrupted and a.attempt_kind != series]
    included = [a for a in attempts if not a.metrics.interrupted and a.attempt_kind == series]

    successful = [
        a
        for a in included
        if a.factual_score is not None and a.factual_score >= success_factual_min
    ]

    attainment: dict[str, tuple[int, int]] = {}
    for name, target in targets.items():
        values = _values(included, name)
        attainment[name] = (sum(v <= target for v in values), len(values))

    # Sums only over attempts that have every value the rate needs (never zero-filled).
    with_interval = [a for a in included if a.metrics.first_to_last_speech_ms]
    minutes_pauses = [a for a in with_interval if a.metrics.long_pause_count is not None]
    minutes_silence = [a for a in with_interval if a.metrics.internal_silence_ms is not None]
    minutes_fillers = [a for a in with_interval if a.metrics.filler_count is not None]

    def total_minutes(items: list[AttemptForAggregate]) -> float:
        return sum(a.metrics.first_to_last_speech_ms for a in items) / 60000

    pauses_per_min = None
    if minutes_pauses:
        count = sum(a.metrics.long_pause_count for a in minutes_pauses)
        pauses_per_min = round(count / total_minutes(minutes_pauses), 2)

    silence_ratio = None
    if minutes_silence:
        internal = sum(a.metrics.internal_silence_ms for a in minutes_silence)
        span = sum(a.metrics.first_to_last_speech_ms for a in minutes_silence)
        silence_ratio = round(internal / span, 3)

    filler_per_min = None
    if minutes_fillers:
        count = sum(a.metrics.filler_count for a in minutes_fillers)
        filler_per_min = round(count / total_minutes(minutes_fillers), 2)

    wpm = _values(included, "words_per_minute")

    return RoundDeliverySummary(
        count_included=len(included),
        count_excluded_interrupted=len(interrupted),
        count_excluded_other_kind=len(other_kind),
        median_preparation_used_ms=_median_int(_values(included, "preparation_used_ms")),
        median_speech_start_latency_ms=_median_int(_values(included, "speech_start_latency_ms")),
        median_total_response_latency_ms=_median_int(
            _values(included, "total_response_latency_ms")
        ),
        median_total_response_latency_successful_ms=_median_int(
            _values(successful, "total_response_latency_ms")
        ),
        target_attainment=attainment,
        median_longest_pause_ms=_median_int(_values(included, "longest_internal_silence_ms")),
        long_pauses_per_speaking_min=pauses_per_min,
        aggregate_silence_ratio=silence_ratio,
        median_words_per_minute=round(median(wpm), 1) if wpm else None,
        filler_per_min=filler_per_min,
    )
