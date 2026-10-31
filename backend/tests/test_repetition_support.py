"""Builders for the repetition tests and the e2e seed (no tests here)."""

from test_adaptive_support import World, build_world
from test_attempts_support import (
    ASSESS_TASK,
    FOLLOW_UP_TEXT,
    FakeProviders,
    act,
    ai_for,
    answer_current,
    override_providers,
    post_answer,
    start_round,
)

from app.features.assessment.schemas import AiAssessment
from app.features.providers.fakes import FakeTextGenerator

DEFAULT_FACTUAL = 88.75  # ai_for(): correctness 70, both points covered
CREDITS = {0: ("missing", "missing"), 25: ("partial", "missing"), 50: ("partial", "partial")}
CREDITS |= {75: ("covered", "partial"), 100: ("covered", "covered")}


def ai_with_factual(factual: float) -> AiAssessment:
    """An assessment whose factual score is `factual` under the default weights (30, 50):
    factual = (0.3 · correctness + 0.5 · completeness) / 0.8; total = 0.8 · factual + 18."""
    for completeness, statuses in CREDITS.items():
        correctness = (0.8 * factual - 0.5 * completeness) / 0.3
        if 0 <= correctness <= 100 and abs(correctness - round(correctness)) < 1e-9:
            return ai_for(correctness=round(correctness), statuses=list(statuses))
    raise ValueError(f"no integer correctness gives factual {factual}")


class Scripted:
    """The fake assessor: main answers take the next queued factual score (else the default),
    follow-up answers always the default."""

    def __init__(self) -> None:
        self.queue: list[float] = []

    def __call__(self, messages) -> AiAssessment:
        if FOLLOW_UP_TEXT in messages[0].content or not self.queue:
            return ai_with_factual(DEFAULT_FACTUAL)
        return ai_with_factual(self.queue.pop(0))


def use_scripted(client) -> Scripted:
    scripted = Scripted()
    fake = FakeProviders(assessor=FakeTextGenerator({ASSESS_TASK: scripted}))
    override_providers(client.app, fake)
    scripted.fake = fake
    client.scripted = scripted  # answer_ready queues its factual score here
    return scripted


def repeat_world(session, name: str = "Historia") -> World:
    """A subject with three topics, one objective and one `short` question each."""
    return build_world(session, name=name, per_objective=(1, 1, 1))


def start_practice(client, world: World, count: int = 2, **config) -> int:
    config.setdefault("immediate_repeat", True)
    config.setdefault("style", "short")
    return start_round(client, {"subject": world.subject}, "practice", count, **config)["id"]


def answer_ready(client, round_id: int, *, factual: float = DEFAULT_FACTUAL) -> int:
    """Answers the current attempt through the fakes with the given factual score."""
    scripted = getattr(client, "scripted", None)
    if scripted is not None:
        scripted.queue.append(factual)
    attempt_id = answer_current(client, round_id)
    post_answer(client, attempt_id)
    return attempt_id


def answer_again(client, round_id: int, attempt_id: int) -> dict:
    return act(client, round_id, "answer_again", "feedback", attempt_id=attempt_id)


def state(client, round_id: int) -> dict:
    response = client.get(f"/api/rounds/{round_id}")
    assert response.status_code == 200, response.text
    return response.json()


def get(client, path: str) -> dict | list:
    response = client.get(f"/api{path}")
    assert response.status_code == 200, response.text
    return response.json()
