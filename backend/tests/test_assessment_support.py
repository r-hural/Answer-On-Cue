"""Shared builders for the assessment tests (no tests here)."""

from app.features.assessment.schemas import (
    REGISTER_KEYS,
    AiAssessment,
    PointJudgement,
    RegisterJudgement,
    RegisterSet,
)
from app.features.delivery.schemas import AnswerMetrics, Thresholds
from app.features.questions.schemas import (
    EssentialPoint,
    PassageRef,
    QuestionWithRubric,
    Rubric,
)


def rubric(weights=(0.5, 0.3, 0.2), **over) -> Rubric:
    base = dict(
        essential_points=[
            EssentialPoint(id=f"p{n}", text=f"Punkt {n}", weight=w, passage_ids=[1])
            for n, w in enumerate(weights, start=1)
        ],
        misconceptions=["Rośliny pobierają tlen do fotosyntezy."],
        major_error_rules=["Pomylenie substratów z produktami fotosyntezy."],
        model_answer="Fotosynteza to proces wytwarzania glukozy z dwutlenku węgla i wody.",
    )
    base.update(over)
    return Rubric(**base)


def question(rub: Rubric | None = None, passages: list[PassageRef] | None = None):
    return QuestionWithRubric(
        version_id=11,
        family_id=7,
        version=1,
        text="Czym jest fotosynteza?",
        style="short",
        difficulty="recall",
        primary_topic_id=None,
        objective_id=None,
        status="approved",
        suggested_prep_s=10,
        suggested_answer_s=60,
        source_policy="materials",
        rubric=rub or rubric(),
        passages=passages
        if passages is not None
        else [PassageRef(id=1, locator="s. 1", heading=None, text="Fotosynteza zachodzi…")],
    )


def register_set() -> RegisterSet:
    return RegisterSet(**{key: RegisterJudgement(summary="Poprawnie.") for key in REGISTER_KEYS})


def point(pid: str, status: str = "covered", evidence: str | None = "cytat") -> PointJudgement:
    if status == "missing":
        evidence = None
    return PointJudgement(point_id=pid, status=status, evidence=evidence, note="Uwaga.")


def ai(statuses=("covered", "partial", "missing"), **over) -> AiAssessment:
    base = dict(
        correctness=70,
        correctness_evidence=["cytat"],
        points=[point(f"p{n}", s) for n, s in enumerate(statuses, start=1)],
        language=90,
        language_register=register_set(),
        better_version="Fotosynteza jest procesem…",
        improvements=["Wymień produkty procesu."],
        fluency=None,
        fluency_coverage="none",
        fluency_confidence="low",
        fluency_feedback="Brak danych o wypowiedzi.",
    )
    base.update(over)
    return AiAssessment(**base)


def metrics(**over) -> AnswerMetrics:
    base = dict(
        preparation_used_ms=4000,
        speech_start_latency_ms=800,
        total_response_latency_ms=4800,
        answer_duration_ms=20000,
        first_to_last_speech_ms=18000,
        speech_ms=15000,
        leading_silence_ms=800,
        trailing_silence_ms=1200,
        internal_silence_ms=3000,
        silence_ratio=0.17,
        longest_internal_silence_ms=2100,
        long_pause_count=1,
        very_long_pause_count=0,
        long_pauses_per_min=3.3,
        words=45,
        words_per_minute=150.0,
        filler_count=2,
        filler_per_min=6.7,
        filler_examples=["yyy"],
        pauses=[],
        flags=[],
        unavailable={},
        interrupted=False,
        detector="test-vad",
        thresholds=Thresholds(),
    )
    base.update(over)
    return AnswerMetrics(**base)


def sequence(*values):
    """A FakeTextGenerator response that returns the given values in order, one per call."""
    remaining = list(values)

    def respond(messages):
        return remaining.pop(0)

    return respond
