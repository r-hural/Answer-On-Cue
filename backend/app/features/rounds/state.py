"""The round's phase table as a pure function; service.py applies the result."""

from dataclasses import dataclass, replace

ROUND_PAUSED = "The round is paused."
ROUND_OVER = "The round is over."
NOT_NOW = "This action is not available right now."

ANSWERING = ("preparation", "recording")


class TransitionError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class MachineState:
    status: str  # active / paused / finished / abandoned
    phase: str
    paused_from_phase: str | None
    feedback_timing: str  # practice / exam
    is_last: bool  # the current item is the round's last
    result_ready: bool = False  # the current attempt's processing result has arrived
    part: str = "main"  # main / followup: whose question the phase belongs to
    follow_ups_on: bool = False
    follow_up: str | None = None  # effective status of the current item's follow-up, None = no row
    repeat_allowed: bool = False  # "Answer again" may start now (offer rules)
    repeating: bool = False  # the current attempt is an immediate repeat of the main question
    cut_off: bool = False  # the current main attempt's run was cut off (processing, orphaned)


@dataclass(frozen=True)
class Transition:
    status: str
    phase: str
    paused_from_phase: str | None = None
    outcome: str | None = None  # new outcome of the current attempt
    processing_status: str | None = None
    start_reason: str | None = None
    start_attempt: bool = False  # set the current attempt's started_at
    end_attempt: bool = False  # set the current attempt's ended_at
    advance: bool = False  # move to the next item (a new pending attempt)
    new_attempt: bool = False  # restart the current item with a linked attempt
    part: str | None = None  # None keeps the current part
    attempt_kind: str | None = None  # kind of the new attempt; None keeps the current kind


WAITING = ("generating", "failed")  # follow-up states that keep a round in processing


def _next_item(state: MachineState, **attempt) -> Transition:
    if state.is_last:
        return Transition(status="finished", phase="summary", part="main", **attempt)
    return Transition(status="active", phase="question", advance=True, part="main", **attempt)


def _after_answer(state: MachineState, **attempt) -> Transition:
    """Practice shows feedback (on the main answer, also after its follow-up); exam moves on."""
    if state.feedback_timing == "practice":
        return Transition(status="active", phase="feedback", part="main", **attempt)
    return _next_item(state, **attempt)


def _after_main_result(state: MachineState) -> Transition | None:
    """Once the main result is in: wait for, ask or go past the follow-up."""
    if state.follow_up in WAITING:
        return None
    if state.follow_up == "ready":
        return Transition(status="active", phase="question", part="followup")
    return _after_answer(state)


def transition(state: MachineState, action: str, start_reason: str | None = None) -> Transition:
    if state.status in ("finished", "abandoned"):
        raise TransitionError(ROUND_OVER)
    phase = state.phase
    if action == "abandon":
        attempt = {}
        if state.status == "active" and phase in ANSWERING:
            attempt = {"outcome": "interrupted", "end_attempt": True}
        return Transition(status="abandoned", phase="summary", **attempt)
    if state.status == "paused":
        if action != "resume":
            raise TransitionError(ROUND_PAUSED)
        resumed = state.paused_from_phase or phase
        if resumed in ANSWERING:
            # In the follow-up part the new attempt restarts the follow-up, never the main question.
            return Transition(status="active", phase="question", new_attempt=True)
        if resumed == "processing":
            moved = settle(replace(state, status="active", phase=resumed))
            if moved is not None:
                return moved
        return Transition(status="active", phase=resumed)

    if action == "pause" and phase != "summary":
        if phase in ANSWERING:
            return Transition(
                status="paused",
                phase=phase,
                paused_from_phase=phase,
                outcome="interrupted",
                end_attempt=True,
            )
        return Transition(status="paused", phase=phase, paused_from_phase=phase)
    if action == "question_ready" and phase == "question":
        return Transition(status="active", phase="preparation", start_attempt=True)
    if action == "skip" and phase in ("question", "preparation"):
        return _after_answer(state, outcome="skipped", end_attempt=True)
    if action == "start_answer" and phase == "preparation" and start_reason is not None:
        return Transition(status="active", phase="recording", start_reason=start_reason)
    if action == "dont_know" and phase in ANSWERING:
        return _after_answer(state, outcome="dont_know", end_attempt=True)
    if action == "mic_failure" and phase in ANSWERING:
        return _after_answer(state, outcome="technical_failure", end_attempt=True)
    if action == "stop" and phase == "recording":
        attempt = {"outcome": "answered", "processing_status": "processing", "end_attempt": True}
        if state.feedback_timing == "practice":
            return Transition(status="active", phase="processing", **attempt)
        if state.part == "main" and state.follow_ups_on:
            # Exam waits for the main result and its follow-up; nothing is shown meanwhile.
            return Transition(status="active", phase="processing", **attempt)
        return _next_item(state, **attempt)
    if action == "next" and phase == "feedback" and state.feedback_timing == "practice":
        return _next_item(state)
    if (
        action == "answer_again"
        and phase == "feedback"
        and state.feedback_timing == "practice"
        and state.part == "main"
        and state.repeat_allowed
    ):
        # The same main question once more, as a linked attempt; the round stays on the item.
        return Transition(
            status="active", phase="question", new_attempt=True, attempt_kind="immediate"
        )
    if (
        action == "no_follow_up"
        and phase == "processing"
        and state.part == "main"
        and (state.result_ready or state.cut_off)
    ):
        return _after_answer(state)
    raise TransitionError(NOT_NOW)


def settle(state: MachineState) -> Transition | None:
    """The internal move once the current attempt's result or its follow-up arrives (no event)."""
    if state.status != "active" or state.phase != "processing" or not state.result_ready:
        return None
    if state.part == "followup" or state.repeating:
        # A repeat never gets a follow-up: its result goes straight to its feedback.
        return _after_answer(state)
    return _after_main_result(state)
