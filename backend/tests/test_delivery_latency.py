from app.features.delivery.metrics import compute_answer_metrics
from app.features.delivery.schemas import ClientEvents
from app.features.languages.profiles import PROFILES
from app.features.recording.schemas import VadResult


def _events(**over):
    base = dict(
        question_ready_at=1000,
        question_shown_at=500,
        prep_end_at=6000,
        start_reason="button",
        capture_start_at=6000,
        stopped_at=16000,
        pre_roll_ms=1000,
    )
    base.update(over)
    return ClientEvents(**base)


def _vad(intervals, duration=11000):
    speech = sum(e - s for s, e in intervals)
    return VadResult(
        engine="test-vad",
        speech_intervals=intervals,
        speech_ms=speech,
        leading_silence_ms=intervals[0][0] if intervals else duration,
        trailing_silence_ms=duration - intervals[-1][1] if intervals else 0,
        duration_ms=duration,
    )


def test_button_start_latency():
    # prep = 6000 - 1000 = 5000; onset = 3500 - 1000 = 2500; total = 5000 + 2500 = 7500
    m = compute_answer_metrics(_events(), _vad([(3500, 8000)]), None, profile=PROFILES["pl"])
    assert m.preparation_used_ms == 5000
    assert m.speech_start_latency_ms == 2500
    assert m.total_response_latency_ms == 7500
    assert m.leading_silence_ms == 2500
    assert m.answer_duration_ms == 10000


def test_auto_start_latency_zero():
    # auto: latency 0 whatever the onset; total = prep = 2000 - 1000 = 1000
    ev = _events(start_reason="auto", prep_end_at=2000, capture_start_at=2000)
    m = compute_answer_metrics(ev, _vad([(1000, 5000)]), None, profile=PROFILES["pl"])
    assert m.speech_start_latency_ms == 0
    assert m.preparation_used_ms == 1000
    assert m.total_response_latency_ms == 1000


def test_speech_inside_preroll_not_negative():
    # onset = 400 - 1000 = -600 -> clamped to 0
    m = compute_answer_metrics(_events(), _vad([(400, 5000)]), None, profile=PROFILES["pl"])
    assert m.speech_start_latency_ms == 0
    assert m.leading_silence_ms == 0


def test_timer_start_no_speech_unavailable():
    m = compute_answer_metrics(
        _events(start_reason="timer"), _vad([]), None, profile=PROFILES["pl"]
    )
    assert m.speech_start_latency_ms is None
    assert m.total_response_latency_ms is None
    assert m.unavailable["speech_start_latency_ms"] == "no_speech"
    assert m.unavailable["total_response_latency_ms"] == "no_speech"
    assert m.flags == []


def test_slow_start_flag():
    # onset = 4001 - 1000 = 3001 -> flagged; 4000 - 1000 = 3000 -> not flagged
    slow = compute_answer_metrics(_events(), _vad([(4001, 8000)]), None, profile=PROFILES["pl"])
    edge = compute_answer_metrics(_events(), _vad([(4000, 8000)]), None, profile=PROFILES["pl"])
    assert slow.speech_start_latency_ms == 3001
    assert slow.flags == ["slow_start"]
    assert edge.speech_start_latency_ms == 3000
    assert edge.flags == []


def test_negative_prep_clamped():
    # prep_end 900 - ready 1000 = -100 -> 0
    m = compute_answer_metrics(
        _events(prep_end_at=900), _vad([(2000, 5000)]), None, profile=PROFILES["pl"]
    )
    assert m.preparation_used_ms == 0


def test_no_speech_leading_silence_has_reason():
    m = compute_answer_metrics(
        _events(start_reason="timer"), _vad([]), None, profile=PROFILES["pl"]
    )
    assert m.leading_silence_ms is None
    assert m.unavailable["leading_silence_ms"] == "no_speech"
