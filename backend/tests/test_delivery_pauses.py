from app.features.delivery.metrics import compute_answer_metrics
from app.features.delivery.schemas import ClientEvents, Thresholds
from app.features.languages.profiles import PROFILES
from app.features.recording.schemas import VadResult


def _events(**over):
    base = dict(
        question_ready_at=0,
        question_shown_at=0,
        prep_end_at=0,
        start_reason="button",
        capture_start_at=0,
        stopped_at=23000,
        pre_roll_ms=0,
    )
    base.update(over)
    return ClientEvents(**base)


def _vad(intervals, duration=23000, engine="test-vad"):
    return VadResult(
        engine=engine,
        speech_intervals=intervals,
        speech_ms=sum(e - s for s, e in intervals),
        leading_silence_ms=0,
        trailing_silence_ms=0,
        duration_ms=duration,
    )


def test_internal_gaps_and_markers():
    # gaps: 6500-4000 = 2500 (long), 14500-9000 = 5500 (very_long); internal = 8000
    # first_to_last = 20000 - 1000 = 19000; ratio = 8000/19000 = 0.421
    # per min = 2 / (19000/60000) = 6.316 -> 6.32; trailing = 23000 - 20000 = 3000
    vad = _vad([(1000, 4000), (6500, 9000), (14500, 20000)])
    m = compute_answer_metrics(_events(), vad, None, profile=PROFILES["pl"])
    assert m.first_to_last_speech_ms == 19000
    assert m.internal_silence_ms == 8000
    assert m.longest_internal_silence_ms == 5500
    assert m.silence_ratio == 0.421
    assert m.long_pause_count == 2
    assert m.very_long_pause_count == 1
    assert m.long_pauses_per_min == 6.32
    assert m.trailing_silence_ms == 3000
    assert m.leading_silence_ms == 1000
    assert [(p.start_ms, p.end_ms, p.kind) for p in m.pauses] == [
        (4000, 6500, "long"),
        (9000, 14500, "very_long"),
    ]


def test_trailing_silence_not_an_internal_pause():
    m = compute_answer_metrics(
        _events(), _vad([(1000, 2000)], duration=22000), None, profile=PROFILES["pl"]
    )
    assert m.trailing_silence_ms == 20000
    assert m.internal_silence_ms == 0
    assert m.long_pause_count == 0
    assert m.pauses == []


def test_single_interval_no_internal_pause():
    m = compute_answer_metrics(_events(), _vad([(1000, 11000)]), None, profile=PROFILES["pl"])
    assert m.internal_silence_ms == 0
    assert m.longest_internal_silence_ms == 0
    assert m.silence_ratio == 0.0
    assert m.long_pause_count == 0
    assert m.long_pauses_per_min == 0.0


def test_short_gap_below_long_threshold_has_no_marker():
    # gap 2999 - 1000 = 1999 < 2000
    m = compute_answer_metrics(
        _events(), _vad([(0, 1000), (2999, 5000)]), None, profile=PROFILES["pl"]
    )
    assert m.internal_silence_ms == 1999
    assert m.pauses == []
    assert m.long_pause_count == 0


def test_wpm_from_transcript():
    # 57 words over 19000 ms: 57 / (19000/60000) = 180.0
    vad = _vad([(1000, 20000)])
    m = compute_answer_metrics(_events(), vad, " ".join(["słowo"] * 57), profile=PROFILES["pl"])
    assert m.words == 57
    assert m.words_per_minute == 180.0


def test_zero_interval_rates_none():
    # a single zero-length interval: no rate may become 0
    m = compute_answer_metrics(
        _events(), _vad([(5000, 5000)]), "ala ma kota yyy", profile=PROFILES["pl"]
    )
    assert m.first_to_last_speech_ms == 0
    assert m.silence_ratio is None
    assert m.long_pauses_per_min is None
    assert m.words_per_minute is None
    assert m.filler_per_min is None
    assert m.words == 4
    assert m.filler_count == 1
    for name in ("silence_ratio", "long_pauses_per_min", "words_per_minute", "filler_per_min"):
        assert m.unavailable[name] == "zero_interval"


def test_no_speech_everything_none():
    m = compute_answer_metrics(_events(), _vad([]), "tekst", profile=PROFILES["pl"])
    assert m.first_to_last_speech_ms is None
    assert m.trailing_silence_ms is None
    assert m.internal_silence_ms is None
    assert m.long_pause_count is None
    assert m.silence_ratio is None
    assert m.unavailable["silence_ratio"] == "no_speech"
    assert m.unavailable["long_pause_count"] == "no_speech"


def test_no_transcript_words_none_reason():
    m = compute_answer_metrics(_events(), _vad([(1000, 7000)]), None, profile=PROFILES["pl"])
    assert m.words is None
    assert m.words_per_minute is None
    assert m.filler_count is None
    assert m.filler_per_min is None
    assert m.unavailable["words"] == "no_transcript"
    assert m.unavailable["words_per_minute"] == "no_transcript"
    assert m.unavailable["filler_count"] == "no_transcript"
    assert m.unavailable["filler_per_min"] == "no_transcript"


def test_filler_rate_per_min():
    # 3 fillers (yyy, eee, wiesz) over 30000 ms = 0.5 min -> 6.0 per min
    m = compute_answer_metrics(
        _events(),
        _vad([(0, 30000)], duration=30000),
        "yyy to eee jest wiesz ok",
        profile=PROFILES["pl"],
    )
    assert m.filler_count == 3
    assert m.filler_per_min == 6.0
    assert m.filler_examples == ["yyy", "eee", "wiesz"]
    assert m.fillers_lower_bound is True


def test_filler_examples_capped_at_five():
    m = compute_answer_metrics(
        _events(), _vad([(0, 30000)], duration=30000), "yyy " * 7, profile=PROFILES["pl"]
    )
    assert m.filler_count == 7
    assert len(m.filler_examples) == 5


def test_interrupted_flag_passthrough():
    m = compute_answer_metrics(
        _events(interrupted=True), _vad([(0, 1000)]), None, profile=PROFILES["pl"]
    )
    assert m.interrupted is True


def test_detector_and_thresholds_snapshotted():
    th = Thresholds(long_pause_ms=1000, very_long_pause_ms=1800)
    vad = _vad([(0, 1000), (2200, 3000)], engine="webrtcvad-2")
    m = compute_answer_metrics(_events(), vad, None, th, profile=PROFILES["pl"])
    # gap 2200 - 1000 = 1200 >= 1000 -> long (< 1800)
    assert m.detector == "webrtcvad-2"
    assert m.thresholds == th
    assert [p.kind for p in m.pauses] == ["long"]
    assert m.approximate is True
