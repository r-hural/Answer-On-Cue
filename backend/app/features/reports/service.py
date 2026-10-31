"""Read models composed from rounds, attempt results, rubrics and the aggregate functions."""

from collections.abc import Callable
from datetime import UTC, datetime
from statistics import median

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.assessment.aggregate import (
    ItemForSummary,
    follow_up_summary,
    round_knowledge_summary,
)
from app.features.assessment.schemas import BAND_LABELS, AssessmentResult
from app.features.attempts import service as attempts
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.schemas import AttemptResultOut
from app.features.costs.usage_report import round_costs
from app.features.delivery.aggregate import SUCCESS_FACTUAL_MIN, aggregate_round
from app.features.delivery.schemas import AttemptForAggregate
from app.features.follow_ups import service as follow_ups
from app.features.follow_ups.models import FollowUp
from app.features.library.models import Chapter, Document, DocumentVersion, SourcePassage, Subject
from app.features.questions import service as questions
from app.features.questions import validation
from app.features.questions.models import QuestionVersion
from app.features.questions.schemas import QuestionWithRubric
from app.features.reports.models import TranscriptReport
from app.features.reports.schemas import (
    AnswerBrief,
    AttemptReport,
    ConflictOut,
    ExtensionSeries,
    FollowUpBrief,
    FollowUpSeries,
    HistoryRow,
    ItemRow,
    PointOut,
    RepeatSeries,
    RetryComparison,
    RetrySeries,
    RoundReport,
    SeriesRow,
    SourceOut,
    TranscriptReportIn,
    TranscriptReportOut,
)
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt, Round, RoundEvent, RoundItem
from app.features.rounds.schemas import RoundConfig

NO_TRANSCRIPT = "There is no transcript to report on."
ROUND_NOT_FOUND = "Round not found."
HISTORY_LIMIT = 50
EXCERPT_CHARS = 240
WITHHELD_STATUSES = ("active", "paused")
ASSESSED_STATUSES = ("assessed", "no_answer")
GOOD_BANDS = ("good", "very_good")
LOW_BANDS = ("weak", "needs_improvement")
NO_ANSWER_OUTCOMES = ("dont_know", "skipped")
NOTE_POINTS = 2


def _utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1].rstrip() + "…"


def _transcript_report(session: Session, attempt_id: int) -> TranscriptReportOut | None:
    row = session.scalar(select(TranscriptReport).where(TranscriptReport.attempt_id == attempt_id))
    if row is None:
        return None
    return TranscriptReportOut(
        attempt_id=attempt_id, corrected_text=row.corrected_text, created_at=row.created_at
    )


def _points(question: QuestionWithRubric, assessment: AssessmentResult | None) -> list[PointOut]:
    if assessment is None or assessment.ai is None:
        return []
    judged = {p.point_id: p for p in assessment.ai.points}
    points = []
    for point in question.rubric.essential_points:
        judgement = judged.get(point.id)
        if judgement is not None:
            points.append(
                PointOut(
                    id=point.id,
                    text=point.text,
                    status=judgement.status,
                    evidence=judgement.evidence,
                    note=judgement.note,
                    basis=point.basis,
                    disputed=judgement.disputed,
                )
            )
    return points


def _sources(session: Session, question: QuestionWithRubric) -> list[SourceOut]:
    ids = list(dict.fromkeys(i for p in question.rubric.essential_points for i in p.passage_ids))
    if not ids:
        return []
    found = {
        passage.id: (passage, title)
        for passage, title in session.execute(
            select(SourcePassage, Document.title)
            .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(SourcePassage.id.in_(ids))
        )
    }
    return [
        SourceOut(
            passage_id=pid,
            document_title=found[pid][1],
            locator=found[pid][0].locator,
            heading=found[pid][0].heading,
            excerpt=_excerpt(found[pid][0].text),
        )
        for pid in ids
        if pid in found
    ]


def _passage_sources(session: Session, ids: list[int]) -> list[SourceOut]:
    """SourceOut rows for the given passage ids, in that order (missing ones skipped)."""
    if not ids:
        return []
    found = {
        passage.id: (passage, title)
        for passage, title in session.execute(
            select(SourcePassage, Document.title)
            .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(SourcePassage.id.in_(ids))
        )
    }
    return [
        SourceOut(
            passage_id=pid,
            document_title=found[pid][1],
            locator=found[pid][0].locator,
            heading=found[pid][0].heading,
            excerpt=_excerpt(found[pid][0].text),
        )
        for pid in ids
        if pid in found
    ]


def _conflicts(
    session: Session, question: QuestionWithRubric, assessment: AssessmentResult | None
) -> list[ConflictOut]:
    if assessment is None or assessment.ai is None or not assessment.ai.source_conflicts:
        return []
    texts = {p.id: p.text for p in question.rubric.essential_points}
    conflicts = assessment.ai.source_conflicts
    ids = list(dict.fromkeys(i for c in conflicts for i in c.passage_ids))
    by_id = {s.passage_id: s for s in _passage_sources(session, ids)}
    return [
        ConflictOut(
            point_id=c.point_id,
            point_text=texts.get(c.point_id) if c.point_id is not None else None,
            note=c.note,
            sources=[by_id[i] for i in c.passage_ids if i in by_id],
        )
        for c in conflicts
    ]


def attempt_report(session: Session, attempt_id: int) -> AttemptReport:
    result = attempts.get_result(session, attempt_id)  # 404, 409 and the audio-wait mark
    ctx = rounds.get_attempt_context(session, attempt_id)
    item = session.get(RoundItem, ctx.item_id)
    total = len(rounds.get_state(session, ctx.round_id).items)
    round_row = session.get(Round, ctx.round_id)
    subject = session.get(Subject, ctx.subject_id)
    question = questions.get_question(session, ctx.question_version_id, with_rubric=True)

    assessment = result.assessment
    shown = (
        assessment is not None and assessment.status in ASSESSED_STATUSES and not result.withheld
    )
    display = assessment.model_answer_display if shown else None
    parent_attempt_id = brief = None
    if item.role == "followup":
        row = session.scalar(select(FollowUp).where(FollowUp.item_id == item.id))
        parent_attempt_id = row.main_attempt_id if row is not None else None
    elif ctx.config.follow_ups.enabled and not result.withheld and ctx.attempt_kind == "initial":
        brief = _brief(session, item, round_row, result)  # repeats get no follow-up
    original, again_attempt_id = _links(session, item, ctx.attempt_kind, attempt_id)
    return AttemptReport(
        attempt_id=attempt_id,
        round_id=ctx.round_id,
        subject_id=ctx.subject_id,
        subject_name=subject.name,
        ordinal=item.ordinal + 1,
        total=total,
        feedback_timing=ctx.config.feedback_timing,
        round_status=round_row.status,
        question_text=question.text,
        result=result,
        points=_points(question, assessment) if shown else [],
        key_points=[p.text for p in question.rubric.essential_points] if display == "auto" else [],
        model_answer=question.rubric.model_answer if display is not None else None,
        expanded_explanation=question.rubric.expanded_explanation if display is not None else None,
        model_answer_display=display,
        sources=_sources(session, question) if shown else [],
        transcript_report=_transcript_report(session, attempt_id) if not result.withheld else None,
        role="followup" if item.role == "followup" else "main",
        parent_attempt_id=parent_attempt_id,
        follow_up=brief,
        round_type=round_row.round_type,
        source_policy=question.source_policy,
        question_basis=validation.question_basis(question.rubric, question.source_policy),
        beyond_material=question.rubric.beyond_material if display is not None else [],
        source_conflicts=_conflicts(session, question, assessment) if shown else [],
        attempt_kind=ctx.attempt_kind,
        original=original,
        again_attempt_id=again_attempt_id,
    )


def _links(
    session: Session, item: RoundItem, kind: str, attempt_id: int
) -> tuple[AnswerBrief | None, int | None]:
    """(a repeat's original answer, an original's repeat id) for the feedback comparison."""
    if item.role != "main":
        return None, None
    attempt = session.get(Attempt, attempt_id)
    if kind == "immediate":
        if attempt.parent_attempt_id is None:
            return None, None
        parent = session.get(Attempt, attempt.parent_attempt_id)
        return _answer_brief(parent, attempts.get_result(session, parent.id)), None
    if kind == "retry":
        # the original round's answer to the same question (its final, never its repeat)
        if item.parent_item_id is None:
            return None, None
        tried = list(
            session.scalars(
                select(Attempt)
                .where(Attempt.round_item_id == item.parent_item_id)
                .order_by(Attempt.id)
            )
        )
        final = _final_attempt(tried)
        if final is None or final.outcome in ("pending", "interrupted"):
            return None, None
        return _answer_brief(final, attempts.get_result(session, final.id)), None
    tried = list(
        session.scalars(
            select(Attempt).where(Attempt.round_item_id == item.id).order_by(Attempt.id)
        )
    )
    final = _final_attempt(tried)
    repeat = _again_attempt(tried)
    if final is None or final.id != attempt_id or repeat is None:
        return None, None
    return None, repeat.id


def _answer_brief(attempt: Attempt, result: AttemptResultOut) -> AnswerBrief:
    assessment, metrics = result.assessment, result.metrics
    scores = assessment.scores if assessment is not None else None
    return AnswerBrief(
        attempt_id=attempt.id,
        outcome=attempt.outcome,
        status=result.status,
        band=assessment.band if assessment is not None else None,
        factual=scores.factual if scores is not None else None,
        total=scores.total if scores is not None else None,
        preparation_used_ms=metrics.preparation_used_ms if metrics is not None else None,
        long_pause_count=metrics.long_pause_count if metrics is not None else None,
        fluency=assessment.ai.fluency if assessment is not None and assessment.ai else None,
    )


def _row_brief(row: ItemRow, result: AttemptResultOut | None) -> AnswerBrief:
    """An item row's answer as a brief (not asked: no attempt and no scores)."""
    metrics = result.metrics if result is not None else None
    return AnswerBrief(
        attempt_id=row.attempt_id,
        outcome=row.outcome,
        status=row.status,
        band=row.band,
        factual=row.factual,
        total=row.total,
        preparation_used_ms=row.preparation_used_ms,
        long_pause_count=metrics.long_pause_count if metrics is not None else None,
        fluency=row.fluency,
    )


def _below_good(brief: AnswerBrief) -> bool:
    return brief.band in LOW_BANDS or brief.outcome in NO_ANSWER_OUTCOMES


def _again_attempt(tried: list[Attempt]) -> Attempt | None:
    """The item's answered repeat (Answer again): not pending, interrupted or skipped."""
    repeats = [
        a
        for a in tried
        if a.attempt_kind == "immediate" and a.outcome not in ("pending", "interrupted", "skipped")
    ]
    return repeats[-1] if repeats else None


def _change(original: AnswerBrief, again: AnswerBrief) -> float | None:
    if original.factual is None or again.factual is None:
        return None
    return again.factual - original.factual


def _not_asked(reason: str, message: str | None = None) -> FollowUpBrief:
    return FollowUpBrief(
        asked=False,
        reason=reason,
        failure_message=message,
        question_text=None,
        attempt_id=None,
        outcome=None,
        status=None,
        band=None,
        factual=None,
        note=None,
    )


def _asked(session: Session, item: RoundItem, final: Attempt) -> FollowUpBrief:
    result = attempts.get_result(session, final.id)
    assessment = result.assessment
    notes = (
        [p.note for p in assessment.ai.points[:NOTE_POINTS]] if assessment and assessment.ai else []
    )
    return FollowUpBrief(
        asked=True,
        reason=None,
        failure_message=None,
        question_text=session.get(QuestionVersion, item.question_version_id).text,
        attempt_id=final.id,
        outcome=final.outcome,
        status=result.status,
        band=assessment.band if assessment else None,
        factual=assessment.scores.factual if assessment and assessment.scores else None,
        note=" ".join(notes) or None,
    )


def _brief(
    session: Session, main_item: RoundItem, round_row: Round, main: AttemptResultOut
) -> FollowUpBrief | None:
    """Asked (with its own scores) or why not; None while that is still open."""
    row = session.scalar(select(FollowUp).where(FollowUp.main_item_id == main_item.id))
    if row is not None and row.item_id is not None:
        item = session.get(RoundItem, row.item_id)
        tried = list(
            session.scalars(
                select(Attempt).where(Attempt.round_item_id == item.id).order_by(Attempt.id)
            )
        )
        final = _final_attempt(tried)
        if final is None or final.outcome in ("pending", "interrupted"):
            return _not_asked("not_reached")
        return _asked(session, item, final)
    if main.status == "failed" or main.outcome in ("technical_failure", "given_up"):
        return _not_asked("main_unresolved")
    if main.status == "processing":
        return None
    if main.assessment is None or main.assessment.status != "assessed":
        return _not_asked("no_answer")
    if row is None:
        continued = session.scalar(
            select(RoundEvent.id).where(
                RoundEvent.attempt_id == main.attempt_id, RoundEvent.action == "no_follow_up"
            )
        )
        return _not_asked("continued" if continued else "not_reached")
    if row.status == "omitted":
        if row.failure_kind is not None:
            return _not_asked("generation_failed", row.failure_message)
        return _not_asked("continued")
    seen = follow_ups.effective(row)
    if seen.status == "failed":
        return _not_asked("generation_failed", seen.failure.message if seen.failure else None)
    if round_row.status not in WITHHELD_STATUSES:
        return _not_asked("not_reached")
    return None


def put_transcript_report(
    session: Session, attempt_id: int, body: TranscriptReportIn
) -> TranscriptReportOut:
    result = attempts.get_result(session, attempt_id)
    if result.status != "ready" or not result.transcript or result.withheld:
        raise HTTPException(409, NO_TRANSCRIPT)
    row = session.scalar(select(TranscriptReport).where(TranscriptReport.attempt_id == attempt_id))
    if row is None:
        session.add(TranscriptReport(attempt_id=attempt_id, corrected_text=body.corrected_text))
    else:
        row.corrected_text = body.corrected_text
    session.commit()
    return _transcript_report(session, attempt_id)


# ---- rounds ----


def _final_attempt(attempts_of_item: list[Attempt]) -> Attempt | None:
    """The item's latest attempt that was not interrupted (else its latest); an immediate
    repeat (Answer again) is never the final attempt."""
    attempts_of_item = [a for a in attempts_of_item if a.attempt_kind != "immediate"]
    kept = [a for a in attempts_of_item if a.outcome != "interrupted"]
    if kept:
        return kept[-1]
    return attempts_of_item[-1] if attempts_of_item else None


def _withheld(config: RoundConfig, status: str) -> bool:
    return config.feedback_timing == "exam" and status in WITHHELD_STATUSES


def _row(
    item: RoundItem,
    question_text: str,
    history: list[Attempt],
    read: Callable[[Attempt], AttemptResultOut],
) -> tuple:
    """(ItemRow, result or None, final attempt or None) for one main item."""
    final = _final_attempt(history)
    # restarts are judged within the kind: an interrupted repeat never marks the original
    restarted = any(a.outcome == "interrupted" and a.attempt_kind != "immediate" for a in history)
    blank = ItemRow(
        ordinal=item.ordinal + 1,
        item_id=item.id,
        attempt_id=None,
        question_text=question_text,
        outcome="pending",
        status="not_asked",
        band=None,
        factual=None,
        total=None,
        preparation_used_ms=None,
        speech_start_latency_ms=None,
        auto_start=False,
        fluency=None,
        restarted=restarted,
        failure=None,
    )
    if final is None or final.outcome in ("pending", "interrupted"):
        if final is not None:
            blank.outcome = final.outcome
        return blank, None, final
    result = read(final)
    assessment = result.assessment
    metrics = result.metrics
    row = blank.model_copy(
        update={
            "attempt_id": final.id,
            "outcome": final.outcome,
            "status": result.status,
            "failure": result.failure,
            "auto_start": final.start_reason == "auto",
        }
    )
    if assessment is not None:
        row.band = assessment.band
        if assessment.scores is not None:
            row.factual = assessment.scores.factual
            row.total = assessment.scores.total
        if assessment.ai is not None:
            row.fluency = assessment.ai.fluency
    if metrics is not None:
        row.preparation_used_ms = metrics.preparation_used_ms
        row.speech_start_latency_ms = metrics.speech_start_latency_ms
    return row, result, final


def _settled_reader(session: Session) -> Callable[[Attempt], AttemptResultOut]:
    """The full result, settling a missing-audio answer on the way (the report GETs)."""

    def read(final: Attempt) -> AttemptResultOut:
        result = attempts.get_result(session, final.id)
        session.refresh(final)  # the audio-wait rule may have changed it
        return result

    return read


def _read_only_reader(
    session: Session, row: Round, config: RoundConfig, items: list[RoundItem], finals: list[Attempt]
) -> Callable[[Attempt], AttemptResultOut]:
    """Results from rows loaded once per round; writes nothing (the history list)."""
    withheld = _withheld(config, row.status)
    processing = {
        p.attempt_id: p
        for p in session.scalars(
            select(AttemptProcessing).where(
                AttemptProcessing.attempt_id.in_([a.id for a in finals])
            )
        )
    }
    version_of = {item.id: item.question_version_id for item in items}

    def read(final: Attempt) -> AttemptResultOut:
        return attempts.compose_result(
            final,
            processing.get(final.id),
            question_version_id=version_of[final.round_item_id],
            config=config,
            withheld=withheld,
            overdue=attempts.audio_overdue(final),
        )

    return read


def _build(session: Session, row: Round, *, read_only: bool = False) -> RoundReport:
    config = RoundConfig.model_validate_json(row.config_json)
    withheld = _withheld(config, row.status)
    series_kind = rounds.series_kind(row.round_type)
    subject = session.get(Subject, row.subject_id)
    items = list(
        session.scalars(
            select(RoundItem)
            .where(RoundItem.round_id == row.id, RoundItem.role == "main")
            .order_by(RoundItem.ordinal)
        )
    )
    history: dict[int, list[Attempt]] = {item.id: [] for item in items}
    for attempt in session.scalars(
        select(Attempt).where(Attempt.round_item_id.in_(history)).order_by(Attempt.id)
    ):
        history[attempt.round_item_id].append(attempt)
    texts = dict(
        session.execute(
            select(QuestionVersion.id, QuestionVersion.text).where(
                QuestionVersion.id.in_({item.question_version_id for item in items})
            )
        ).all()
    )
    if read_only:
        finals = [f for f in (_final_attempt(history[item.id]) for item in items) if f is not None]
        read = _read_only_reader(session, row, config, items, finals)
    else:
        read = _settled_reader(session)

    rows: list[ItemRow] = []
    originals: list[AnswerBrief] = []
    summary_items: list[ItemForSummary] = []
    delivery_input: list[AttemptForAggregate] = []
    for item in items:
        item_row, result, final = _row(
            item, texts[item.question_version_id], history[item.id], read
        )
        rows.append(item_row)
        originals.append(_row_brief(item_row, result))
        if item_row.status == "not_asked":
            continue
        ready = item_row.status == "ready" and result is not None
        summary_items.append(
            ItemForSummary(
                result=result.assessment if ready else None,
                attempt_kind=final.attempt_kind,
                given_up=final.outcome == "given_up",
            )
        )
        if ready and result.metrics is not None and not withheld:
            delivery_input.append(
                AttemptForAggregate(
                    metrics=result.metrics,
                    attempt_kind=final.attempt_kind,
                    factual_score=item_row.factual,
                )
            )

    if withheld:
        knowledge = delivery = None
        provisional = False
    else:
        knowledge = round_knowledge_summary(summary_items, series=series_kind)
        delivery = aggregate_round(
            delivery_input, success_factual_min=_good_from(config), series=series_kind
        )
        provisional = knowledge.provisional or row.status == "abandoned"
    repeats = None
    if config.immediate_repeat and not withheld and not read_only:
        repeats = _repeat_series(config, items, rows, originals, history, read)
    series = None
    if config.follow_ups.enabled and not withheld and not read_only:
        series = _follow_up_series(session, row, items, rows, read)
    comparison = None
    if row.round_type == "retry" and not withheld and not read_only:
        comparison = _retry_comparison(session, row, items, rows, originals)
    summary = rounds.round_summary(session, row.id)
    return RoundReport(
        round_id=row.id,
        subject_id=row.subject_id,
        subject_name=subject.name,
        status=row.status,
        started_at=_utc(row.started_at),
        finished_at=_utc(row.finished_at) if row.finished_at else None,
        config=config,
        withheld=withheld,
        provisional=provisional,
        asked=sum(r.status != "not_asked" for r in rows),
        total=len(items),
        knowledge=knowledge,
        delivery=delivery,
        items=[_hide(r) if withheld else r for r in rows],
        pause_count=summary.pause_count,
        paused_ms=summary.paused_ms,
        restarted_ordinals=[r.ordinal for r in rows if r.restarted],
        follow_ups=series,
        parent_round_id=row.parent_round_id,
        selection_counts=_selection_counts(row, items),
        extension=_extension_series(session, row, config) if not read_only else None,
        repeats=repeats,
        retry=_retry_series(session, row, config) if not read_only else None,
        retry_comparison=comparison,
    )


def _retry_series(session: Session, row: Round, config: RoundConfig) -> RetrySeries | None:
    """The retry round's own series, read without writing; never part of this round's scores."""
    if not config.retry_round or row.round_type in ("retry", "extension"):
        return None
    child = session.scalar(
        select(Round).where(Round.parent_round_id == row.id, Round.round_type == "retry")
    )
    if child is None:
        return None
    report = _build(session, child, read_only=True)
    return RetrySeries(
        round_id=child.id,
        status=child.status,
        started_at=report.started_at,
        asked=report.asked,
        total=report.total,
        good_or_better=sum(i.band in GOOD_BANDS for i in report.items),
        still_below=sum(
            i.band in LOW_BANDS or i.outcome in NO_ANSWER_OUTCOMES for i in report.items
        ),
        provisional=report.provisional,
        withheld=report.withheld,
        knowledge=report.knowledge,
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median_ms(values: list[int | None]) -> int | None:
    kept = [v for v in values if v is not None]
    return round(median(kept)) if kept else None


def _retry_comparison(
    session: Session,
    row: Round,
    items: list[RoundItem],
    rows: list[ItemRow],
    answers: list[AnswerBrief],
) -> RetryComparison | None:
    """A retry round's answers next to the original round's answers to the same items."""
    parent = session.get(Round, row.parent_round_id) if row.parent_round_id else None
    if parent is None:
        return None
    parent_rows = {r.item_id: r for r in _build(session, parent, read_only=True).items}
    series_rows: list[SeriesRow] = []
    for item, item_row, again in zip(items, rows, answers, strict=True):
        before = parent_rows.get(item.parent_item_id)
        if before is None:
            continue
        original = _row_brief(before, None)
        series_rows.append(
            SeriesRow(
                ordinal=item_row.ordinal,
                item_id=item.id,
                question_text=item_row.question_text,
                original=original,
                again=again,
                factual_change=_change(original, again),
                below_good=_below_good(again),
            )
        )
    paired = [
        r for r in series_rows if r.original.factual is not None and r.again.factual is not None
    ]
    asked = [r for r in series_rows if r.again.status != "not_asked"]
    return RetryComparison(
        parent_round_id=parent.id,
        parent_started_at=_utc(parent.started_at),
        paired=len(paired),
        original_mean_total=_mean([r.original.total for r in paired]),
        retry_mean_total=_mean([r.again.total for r in paired]),
        original_mean_factual=_mean([r.original.factual for r in paired]),
        retry_mean_factual=_mean([r.again.factual for r in paired]),
        original_median_preparation_ms=_median_ms([r.original.preparation_used_ms for r in asked]),
        retry_median_preparation_ms=_median_ms([r.again.preparation_used_ms for r in asked]),
        rows=series_rows,
        still_below=[r.ordinal for r in series_rows if r.below_good],
    )


def _good_from(config: RoundConfig) -> int:
    return config.band_bounds[1] if config.band_bounds else SUCCESS_FACTUAL_MIN


def _repeat_series(
    config: RoundConfig,
    items: list[RoundItem],
    rows: list[ItemRow],
    originals: list[AnswerBrief],
    history: dict[int, list[Attempt]],
    read: Callable[[Attempt], AttemptResultOut],
) -> RepeatSeries | None:
    """The Answer again repeats' own series; fills each repeated row's `again`."""
    series_rows: list[SeriesRow] = []
    summary_items: list[ItemForSummary] = []
    delivery_input: list[AttemptForAggregate] = []
    for item, item_row, original in zip(items, rows, originals, strict=True):
        repeat = _again_attempt(history[item.id])
        if repeat is None:
            continue
        result = read(repeat)
        again = _answer_brief(repeat, result)
        item_row.again = again
        series_rows.append(
            SeriesRow(
                ordinal=item_row.ordinal,
                item_id=item.id,
                question_text=item_row.question_text,
                original=original,
                again=again,
                factual_change=_change(original, again),
                below_good=_below_good(again),
            )
        )
        ready = result.status == "ready" and result.assessment is not None
        summary_items.append(
            ItemForSummary(
                result=result.assessment if ready else None,
                attempt_kind="immediate",
                given_up=repeat.outcome == "given_up",
            )
        )
        if ready and result.metrics is not None:
            delivery_input.append(
                AttemptForAggregate(
                    metrics=result.metrics, attempt_kind="immediate", factual_score=again.factual
                )
            )
    if not series_rows:
        return None
    return RepeatSeries(
        asked=len(series_rows),
        good_or_better=sum(r.again.band in GOOD_BANDS for r in series_rows),
        improved=sum(r.factual_change is not None and r.factual_change > 0 for r in series_rows),
        rows=series_rows,
        knowledge=round_knowledge_summary(summary_items, series="immediate"),
        delivery=aggregate_round(
            delivery_input, success_factual_min=_good_from(config), series="immediate"
        ),
    )


def _extension_series(session: Session, row: Round, config: RoundConfig) -> ExtensionSeries | None:
    """The extension's own series, read without writing; never part of this round's scores."""
    if not config.extension.enabled or row.round_type == "extension":
        return None
    child = session.scalar(
        select(Round).where(Round.parent_round_id == row.id, Round.round_type == "extension")
    )
    if child is None:
        return None
    report = _build(session, child, read_only=True)
    return ExtensionSeries(
        round_id=child.id,
        status=child.status,
        asked=report.asked,
        total=report.total,
        good_or_better=sum(i.band in GOOD_BANDS for i in report.items),
        provisional=report.provisional,
        withheld=report.withheld,
        knowledge=report.knowledge,
    )


def _selection_counts(row: Round, items: list[RoundItem]) -> dict[str, int] | None:
    if row.round_type != "adaptive":
        return None
    counts: dict[str, int] = {}
    for item in items:
        if item.selection is not None:
            counts[item.selection] = counts.get(item.selection, 0) + 1
    return counts


def _follow_up_series(
    session: Session,
    row: Round,
    items: list[RoundItem],
    rows: list[ItemRow],
    read: Callable[[Attempt], AttemptResultOut],
) -> FollowUpSeries:
    """The follow-ups' own scores; the main score above never sees them."""
    follow_items = {
        item.parent_item_id: item
        for item in session.scalars(
            select(RoundItem).where(RoundItem.round_id == row.id, RoundItem.role == "followup")
        )
    }
    tried: dict[int, list[Attempt]] = {item.id: [] for item in follow_items.values()}
    for attempt in session.scalars(
        select(Attempt).where(Attempt.round_item_id.in_(tried)).order_by(Attempt.id)
    ):
        tried[attempt.round_item_id].append(attempt)
    asked = not_asked = unresolved = good = processing = 0
    summary_items: list[ItemForSummary] = []
    for item, item_row in zip(items, rows, strict=True):
        if item_row.status == "not_asked":
            continue
        follow = follow_items.get(item.id)
        final = _final_attempt(tried[follow.id]) if follow is not None else None
        if final is None or final.outcome in ("pending", "interrupted"):
            not_asked += 1
            continue
        asked += 1
        result = read(final)
        ready = result.status == "ready" and result.assessment is not None
        if not ready:
            unresolved += 1
            if result.status == "processing":
                processing += 1
        elif result.assessment.band in GOOD_BANDS:
            good += 1
        summary_items.append(
            ItemForSummary(
                result=result.assessment if ready else None,
                role="followup",
                attempt_kind="followup",
            )
        )
    knowledge = follow_up_summary(summary_items)
    return FollowUpSeries(
        asked=asked,
        not_asked=not_asked,
        unresolved=unresolved,
        good_or_better=good,
        provisional=knowledge.provisional,
        knowledge=knowledge,
        processing=processing,
    )


def _hide(row: ItemRow) -> ItemRow:
    return row.model_copy(
        update={
            "band": None,
            "factual": None,
            "total": None,
            "fluency": None,
            "preparation_used_ms": None,
            "speech_start_latency_ms": None,
        }
    )


def round_report(session: Session, round_id: int) -> RoundReport:
    row = session.get(Round, round_id)
    if row is None:
        raise HTTPException(404, ROUND_NOT_FOUND)
    report = _build(session, row)
    report.ai_cost_usd = round_costs(session, [row.id]).get(row.id)
    return report


# "All questions" covers rounds stored "all" and legacy "expanded" rounds (a subset of its pool).
POLICY_GROUPS = {"materials": {"materials"}, "expanded": {"expanded"}, "all": {"all", "expanded"}}


def history(
    session: Session,
    subject_id: int | None = None,
    feedback_timing: str | None = None,
    source_policy: str | None = None,
    chapter_id: int | None = None,
) -> list[HistoryRow]:
    query = (
        select(Round)
        .where(Round.round_type != "extension")  # shown on its round's summary instead
        .order_by(Round.started_at.desc(), Round.id.desc())
    )
    if subject_id is not None:
        query = query.where(Round.subject_id == subject_id)
    out: list[HistoryRow] = []
    listed = list(session.scalars(query))
    # a retry round names its original round: one batched read, only when there are any
    parent_ids = {
        r.parent_round_id for r in listed if r.round_type == "retry" and r.parent_round_id
    }
    parents = (
        dict(
            session.execute(
                select(Round.id, Round.started_at).where(Round.id.in_(parent_ids))
            ).all()
        )
        if parent_ids
        else {}
    )
    configs = {row.id: RoundConfig.model_validate_json(row.config_json) for row in listed}
    costs = round_costs(session, [row.id for row in listed])  # one grouped read for all rounds
    # chapter names: one batched read, only when a listed round has a chapter
    chapter_ids = {c.scope.chapter_id for c in configs.values() if c.scope.chapter_id is not None}
    chapter_names = (
        dict(
            session.execute(
                select(Chapter.id, Chapter.name).where(Chapter.id.in_(chapter_ids))
            ).all()
        )
        if chapter_ids
        else {}
    )
    for row in listed:
        config = configs[row.id]
        if feedback_timing is not None and config.feedback_timing != feedback_timing:
            continue
        if source_policy is not None and config.source_policy not in POLICY_GROUPS[source_policy]:
            continue
        if chapter_id is not None and config.scope.chapter_id != chapter_id:
            continue
        report = _build(session, row, read_only=True)
        knowledge = report.knowledge
        preparation = [
            i.preparation_used_ms for i in report.items if i.preparation_used_ms is not None
        ]
        out.append(
            HistoryRow(
                round_id=row.id,
                subject_id=row.subject_id,
                subject_name=report.subject_name,
                status=row.status,
                started_at=report.started_at,
                finished_at=report.finished_at,
                feedback_timing=config.feedback_timing,
                total=report.total,
                asked=report.asked,
                primary_score=knowledge.primary_score if knowledge else None,
                provisional=report.provisional,
                band_distribution=knowledge.band_distribution
                if knowledge
                else dict.fromkeys(BAND_LABELS, 0),
                median_preparation_used_ms=round(median(preparation)) if preparation else None,
                mean_fluency=knowledge.mean_fluency if knowledge else None,
                withheld=report.withheld,
                round_type=row.round_type,
                source_policy=config.source_policy,
                parent_round_id=row.parent_round_id if row.round_type == "retry" else None,
                parent_started_at=_utc(parents[row.parent_round_id])
                if row.parent_round_id in parents
                else None,
                chapter_id=config.scope.chapter_id,
                chapter_name=chapter_names.get(config.scope.chapter_id),
                ai_cost_usd=costs.get(row.id),
            )
        )
        if len(out) >= HISTORY_LIMIT:
            break
    return out
