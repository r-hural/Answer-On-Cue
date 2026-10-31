import pytest

from app.features.rounds.state import (
    NOT_NOW,
    ROUND_OVER,
    ROUND_PAUSED,
    MachineState,
    TransitionError,
    settle,
    transition,
)

PHASES = ("question", "preparation", "recording", "processing", "feedback", "summary")
ACTIONS = (
    "question_ready",
    "start_answer",
    "stop",
    "dont_know",
    "skip",
    "mic_failure",
    "next",
    "pause",
    "resume",
    "abandon",
)


def state(phase: str, timing: str = "practice", **kwargs) -> MachineState:
    kwargs.setdefault("status", "active")
    kwargs.setdefault("paused_from_phase", None)
    kwargs.setdefault("is_last", False)
    return MachineState(phase=phase, feedback_timing=timing, **kwargs)


# (phase, action, practice → (phase, outcome), exam → (phase, outcome)); "next" = next item.
TABLE = [
    ("question", "question_ready", ("preparation", None), ("preparation", None)),
    ("question", "skip", ("feedback", "skipped"), ("next", "skipped")),
    ("preparation", "skip", ("feedback", "skipped"), ("next", "skipped")),
    ("preparation", "start_answer", ("recording", None), ("recording", None)),
    ("preparation", "dont_know", ("feedback", "dont_know"), ("next", "dont_know")),
    ("recording", "dont_know", ("feedback", "dont_know"), ("next", "dont_know")),
    (
        "preparation",
        "mic_failure",
        ("feedback", "technical_failure"),
        ("next", "technical_failure"),
    ),
    ("recording", "mic_failure", ("feedback", "technical_failure"), ("next", "technical_failure")),
    ("recording", "stop", ("processing", "answered"), ("next", "answered")),
]


@pytest.mark.parametrize("phase,action,practice,exam", TABLE)
@pytest.mark.parametrize("timing", ["practice", "exam"])
def test_phase_table(phase, action, practice, exam, timing):
    expected_phase, outcome = practice if timing == "practice" else exam
    result = transition(state(phase, timing), action, start_reason="button")
    assert result.status == "active"
    assert result.outcome == outcome
    if expected_phase == "next":
        assert (result.phase, result.advance) == ("question", True)
    else:
        assert (result.phase, result.advance) == (expected_phase, False)


def test_question_ready_starts_attempt():
    assert transition(state("question"), "question_ready").start_attempt


def test_start_answer_keeps_reason():
    result = transition(state("preparation"), "start_answer", start_reason="timer")
    assert result.start_reason == "timer"
    assert not result.end_attempt


def test_stop_marks_processing():
    for timing in ("practice", "exam"):
        result = transition(state("recording", timing), "stop")
        assert (result.processing_status, result.end_attempt) == ("processing", True)


def test_next_in_practice_feedback():
    result = transition(state("feedback"), "next")
    assert (result.phase, result.advance, result.outcome) == ("question", True, None)


@pytest.mark.parametrize("timing", ["practice", "exam"])
def test_last_item_goes_to_summary(timing):
    last = state("recording", timing, is_last=True)
    result = transition(last, "dont_know")
    expected = ("feedback", "active") if timing == "practice" else ("summary", "finished")
    assert (result.phase, result.status) == expected
    assert not result.advance
    done = transition(state("feedback", is_last=True), "next")
    assert (done.phase, done.status, done.advance) == ("summary", "finished", False)


@pytest.mark.parametrize(
    "phase,action,timing",
    [
        ("preparation", "stop", "practice"),
        ("question", "start_answer", "practice"),
        ("question", "dont_know", "practice"),
        ("recording", "skip", "practice"),
        ("recording", "question_ready", "practice"),
        ("processing", "next", "practice"),
        ("feedback", "next", "exam"),
        ("feedback", "stop", "practice"),
        ("question", "resume", "practice"),
        ("summary", "pause", "practice"),
    ],
)
def test_invalid_transitions(phase, action, timing):
    with pytest.raises(TransitionError) as error:
        transition(state(phase, timing), action, start_reason="button")
    assert error.value.message == NOT_NOW


def test_start_answer_without_reason_invalid():
    with pytest.raises(TransitionError):
        transition(state("preparation"), "start_answer")


@pytest.mark.parametrize("phase", ["question", "feedback", "processing"])
def test_pause_between_questions_keeps_attempt(phase):
    result = transition(state(phase), "pause")
    assert (result.status, result.phase, result.paused_from_phase) == ("paused", phase, phase)
    assert result.outcome is None
    assert not result.end_attempt


@pytest.mark.parametrize("phase", ["preparation", "recording"])
@pytest.mark.parametrize("timing", ["practice", "exam"])
def test_pause_mid_question_interrupts(phase, timing):
    result = transition(state(phase, timing), "pause")
    assert (result.status, result.paused_from_phase) == ("paused", phase)
    assert (result.outcome, result.end_attempt) == ("interrupted", True)
    assert result.processing_status is None


@pytest.mark.parametrize("phase", ["preparation", "recording"])
def test_resume_after_interruption_asks_new_attempt(phase):
    paused = state(phase, status="paused", paused_from_phase=phase)
    result = transition(paused, "resume")
    assert (result.status, result.phase, result.new_attempt) == ("active", "question", True)
    assert result.outcome is None


@pytest.mark.parametrize("phase", ["question", "feedback", "processing"])
def test_resume_returns_to_paused_phase(phase):
    result = transition(state(phase, status="paused", paused_from_phase=phase), "resume")
    assert (result.status, result.phase, result.new_attempt) == ("active", phase, False)
    assert result.paused_from_phase is None


def test_resume_from_processing_with_result_goes_to_feedback():
    paused = state("processing", status="paused", paused_from_phase="processing", result_ready=True)
    assert transition(paused, "resume").phase == "feedback"


@pytest.mark.parametrize("action", [a for a in ACTIONS if a not in ("resume", "abandon")])
def test_paused_allows_only_resume_and_abandon(action):
    paused = state("question", status="paused", paused_from_phase="question")
    with pytest.raises(TransitionError) as error:
        transition(paused, action, start_reason="button")
    assert error.value.message == ROUND_PAUSED
    assert transition(paused, "abandon").status == "abandoned"


@pytest.mark.parametrize("status", ["finished", "abandoned"])
@pytest.mark.parametrize("action", ACTIONS)
def test_finished_allows_nothing(status, action):
    with pytest.raises(TransitionError) as error:
        transition(state("summary", status=status), action, start_reason="button")
    assert error.value.message == ROUND_OVER


@pytest.mark.parametrize("phase", PHASES[:-1])
def test_abandon_from_any_phase(phase):
    result = transition(state(phase), "abandon")
    assert (result.status, result.phase) == ("abandoned", "summary")
    assert result.outcome == ("interrupted" if phase in ("preparation", "recording") else None)


def test_result_moves_practice_processing_to_feedback_only():
    assert settle(state("processing", result_ready=True)).phase == "feedback"
    assert settle(state("processing")) is None  # no result yet
    assert settle(state("question", result_ready=True)) is None
    assert settle(state("processing", status="paused", result_ready=True)) is None


# ---- follow-ups ----


def fu(phase: str, timing: str = "practice", **kwargs) -> MachineState:
    """A round with follow-ups on."""
    kwargs.setdefault("follow_ups_on", True)
    return state(phase, timing, **kwargs)


@pytest.mark.parametrize("timing", ["practice", "exam"])
def test_main_stop_with_follow_ups_goes_to_processing(timing):
    result = transition(fu("recording", timing), "stop")
    assert (result.phase, result.advance, result.outcome) == ("processing", False, "answered")


def test_exam_without_follow_ups_unchanged():
    result = transition(state("recording", "exam"), "stop")
    assert (result.phase, result.advance) == ("question", True)


@pytest.mark.parametrize("timing", ["practice", "exam"])
@pytest.mark.parametrize("status", ["generating", "failed"])
def test_settle_waits_for_follow_up(timing, status):
    assert settle(fu("processing", timing, result_ready=True, follow_up=status)) is None


@pytest.mark.parametrize("timing", ["practice", "exam"])
def test_settle_ready_follow_up_asks_it(timing):
    result = settle(fu("processing", timing, result_ready=True, follow_up="ready"))
    assert (result.status, result.phase, result.part, result.advance) == (
        "active",
        "question",
        "followup",
        False,
    )


@pytest.mark.parametrize("follow_up", [None, "omitted"])
def test_settle_practice_without_row_goes_to_feedback(follow_up):
    result = settle(fu("processing", result_ready=True, follow_up=follow_up))
    assert (result.phase, result.part, result.advance) == ("feedback", "main", False)


@pytest.mark.parametrize("follow_up", [None, "omitted"])
def test_settle_exam_without_row_goes_to_next_item(follow_up):
    result = settle(fu("processing", "exam", result_ready=True, follow_up=follow_up))
    assert (result.phase, result.part, result.advance) == ("question", "main", True)


def test_settle_exam_last_item_finishes():
    result = settle(fu("processing", "exam", result_ready=True, is_last=True))
    assert (result.status, result.phase, result.advance) == ("finished", "summary", False)


def test_settle_needs_the_result():
    assert settle(fu("processing", follow_up="ready")) is None


@pytest.mark.parametrize("follow_up", ["generating", "failed", None])
def test_no_follow_up_in_main_processing(follow_up):
    result = transition(fu("processing", result_ready=True, follow_up=follow_up), "no_follow_up")
    assert (result.phase, result.part, result.outcome) == ("feedback", "main", None)
    exam = transition(fu("processing", "exam", result_ready=True), "no_follow_up")
    assert (exam.phase, exam.advance) == ("question", True)


@pytest.mark.parametrize(
    "machine",
    [
        fu("question", result_ready=True),
        fu("feedback", result_ready=True),
        fu("recording"),
        fu("processing"),  # the main result has not arrived
        fu("processing", part="followup", result_ready=True),
    ],
)
def test_no_follow_up_refused_outside_processing(machine):
    with pytest.raises(TransitionError) as error:
        transition(machine, "no_follow_up")
    assert error.value.message == NOT_NOW


def test_follow_up_part_keeps_part_on_question_ready():
    result = transition(fu("question", part="followup"), "question_ready")
    assert (result.phase, result.part, result.start_attempt) == ("preparation", None, True)
    start = transition(fu("preparation", part="followup"), "start_answer", start_reason="auto")
    assert (start.phase, start.part) == ("recording", None)


def test_follow_up_practice_stop_goes_to_processing():
    result = transition(fu("recording", part="followup"), "stop")
    assert (result.phase, result.part, result.outcome) == ("processing", None, "answered")


@pytest.mark.parametrize(
    "phase,action,outcome",
    [
        ("question", "skip", "skipped"),
        ("preparation", "skip", "skipped"),
        ("preparation", "dont_know", "dont_know"),
        ("recording", "dont_know", "dont_know"),
        ("recording", "mic_failure", "technical_failure"),
    ],
)
def test_follow_up_other_outcomes_go_to_main_feedback(phase, action, outcome):
    result = transition(fu(phase, part="followup"), action)
    assert (result.phase, result.part, result.outcome) == ("feedback", "main", outcome)


@pytest.mark.parametrize("action", ["stop", "dont_know", "skip"])
def test_exam_follow_up_answer_goes_to_next_item(action):
    phase = "recording" if action != "skip" else "question"
    result = transition(fu(phase, "exam", part="followup"), action)
    assert (result.phase, result.part, result.advance) == ("question", "main", True)
    last = transition(fu(phase, "exam", part="followup", is_last=True), action)
    assert (last.status, last.phase, last.part) == ("finished", "summary", "main")


def test_settle_follow_up_result_goes_to_feedback():
    result = settle(fu("processing", part="followup", result_ready=True, follow_up="ready"))
    assert (result.phase, result.part, result.advance) == ("feedback", "main", False)


@pytest.mark.parametrize("phase", ["preparation", "recording"])
def test_pause_in_follow_up_interrupts_and_resume_restarts_it(phase):
    paused = transition(fu(phase, part="followup"), "pause")
    assert (paused.outcome, paused.part) == ("interrupted", None)
    resumed = transition(
        fu(phase, part="followup", status="paused", paused_from_phase=phase), "resume"
    )
    assert (resumed.phase, resumed.part, resumed.new_attempt) == ("question", None, True)


def paused_processing(timing: str = "practice", **kwargs) -> MachineState:
    return fu("processing", timing, status="paused", paused_from_phase="processing", **kwargs)


def test_resume_from_processing_rules():
    ready = transition(paused_processing(result_ready=True, follow_up="ready"), "resume")
    assert (ready.phase, ready.part, ready.new_attempt) == ("question", "followup", False)
    for waiting in ("generating", "failed"):
        held = transition(paused_processing(result_ready=True, follow_up=waiting), "resume")
        assert (held.status, held.phase, held.part) == ("active", "processing", None)
    for none in (None, "omitted"):
        moved = transition(paused_processing(result_ready=True, follow_up=none), "resume")
        assert (moved.phase, moved.part) == ("feedback", "main")
    exam = transition(paused_processing("exam", result_ready=True), "resume")
    assert (exam.phase, exam.advance) == ("question", True)
    pending = transition(paused_processing(follow_up="ready"), "resume")
    assert (pending.phase, pending.part) == ("processing", None)
    follow = transition(paused_processing(part="followup", result_ready=True), "resume")
    assert (follow.phase, follow.part) == ("feedback", "main")
