import pytest

from app.features.rounds.state import (
    NOT_NOW,
    ROUND_PAUSED,
    MachineState,
    TransitionError,
    settle,
    transition,
)


def state(phase: str, timing: str = "practice", **kwargs) -> MachineState:
    kwargs.setdefault("status", "active")
    kwargs.setdefault("paused_from_phase", None)
    kwargs.setdefault("is_last", False)
    return MachineState(phase=phase, feedback_timing=timing, **kwargs)


def test_answer_again_from_feedback():
    result = transition(state("feedback", repeat_allowed=True), "answer_again")
    assert (result.status, result.phase) == ("active", "question")
    assert result.new_attempt is True
    assert result.attempt_kind == "immediate"
    assert result.part is None  # the round stays in its main part
    assert result.advance is False and result.outcome is None


@pytest.mark.parametrize(
    "machine,message",
    [
        (state("feedback", repeat_allowed=False), NOT_NOW),
        (state("feedback", "exam", repeat_allowed=True), NOT_NOW),
        (state("processing", repeat_allowed=True, result_ready=True), NOT_NOW),
        (state("question", repeat_allowed=True), NOT_NOW),
        (state("feedback", repeat_allowed=True, part="followup"), NOT_NOW),
        (
            state("feedback", status="paused", paused_from_phase="feedback", repeat_allowed=True),
            ROUND_PAUSED,  # a paused round refuses every action but resume and abandon
        ),
    ],
)
def test_answer_again_refused(machine, message):
    with pytest.raises(TransitionError) as error:
        transition(machine, "answer_again")
    assert error.value.message == message


@pytest.mark.parametrize("follow_up", ["ready", "generating", "failed", None])
def test_repeat_result_goes_to_feedback_not_follow_up(follow_up):
    machine = state(
        "processing", repeating=True, result_ready=True, follow_ups_on=True, follow_up=follow_up
    )
    moved = settle(machine)
    assert moved is not None
    assert (moved.status, moved.phase, moved.part) == ("active", "feedback", "main")
    # the original answer (not repeating) still waits for or asks its follow-up
    original = settle(
        state("processing", result_ready=True, follow_ups_on=True, follow_up=follow_up)
    )
    if follow_up == "ready":
        assert (original.phase, original.part) == ("question", "followup")
    elif follow_up in ("generating", "failed"):
        assert original is None


def test_repeat_still_processing_does_not_settle():
    assert settle(state("processing", repeating=True, result_ready=False)) is None


@pytest.mark.parametrize("action,outcome", [("skip", "skipped"), ("dont_know", "dont_know")])
def test_repeat_skip_and_dont_know_back_to_feedback(action, outcome):
    result = transition(state("preparation", repeating=True, follow_ups_on=True), action)
    assert (result.phase, result.outcome, result.part) == ("feedback", outcome, "main")
    assert result.advance is False


def test_resume_keeps_kind():
    paused = state("recording", status="paused", paused_from_phase="recording", repeating=True)
    result = transition(paused, "resume")
    assert (result.phase, result.new_attempt) == ("question", True)
    assert result.attempt_kind is None  # _apply keeps the interrupted attempt's kind
