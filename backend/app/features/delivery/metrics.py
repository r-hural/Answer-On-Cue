from app.features.delivery.fillers import count_words, find_fillers
from app.features.delivery.schemas import (
    AnswerMetrics,
    ClientEvents,
    PauseMarker,
    Thresholds,
)
from app.features.languages.profiles import LanguageProfile
from app.features.recording.schemas import VadResult


def compute_answer_metrics(
    events: ClientEvents,
    vad: VadResult,
    transcript: str | None,
    thresholds: Thresholds | None = None,
    *,
    profile: LanguageProfile,
) -> AnswerMetrics:
    thresholds = thresholds or Thresholds()
    unavailable: dict[str, str] = {}
    intervals = vad.speech_intervals

    preparation_used_ms = max(0, round(events.prep_end_at - events.question_ready_at))
    answer_duration_ms = max(0, round(events.stopped_at - events.capture_start_at))

    onset: int | None = None
    leading: int | None = None
    if intervals:
        # VAD intervals are in recording time; the recording starts pre_roll_ms early.
        onset = intervals[0][0] - events.pre_roll_ms
        leading = max(0, onset)

    latency: int | None
    if events.start_reason == "auto":
        latency = 0
    elif onset is None:
        latency = None
        unavailable["speech_start_latency_ms"] = "no_speech"
    else:
        latency = max(0, onset)

    total: int | None = None
    if latency is not None:
        total = preparation_used_ms + latency
    else:
        unavailable["total_response_latency_ms"] = "no_speech"

    first_to_last: int | None = None
    trailing: int | None = None
    internal: int | None = None
    longest: int | None = None
    long_count: int | None = None
    very_long_count: int | None = None
    pauses: list[PauseMarker] = []
    if intervals:
        first_to_last = intervals[-1][1] - intervals[0][0]
        trailing = vad.duration_ms - intervals[-1][1]
        gaps = [(a[1], b[0]) for a, b in zip(intervals, intervals[1:], strict=False)]
        internal = sum(end - start for start, end in gaps)
        longest = max((end - start for start, end in gaps), default=0)
        for start, end in gaps:
            gap = end - start
            if gap >= thresholds.very_long_pause_ms:
                pauses.append(PauseMarker(start_ms=start, end_ms=end, kind="very_long"))
            elif gap >= thresholds.long_pause_ms:
                pauses.append(PauseMarker(start_ms=start, end_ms=end, kind="long"))
        long_count = len(pauses)
        very_long_count = sum(p.kind == "very_long" for p in pauses)
    else:
        for name in (
            "leading_silence_ms",
            "first_to_last_speech_ms",
            "trailing_silence_ms",
            "internal_silence_ms",
            "longest_internal_silence_ms",
            "long_pause_count",
            "very_long_pause_count",
        ):
            unavailable[name] = "no_speech"

    words = count_words(transcript) if transcript is not None else None
    fillers = find_fillers(transcript, profile) if transcript is not None else None
    if transcript is None:
        for name in ("words", "filler_count"):
            unavailable[name] = "no_transcript"

    # Rates use first-to-last speech minutes; zero or missing means unavailable, not 0.
    minutes: float | None = None
    reason = "no_speech"
    if first_to_last == 0:
        reason = "zero_interval"
    elif first_to_last is not None:
        minutes = first_to_last / 60000

    def rate(name: str, value: int | None, digits: int, needs_transcript: bool = False):
        if needs_transcript and transcript is None:
            unavailable[name] = "no_transcript"
            return None
        if minutes is None or value is None:
            unavailable[name] = reason
            return None
        return round(value / minutes, digits)

    ratio: float | None = None
    if minutes is None:
        unavailable["silence_ratio"] = reason
    else:
        ratio = round((internal or 0) / first_to_last, 3)
    pauses_per_min = rate("long_pauses_per_min", long_count, 2)
    wpm = rate("words_per_minute", words, 1, needs_transcript=True)
    filler_rate = rate(
        "filler_per_min", len(fillers) if fillers is not None else None, 2, needs_transcript=True
    )

    flags = []
    if latency is not None and latency > thresholds.speech_start_flag_ms:
        flags.append("slow_start")

    return AnswerMetrics(
        preparation_used_ms=preparation_used_ms,
        speech_start_latency_ms=latency,
        total_response_latency_ms=total,
        answer_duration_ms=answer_duration_ms,
        first_to_last_speech_ms=first_to_last,
        speech_ms=vad.speech_ms,
        leading_silence_ms=leading,
        trailing_silence_ms=trailing,
        internal_silence_ms=internal,
        silence_ratio=ratio,
        longest_internal_silence_ms=longest,
        long_pause_count=long_count,
        very_long_pause_count=very_long_count,
        long_pauses_per_min=pauses_per_min,
        words=words,
        words_per_minute=wpm,
        filler_count=len(fillers) if fillers is not None else None,
        filler_per_min=filler_rate,
        filler_examples=(fillers or [])[:5],
        pauses=pauses,
        flags=flags,
        unavailable=unavailable,
        interrupted=events.interrupted,
        detector=vad.engine,
        thresholds=thresholds,
    )
