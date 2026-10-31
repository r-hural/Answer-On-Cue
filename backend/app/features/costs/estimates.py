"""Cost estimates before paid work: one call, a round, a library job.

Feature modules are imported inside the functions: the provider registry imports this module,
and the features import the registry.
"""

import io
import math
import wave

from app.features.costs.prices import price_for, usd
from app.features.providers.types import Usage

# Where no subject is at hand (one provider call), text is priced at three characters per token,
# the Polish rate; a subject's own rate comes from its language profile.
UNKNOWN_LANGUAGE_CHARS_PER_TOKEN = 3.0
# Typical output tokens per task; the fallback covers a task not listed here.
TYPICAL_OUTPUT_TOKENS: dict[str, int] = {
    "assessment.assess": 800,
    "follow_ups.generate": 400,
    "questions.generate": 2500,
    "questions.verify": 600,
    "topics.extract_section": 1500,
    "topics.consolidate": 1500,
}
TYPICAL_OUTPUT_FALLBACK = 2000
# A compressed recording (webm/ogg/mp4) of unknown length: about 32 kbit/s.
AUDIO_BYTES_PER_SECOND = 4000


def tokens_for(chars: int, chars_per_token: float = UNKNOWN_LANGUAGE_CHARS_PER_TOKEN) -> int:
    return math.ceil(chars / chars_per_token)


def typical_output(task: str) -> int:
    return TYPICAL_OUTPUT_TOKENS.get(task, TYPICAL_OUTPUT_FALLBACK)


def text_call_usd(
    provider: str,
    model: str,
    role: str,
    chars_in: int,
    tokens_out: int,
    *,
    chars_per_token: float = UNKNOWN_LANGUAGE_CHARS_PER_TOKEN,
) -> float:
    price, _ = price_for(provider, model, role)
    tokens_in = tokens_for(chars_in, chars_per_token)
    return usd(price, Usage(input_tokens=tokens_in, output_tokens=tokens_out))


def _subject_chars_per_token(session, subject_id: int | None) -> float:
    """The subject's language rate; the default when there is no such subject."""
    from fastapi import HTTPException

    from app.features.languages.service import profile_for_subject

    if subject_id is None:
        return UNKNOWN_LANGUAGE_CHARS_PER_TOKEN
    try:
        return profile_for_subject(session, subject_id).chars_per_token
    except HTTPException:
        return UNKNOWN_LANGUAGE_CHARS_PER_TOKEN


def audio_seconds(audio: bytes, mime: str) -> float:
    """A WAV's own duration; anything else from its size."""
    if mime in ("audio/wav", "audio/x-wav"):
        try:
            with wave.open(io.BytesIO(audio)) as wav:
                rate = wav.getframerate()
                if rate:
                    return wav.getnframes() / rate
        except (wave.Error, EOFError):
            pass
    return len(audio) / AUDIO_BYTES_PER_SECOND


def transcription_usd(provider: str, model: str, seconds: float) -> float:
    price, _ = price_for(provider, model, "transcription")
    return usd(price, Usage(audio_seconds=seconds))


# A read question: speaking speed, audio output tokens and the reader instructions sent along.
VOICE_CHARS_PER_SECOND = 12
AUDIO_OUTPUT_TOKENS_PER_SECOND = 20
READER_INSTRUCTION_CHARS = 250
PAID_VOICE_PROVIDERS = ("azure_speech", "openai")


def voice_usd(
    provider: str,
    model: str,
    characters: int,
    *,
    chars_per_token: float = UNKNOWN_LANGUAGE_CHARS_PER_TOKEN,
) -> float:
    """Characters for Azure; tokens for a token-priced voice (OpenAI Realtime); Piper is free."""
    price, _ = price_for(provider, model, "voice")
    seconds = characters / VOICE_CHARS_PER_SECOND
    return usd(
        price,
        Usage(
            characters=characters,
            input_tokens=tokens_for(characters + READER_INSTRUCTION_CHARS, chars_per_token),
            output_tokens=math.ceil(seconds * AUDIO_OUTPUT_TOKENS_PER_SECOND),
        ),
    )


def verify_usd(settings, characters: int) -> float:
    """The transcription that checks one reading of `characters`."""
    return transcription_usd(
        settings.transcription_provider,
        settings.transcription_model,
        characters / VOICE_CHARS_PER_SECOND,
    )


def _fits(amount: float, month) -> bool:
    return month.remaining_usd is None or amount <= month.remaining_usd + 1e-9


# ---- a round (budget assumptions) ----

# Typical per answer until enough answers are measured (about 100k in / 15k out for
# 10 main answers and 5 follow-ups, 25 recorded minutes).
ASSESS_INPUT_TOKENS = 4000
FOLLOW_UP_INPUT_TOKENS = 3000
FOLLOW_UP_SECONDS = 60  # FollowUpConfig.answer_seconds default
TYPICAL_FOLLOW_UP_SHARE = 0.5  # the low end: half the answers get a follow-up; the high end: all
VOICE_CHARS_PER_QUESTION = 200  # about 3 000 characters per round of 10 plus follow-ups
MEASURED_MIN_ANSWERS = 10
MEASURED_WINDOW = 50


def _measured(session, operation: str) -> list:
    from sqlalchemy import select

    from app.features.providers.models import UsageEventRow

    return list(
        session.execute(
            select(
                UsageEventRow.input_tokens,
                UsageEventRow.output_tokens,
                UsageEventRow.audio_seconds,
            )
            .where(UsageEventRow.operation == operation, UsageEventRow.status == "ok")
            .order_by(UsageEventRow.id.desc())
            .limit(MEASURED_WINDOW)
        ).all()
    )


def _answer_profile(session) -> tuple[str, int, int, float | None]:
    """(basis, assessment input, assessment output, audio seconds per answer or None)."""
    assessed = _measured(session, "attempt.assess")
    heard = _measured(session, "attempt.transcribe")  # always read: the same statements either way
    if len(assessed) < MEASURED_MIN_ANSWERS:
        return "typical", ASSESS_INPUT_TOKENS, typical_output("assessment.assess"), None
    seconds = sum(r.audio_seconds for r in heard) / len(heard) if heard else None
    return (
        "measured",
        round(sum(r.input_tokens for r in assessed) / len(assessed)),
        round(sum(r.output_tokens for r in assessed) / len(assessed)),
        seconds,
    )


def round_estimate(
    session,
    settings,
    *,
    subject_id: int,
    question_count: int,
    answer_seconds: int,
    follow_ups: bool,
    follow_up_seconds: int = FOLLOW_UP_SECONDS,
    voice_provider: str | None = None,
    now=None,
    tz=None,
):
    """A range for the round: low with half the answers followed up, high with all of them.
    Read only; a fixed number of statements whatever the usage history."""
    from app.features.costs.schemas import EstimatePart, RoundEstimate
    from app.features.costs.spending import month_state
    from app.features.languages.profiles import get_profile
    from app.features.library import service as library
    from app.features.providers.registry import resolve_role

    subject = library.get_subject(session, subject_id)
    chars_per_token = get_profile(subject.language).chars_per_token
    basis, assess_in, assess_out, measured_seconds = _answer_profile(session)
    if measured_seconds is None:
        main_seconds, follow_seconds = answer_seconds, follow_up_seconds
    else:
        main_seconds = min(measured_seconds, answer_seconds)
        follow_seconds = min(measured_seconds, follow_up_seconds)
    assess_provider, assess_model = resolve_role(settings, "assessment")
    gen_provider, gen_model = resolve_role(settings, "generation")
    assess_one = usd(
        price_for(assess_provider, assess_model, "assessment")[0],
        Usage(input_tokens=assess_in, output_tokens=assess_out),
    )
    follow_up_one = usd(
        price_for(gen_provider, gen_model, "generation")[0],
        Usage(
            input_tokens=FOLLOW_UP_INPUT_TOKENS,
            output_tokens=typical_output("follow_ups.generate"),
        ),
    )
    voice = voice_provider or settings.voice_provider

    def parts(share: float) -> tuple[float, list[EstimatePart]]:
        followed = question_count * share if follow_ups else 0
        seconds = question_count * main_seconds + followed * follow_seconds
        found = [
            EstimatePart(
                operation="attempt.transcribe",
                usd=transcription_usd(
                    settings.transcription_provider, settings.transcription_model, seconds
                ),
            ),
            EstimatePart(operation="attempt.assess", usd=(question_count + followed) * assess_one),
        ]
        if follow_ups:
            found.append(EstimatePart(operation="follow_up.generate", usd=followed * follow_up_one))
        if voice in PAID_VOICE_PROVIDERS:
            chars = round((question_count + followed) * VOICE_CHARS_PER_QUESTION)
            amount = voice_usd(
                voice, settings.openai_voice_model, chars, chars_per_token=chars_per_token
            )
            if voice == "openai":  # every reading is checked by a transcription
                amount += verify_usd(settings, chars)
            found.append(EstimatePart(operation="voice.synthesize", usd=amount))
        return seconds, found

    low_seconds, low_parts = parts(TYPICAL_FOLLOW_UP_SHARE)
    _, high_parts = parts(1.0)
    high = sum(p.usd for p in high_parts)
    month = month_state(session, settings, now=now, tz=tz)
    return RoundEstimate(
        low_usd=sum(p.usd for p in low_parts),
        high_usd=high,
        transcription_minutes=round(low_seconds / 60, 1),
        basis=basis,
        parts=high_parts,
        voice_provider=voice,
        month=month,
        fits=_fits(high, month),
    )


# ---- library jobs ----

SYSTEM_CHARS = 3000  # a job prompt's fixed instructions
CONSOLIDATE_CHARS_PER_SECTION = 1500  # the topic list one section adds to the consolidation
QUESTION_OUTPUT_TOKENS = 500  # one generated question with its rubric
QUESTION_CHARS = 1500  # one generated question with its rubric, sent back for verification
SECONDS_PER_STEP = {"topics.extract": 30, "questions.generate": 40}


def _passage_chars(session, ids: list[int]) -> int:
    from sqlalchemy import func, select

    from app.features.library.models import SourcePassage

    if not ids:
        return 0
    return session.scalar(
        select(func.coalesce(func.sum(func.length(SourcePassage.text)), 0)).where(
            SourcePassage.id.in_(ids), SourcePassage.excluded.is_(False)
        )
    )


def _topic_chars(session, topic_ids: list[int], version_id: int | None) -> dict[int, int]:
    """Each topic's usable passage characters in one statement, capped as topic_passages caps."""
    from sqlalchemy import func, select

    from app.features.library.models import SourcePassage
    from app.features.topics.models import TopicPassage
    from app.features.topics.service import SECTION_CHAR_LIMIT

    if not topic_ids:
        return {}
    query = (
        select(TopicPassage.topic_id, func.sum(func.length(SourcePassage.text)))
        .join(SourcePassage, SourcePassage.id == TopicPassage.passage_id)
        .where(TopicPassage.topic_id.in_(topic_ids), SourcePassage.excluded.is_(False))
        .group_by(TopicPassage.topic_id)
    )
    if version_id is not None:
        query = query.where(SourcePassage.document_version_id == version_id)
    return {
        topic_id: min(chars or 0, SECTION_CHAR_LIMIT) for topic_id, chars in session.execute(query)
    }


def _minutes(kind: str, steps: int) -> int:
    return -(-steps * SECONDS_PER_STEP.get(kind, 30) // 60) if steps else 0


def estimate_job(
    session,
    settings,
    kind: str,
    params: dict,
    *,
    now=None,
    tz=None,
    done=frozenset(),
    chars_per_token: float = UNKNOWN_LANGUAGE_CHARS_PER_TOKEN,
):
    """A job's cost from its kind and params: "sections" or "batches".

    `done` holds the step indexes already completed (a resume): only the rest is priced."""
    from app.features.costs.schemas import JobEstimate
    from app.features.costs.spending import month_state
    from app.features.providers.registry import resolve_role

    provider, model = resolve_role(settings, "generation")

    def price_call(*args) -> float:
        if chars_per_token == UNKNOWN_LANGUAGE_CHARS_PER_TOKEN:
            return text_call_usd(*args)  # the default: today's call, unchanged
        return text_call_usd(*args, chars_per_token=chars_per_token)

    questions = sections = steps = 0
    amount = 0.0
    if kind == "topics.extract":
        found = params.get("sections") or []
        # Steps 0..n-1 are the sections, step n the consolidation over all of them.
        left = [section for index, section in enumerate(found) if index not in done]
        consolidate = bool(found) and len(found) not in done
        sections = len(left)
        steps = sections + consolidate
        if sections:
            chars = _passage_chars(session, [pid for section in left for pid in section])
            amount += price_call(
                provider,
                model,
                "generation",
                chars + sections * SYSTEM_CHARS,
                sections * typical_output("topics.extract_section"),
            )
        if consolidate:
            amount += price_call(
                provider,
                model,
                "generation",
                SYSTEM_CHARS + len(found) * CONSOLIDATE_CHARS_PER_SECTION,
                typical_output("topics.consolidate"),
            )
    elif kind == "questions.generate":
        batches = [
            batch for index, batch in enumerate(params.get("batches") or []) if index not in done
        ]
        steps = len(batches)
        topic_chars = _topic_chars(
            session, sorted({b["topic_id"] for b in batches}), params.get("document_version_id")
        )
        for batch in batches:
            count = batch.get("count", 0)
            chars = topic_chars.get(batch["topic_id"], 0)
            questions += count
            amount += price_call(
                provider, model, "generation", chars + SYSTEM_CHARS, count * QUESTION_OUTPUT_TOKENS
            ) + price_call(
                provider,
                model,
                "generation",
                chars + SYSTEM_CHARS + count * QUESTION_CHARS,
                typical_output("questions.verify"),
            )
    month = month_state(session, settings, now=now, tz=tz)
    return JobEstimate(
        kind=kind,
        usd=amount,
        questions=questions,
        sections=sections,
        minutes=_minutes(kind, steps),
        provider=provider,
        basis="typical",
        month=month,
        fits=_fits(amount, month),
    )


def job_estimate(session, settings, job):
    """What is left of the job: the steps in state_json["steps"] are done and cost nothing."""
    from app.features.jobs.service import load_params, step_results

    return estimate_job(
        session,
        settings,
        job.kind,
        load_params(job),
        done=frozenset(step_results(job)),
        chars_per_token=_subject_chars_per_token(session, job.subject_id),
    )


def job_estimate_usd(session, settings, job) -> float:
    return job_estimate(session, settings, job).usd


# ---- before the library jobs start ----


def _generation_topic_ids(
    session,
    subject_id: int,
    topic_ids: list[int] | None,
    *,
    chapter_id: int | None = None,
    document_version_id: int | None = None,
) -> list[int]:
    """The topics questions.service.start_generation_job picks; [] where it would refuse."""
    from fastapi import HTTPException

    from app.features.questions.service import generation_topic_ids

    try:
        return generation_topic_ids(
            session,
            subject_id,
            topic_ids,
            chapter_id=chapter_id,
            document_version_id=document_version_id,
        )
    except HTTPException:
        return []


def _extraction_sections(session, version) -> list[list[int]]:
    """The sections topics.service.start_extraction fixes at start; [] where it would refuse."""
    from fastapi import HTTPException

    from app.features.topics.service import extraction_sections, refuse_replaced

    if version.status != "imported":
        return []
    try:
        refuse_replaced(session, version)  # an older version cannot be analysed
        return extraction_sections(session, version)
    except HTTPException:
        return []


def generation_estimate(session, settings, body):
    """The job the Generate button would start (questions.service batches), priced."""
    from app.features.questions.service import BATCH_SIZE

    topic_ids = _generation_topic_ids(
        session,
        body.subject_id,
        body.topic_ids,
        chapter_id=body.chapter_id,
        document_version_id=body.document_version_id,
    )
    batches = [
        {
            "topic_id": topic_id,
            "start": start,
            "count": min(BATCH_SIZE, body.per_topic - start),
            "document_version_id": body.document_version_id,
        }
        for topic_id in topic_ids
        for start in range(0, body.per_topic, BATCH_SIZE)
    ]
    params = {"batches": batches, "document_version_id": body.document_version_id}
    chars_per_token = _subject_chars_per_token(session, body.subject_id)
    return estimate_job(
        session, settings, "questions.generate", params, chars_per_token=chars_per_token
    )


def extraction_estimate(session, settings, document_version_id: int):
    from app.features.library import service as library
    from app.features.library.models import Document

    version = library.get_version(session, document_version_id)
    params = {
        "document_version_id": version.id,
        "sections": _extraction_sections(session, version),
    }
    document = session.get(Document, version.document_id)
    chars_per_token = _subject_chars_per_token(session, document.subject_id if document else None)
    return estimate_job(
        session, settings, "topics.extract", params, chars_per_token=chars_per_token
    )
