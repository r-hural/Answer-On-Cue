import pytest
from pydantic import ValidationError

from app.features.delivery.schemas import AnswerMetrics, ClientEvents, Thresholds


def _events(**over):
    base = dict(
        question_ready_at=0,
        question_shown_at=0,
        prep_end_at=1,
        start_reason="button",
        capture_start_at=1,
        stopped_at=2,
        pre_roll_ms=0,
    )
    base.update(over)
    return base


def test_threshold_defaults():
    t = Thresholds()
    assert (t.speech_start_flag_ms, t.long_pause_ms, t.very_long_pause_ms) == (3000, 2000, 5000)


def test_client_events_rejects_bad_start_reason():
    with pytest.raises(ValidationError):
        ClientEvents(**_events(start_reason="x"))


def test_answer_metrics_json_round_trip():
    m = AnswerMetrics(
        preparation_used_ms=1,
        speech_start_latency_ms=None,
        total_response_latency_ms=None,
        answer_duration_ms=2,
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
        pauses=[{"start_ms": 1, "end_ms": 3000, "kind": "long"}],
        flags=["slow_start"],
        unavailable={"speech_start_latency_ms": "no_speech"},
        interrupted=False,
        detector="webrtcvad",
        thresholds=Thresholds(),
    )
    assert AnswerMetrics.model_validate_json(m.model_dump_json()) == m
