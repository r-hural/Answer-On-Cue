"""The backend app with the attempt pipeline's providers replaced by fakes (no network, no keys).

The e2e web server runs `e2e_app:app` instead of `app.main:app`, so a recorded answer goes through
the real pipeline (upload, speech detection, metrics, assessment, reports) with a fixed transcript
and a fixed AI assessment. Specs that stub /api/attempts/** are not affected.
Run with the backend as the working directory; nothing in backend/ is edited.
"""

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

from test_attempts_support import (  # noqa: E402
    ASSESS_TASK,
    FOLLOW_UP_TASK,
    FakeProviders,
    ai_for,
    follow_up_for,
)
from test_topics_support import passage_ids_in  # noqa: E402

from app.features.assessment.schemas import (  # noqa: E402
    REGISTER_KEYS,
    PointJudgement,
    RegisterJudgement,
    RegisterSet,
)
from app.features.attempts.providers import get_pipeline_providers  # noqa: E402
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber  # noqa: E402
from app.features.providers.types import ProviderError  # noqa: E402
from app.main import app as backend_app  # noqa: E402

TRANSCRIPT = "Ostracyzm to było głosowanie na skorupach, eee, wygnanie na dziesięć lat."
ENGLISH_TRANSCRIPT = "Ostracism was a vote on pottery shards, um, and the exile lasted ten years."
FOLLOW_UP_TEXT = "Wspomniałeś o głosowaniu na skorupach. Wyjaśnij, kogo w ten sposób wygnano i dlaczego."


def follow_up_or_fail(messages):
    """A follow-up on the learner's words; questions about the eklezja fail (the failure spec)."""
    if any("eklezja" in m.content for m in messages):
        raise ProviderError("server", "anthropic", "Fake provider failure.", retryable=True)
    return follow_up_for(
        passage_ids_in(messages),
        target="unsupported_claim",
        anchor="głosowanie na skorupach",
        text=FOLLOW_UP_TEXT,
    )


class ByLanguageTranscriber(FakeTranscriber):
    """The Polish transcript, or the English one for a round in English. Specs run in parallel, so
    the shared fake is never changed: an English result is a copy."""

    def transcribe(self, audio, mime, glossary, *, operation_id, language):
        result = super().transcribe(
            audio, mime, glossary, operation_id=operation_id, language=language
        )
        if language == "en":
            return result.model_copy(update={"text": ENGLISH_TRANSCRIPT})
        return result


# The seeded questions use the rubric points p1 and p2, which ai_for() judges as covered.
AI = ai_for()
ENGLISH_AI = ai_for(
    correctness_evidence=["a vote on pottery shards"],
    points=[
        PointJudgement(
            point_id=p, status="covered", evidence="a vote on pottery shards", note="Covered."
        )
        for p in ("p1", "p2")
    ],
    language_register=RegisterSet(
        **{key: RegisterJudgement(summary="Correct.") for key in REGISTER_KEYS}
    ),
    better_version=(
        "Ostracism was a vote, written on pottery shards, that sent a citizen into exile "
        "for ten years."
    ),
    improvements=["Name the institution that held the vote."],
    fluency_feedback="No delivery data.",
)
FOLLOW_UP_ASSESS_SECONDS = 4


def assess(messages):
    """A follow-up answer is assessed a little later, so the exam summary opens while it is
    still being processed (and must keep reading until it is in)."""
    if any(ENGLISH_TRANSCRIPT in m.content for m in messages):
        return ENGLISH_AI
    if FOLLOW_UP_TEXT in messages[0].content:
        time.sleep(FOLLOW_UP_ASSESS_SECONDS)
    return AI


PROVIDERS = FakeProviders(
    transcriber=ByLanguageTranscriber(TRANSCRIPT),
    assessor=FakeTextGenerator({ASSESS_TASK: assess}),
    follow_up=FakeTextGenerator({FOLLOW_UP_TASK: follow_up_or_fail}),
)
backend_app.dependency_overrides[get_pipeline_providers] = lambda: PROVIDERS

app = backend_app
