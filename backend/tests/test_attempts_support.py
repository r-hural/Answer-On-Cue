"""Builders for the attempt pipeline tests (no tests here)."""

from pathlib import Path

from test_assessment_support import point, register_set
from test_topics_support import passage_ids_in

from app.features.assessment.schemas import AiAssessment
from app.features.attempts.providers import get_pipeline_providers
from app.features.follow_ups.prompts import GeneratedFollowUp
from app.features.follow_ups.service import FOLLOW_UP_TASK
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.questions.schemas import EssentialPoint, Rubric

AUDIO = Path(__file__).resolve().parent / "fixtures" / "audio"
ASSESS_TASK = "assessment.assess"
POINT_IDS = ("p1", "p2")  # the rubric of test_topics_support.make_question
FOLLOW_UP_TEXT = "Wyjaśnij, dlaczego ta procedura miała chronić demokrację przed tyranią."


class FakeProviders:
    """The pipeline seam with fakes; counts how often each adapter was built."""

    def __init__(self, transcriber=None, assessor=None, follow_up=None) -> None:
        self.fake_transcriber = transcriber or FakeTranscriber()
        self.fake_assessor = assessor or FakeTextGenerator({ASSESS_TASK: ai_for()})
        self.fake_follow_up = follow_up or FakeTextGenerator(
            {FOLLOW_UP_TASK: lambda messages: follow_up_for(passage_ids_in(messages))}
        )
        self.transcriber_builds = 0
        self.assessor_builds = 0
        self.follow_up_builds = 0

    def transcriber(self):
        self.transcriber_builds += 1
        return self.fake_transcriber

    def assessor(self):
        self.assessor_builds += 1
        return self.fake_assessor

    def follow_up_generator(self):
        self.follow_up_builds += 1
        return self.fake_follow_up


def override_providers(app, fake) -> None:
    app.dependency_overrides[get_pipeline_providers] = lambda: fake


def ai_for(question=None, statuses=None, **over) -> AiAssessment:
    """A valid AI assessment for the question's rubric points (p1, p2 without a question)."""
    ids = [p.id for p in question.rubric.essential_points] if question is not None else POINT_IDS
    statuses = statuses or ["covered"] * len(ids)
    base = dict(
        correctness=70,
        correctness_evidence=["cytat"],
        points=[point(pid, s) for pid, s in zip(ids, statuses, strict=True)],
        language=90,
        language_register=register_set(),
        better_version="Demokracja ateńska opierała się na zgromadzeniu.",
        improvements=["Wymień organy władzy."],
        fluency=None,
        fluency_coverage="none",
        fluency_confidence="low",
        fluency_feedback="Brak danych o wypowiedzi.",
    )
    base.update(over)
    return AiAssessment(**base)


def follow_up_for(
    passage_ids, target="justification", anchor=None, text=FOLLOW_UP_TEXT
) -> GeneratedFollowUp:
    """A valid follow-up citing the given passages, with the points p1 and p2 (as ai_for())."""
    cited = list(passage_ids)[:2]
    return GeneratedFollowUp(
        target=target,
        anchor_quote=anchor,
        text=text,
        rubric=Rubric(
            essential_points=[
                EssentialPoint(
                    id="p1", text="Ochrona przed tyranią", weight=0.5, passage_ids=cited
                ),
                EssentialPoint(
                    id="p2", text="Wygnanie na dziesięć lat", weight=0.5, passage_ids=cited
                ),
            ],
            model_answer="Ostracyzm pozwalał wygnać polityka, zanim zdobył władzę tyrana.",
        ),
    )


def wav(name: str) -> bytes:
    return (AUDIO / name).read_bytes()


def events_body(**over) -> dict:
    body = {
        "question_shown_at": 1200.0,
        "question_ready_at": 1200.0,
        "playback_ms": 0,
        "prep_end_at": 9400.0,
        "start_reason": "auto",
        "capture_start_at": 9400.0,
        "stopped_at": 21400.0,
        "pre_roll_ms": 1000,
        "interrupted": False,
        "listening_available": True,
    }
    body.update(over)
    return body


def act(client, round_id: int, action: str, from_phase: str, **extra) -> dict:
    response = client.post(
        f"/api/rounds/{round_id}/actions",
        json={"action": action, "from_phase": from_phase, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def start_round(client, world, timing: str = "practice", count: int = 1, **config) -> dict:
    config.setdefault("scope", {"kind": "subject"})
    response = client.post(
        "/api/rounds",
        json={
            "subject_id": world["subject"].id,
            "config": {"feedback_timing": timing, "question_count": count, **config},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def answer_current(client, round_id: int) -> int:
    """question → preparation → recording (auto) → stop; returns the answered attempt id."""
    act(client, round_id, "question_ready", "question")
    state = act(client, round_id, "start_answer", "preparation", start_reason="auto")
    attempt_id = state["current_attempt"]["id"]
    act(client, round_id, "stop", "recording")
    return attempt_id


def answered_attempt(client, world, timing: str = "practice", count: int = 1) -> tuple[int, int]:
    round_id = start_round(client, world, timing, count)["id"]
    return round_id, answer_current(client, round_id)


def post_events(client, attempt_id: int, **over):
    return client.post(f"/api/attempts/{attempt_id}/events", json=events_body(**over))


def post_audio(client, attempt_id: int, audio: str = "speech_mid.wav", data: bytes | None = None):
    data = wav(audio) if data is None else data
    return client.post(
        f"/api/attempts/{attempt_id}/audio", files={"file": ("a.wav", data, "audio/wav")}
    )


def post_answer(client, attempt_id: int, audio: str = "speech_mid.wav", **events):
    """The client's order after stop: events, then audio. Returns both responses."""
    return post_events(client, attempt_id, **events), post_audio(client, attempt_id, audio)


def result(client, attempt_id: int) -> dict:
    response = client.get(f"/api/attempts/{attempt_id}/result")
    assert response.status_code == 200, response.text
    return response.json()
