"""Trend series: attempt selection and one point per round. Pure: no session, no clock.

The selection is this module's own. It never takes "an item's latest attempt", because a later
answer-again attempt must not replace the independent first answer.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean
from typing import Any

from app.features.assessment.aggregate import ItemForSummary, round_knowledge_summary
from app.features.assessment.schemas import (
    DEFAULT_BAND_BOUNDS,
    DEFAULT_FACTUAL_WEIGHTS,
    AssessmentResult,
)
from app.features.assessment.service import no_answer_result
from app.features.delivery.aggregate import SUCCESS_FACTUAL_MIN, aggregate_round
from app.features.delivery.schemas import AnswerMetrics, AttemptForAggregate
from app.features.trends.schemas import Change, TrendFilters, TrendPoint

TRENDS_LIMIT = 50
MIN_POINTS = 2
SHOWN_STATUSES = ("finished", "abandoned")
SIDE_ROUNDS = ("extension", "retry")  # their own series, never part of `main`
IMMEDIATE = "immediate"  # coverage.rules.ASSISTED_KINDS


@dataclass(frozen=True)
class TrendRow:
    """One attempt row from the batched query, with the stored result it produced."""

    attempt_id: int
    round_id: int
    round_type: str
    round_status: str
    started_at: datetime
    config_json: str
    item_id: int
    role: str
    kind: str
    outcome: str
    processing: str | None  # attempt_processing.status
    assessment: dict[str, Any] | None
    metrics: dict[str, Any] | None
    question_version_id: int = 0


@dataclass(frozen=True)
class Selected:
    row: TrendRow
    restarted: bool  # the item had an earlier interrupted attempt of the same kind
    assisted: bool  # an answer-again attempt added by "Include answers with help"


def _utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _config(row: TrendRow) -> dict[str, Any]:
    try:
        value = json.loads(row.config_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _bounds(row: TrendRow) -> tuple[int, int, int]:
    value = _config(row).get("band_bounds")
    return tuple(value) if value else DEFAULT_BAND_BOUNDS  # type: ignore[return-value]


def _in_series(row: TrendRow, series: str) -> bool:
    match series:
        case "main":
            return (
                row.role == "main" and row.kind == "initial" and row.round_type not in SIDE_ROUNDS
            )
        case "followup":
            return row.role == "followup" and row.kind == "followup"
        case "immediate":
            return row.role == "main" and row.kind == IMMEDIATE
        case "retry":
            return row.role == "main" and row.kind == "retry"
        case "extension":
            return row.role == "main" and row.kind == "initial" and row.round_type == "extension"
    return False


def _answer(row: TrendRow, series: str) -> bool:
    """A finished attempt that can be a point's answer (never pending, interrupted or given
    up)."""
    if row.outcome in ("pending", "interrupted", "given_up"):
        return False
    # A skipped answer-again attempt is no answer (it would replace nothing).
    return not (series == "immediate" and row.outcome == "skipped")


def _choose(attempts: list[TrendRow], series: str, assisted: bool = False) -> Selected | None:
    chosen = None
    for attempt in attempts:
        if _answer(attempt, series):
            chosen = attempt
    if chosen is None:
        return None
    restarted = any(
        a.outcome == "interrupted" and a.attempt_id < chosen.attempt_id for a in attempts
    )
    return Selected(chosen, restarted, assisted)


def select_answers(rows: list[TrendRow], filters: TrendFilters) -> dict[int, list[Selected]]:
    """Per round (in the order of the rows), the answers of the series."""
    items: dict[tuple[int, int], list[TrendRow]] = defaultdict(list)
    rounds: dict[int, None] = {}
    for row in sorted(rows, key=lambda r: r.attempt_id):
        if row.round_status in SHOWN_STATUSES:
            items[(row.round_id, row.item_id)].append(row)
    for row in rows:
        if row.round_status in SHOWN_STATUSES:
            rounds[row.round_id] = None

    selected: dict[int, list[Selected]] = {}
    for (round_id, _), attempts in items.items():
        series_kind = [a for a in attempts if _in_series(a, filters.series)]
        pick = _choose(series_kind, filters.series)
        if pick is not None:
            selected.setdefault(round_id, []).append(pick)
        wants_help = filters.include_assisted and filters.series == "main"
        if wants_help and attempts[0].role == "main" and attempts[0].round_type not in SIDE_ROUNDS:
            again = [a for a in attempts if a.kind == IMMEDIATE]
            extra = _choose(again, IMMEDIATE, assisted=True)
            if extra is not None:
                selected.setdefault(round_id, []).append(extra)
    return {rid: selected[rid] for rid in rounds if rid in selected}


def no_answer(row: TrendRow) -> AssessmentResult:
    config = _config(row)
    return no_answer_result(
        row.question_version_id,
        bounds=tuple(config.get("band_bounds") or DEFAULT_BAND_BOUNDS),  # type: ignore[arg-type]
        weights=tuple(config.get("factual_weights") or DEFAULT_FACTUAL_WEIGHTS),  # type: ignore[arg-type]
    )


def _result(row: TrendRow) -> AssessmentResult | None:
    """The stored result, or None while it is unresolved (processing, failed, overdue audio)."""
    if row.outcome in ("dont_know", "skipped"):
        return no_answer(row)
    if row.outcome != "answered" or row.processing != "ready" or row.assessment is None:
        return None
    try:
        return AssessmentResult.model_validate(row.assessment)
    except ValueError:
        return None


def reliable_filler(assessment: dict | None, metrics: dict | None) -> bool:
    """Fillers are a lower bound: count them only where the transcript can be trusted."""
    if not metrics or not assessment:
        return False
    if metrics.get("filler_count") is None or not (metrics.get("first_to_last_speech_ms") or 0) > 0:
        return False
    ai = assessment.get("ai")
    if not ai or ai.get("fluency_coverage") in (None, "none"):
        return False
    return not ai.get("transcription_doubts")


def _filler_rate(rows: list[TrendRow]) -> tuple[float | None, int]:
    reliable = [r for r in rows if reliable_filler(r.assessment, r.metrics)]
    if not reliable:
        return None, 0
    count = sum(r.metrics["filler_count"] for r in reliable)
    minutes = sum(r.metrics["first_to_last_speech_ms"] for r in reliable) / 60000
    return round(count / minutes, 2), len(reliable)


def point_for(chosen: list[Selected], include_interrupted: bool) -> TrendPoint:
    first = chosen[0].row
    results = [_result(s.row) for s in chosen]
    summary = round_knowledge_summary(
        [ItemForSummary(result=r, role="main", attempt_kind="initial") for r in results]
    )

    # Restarted answers count for knowledge but not for speed: the learner had seen the question.
    eligible = [
        (s, r)
        for s, r in zip(chosen, results, strict=True)
        if include_interrupted or not s.restarted
    ]
    delivery_inputs = []
    for s, r in eligible:
        if s.row.metrics is None:
            continue
        metrics = AnswerMetrics.model_validate(s.row.metrics)
        if include_interrupted and metrics.interrupted:
            metrics = metrics.model_copy(update={"interrupted": False})
        factual = r.scores.factual if r is not None and r.scores is not None else None
        delivery_inputs.append(
            AttemptForAggregate(metrics=metrics, attempt_kind="initial", factual_score=factual)
        )
    bounds = _bounds(first)
    delivery = aggregate_round(
        delivery_inputs, success_factual_min=bounds[1] if bounds else SUCCESS_FACTUAL_MIN
    )
    filler, filler_reliable = _filler_rate([s.row for s, _ in eligible])
    fluencies = [
        float(r.ai.fluency)
        for _, r in eligible
        if r is not None and r.ai is not None and r.ai.fluency is not None
    ]
    return TrendPoint(
        round_id=first.round_id,
        started_at=_utc(first.started_at),
        status=first.round_status,  # type: ignore[arg-type]
        round_type=first.round_type,
        feedback_timing=_config(first).get("feedback_timing", "practice"),
        answers=summary.assessed + summary.no_answer,
        unresolved=summary.unresolved,
        assisted=sum(s.assisted for s in chosen),
        restarted=sum(s.restarted for s in chosen),
        provisional=summary.provisional or first.round_status == "abandoned",
        primary=summary.primary_score,
        factual=summary.mean_factual,
        language=summary.mean_language,
        language_assessable=summary.language_assessable,
        speed_answers=delivery.count_included,
        median_preparation_used_ms=delivery.median_preparation_used_ms,
        median_speech_start_latency_ms=delivery.median_speech_start_latency_ms,
        median_total_response_latency_ms=delivery.median_total_response_latency_ms,
        median_total_response_latency_successful_ms=(
            delivery.median_total_response_latency_successful_ms
        ),
        long_pauses_per_min=delivery.long_pauses_per_speaking_min,
        silence_ratio=delivery.aggregate_silence_ratio,
        filler_per_min=filler,
        filler_reliable=filler_reliable,
        fluency=fmean(fluencies) if fluencies else None,
        fluency_assessable=len(fluencies),
        band_bounds=bounds,
        changes=[],
    )


def models_of(chosen: list[Selected]) -> set[str]:
    out = set()
    for s in chosen:
        provenance = (s.row.assessment or {}).get("provenance") or {}
        if provenance.get("model"):
            out.add(provenance["model"])
    return out


def detectors_of(chosen: list[Selected]) -> set[str]:
    return {s.row.metrics["detector"] for s in chosen if (s.row.metrics or {}).get("detector")}


def mark_changes(
    points: list[TrendPoint], models: list[set[str]], detectors: list[set[str]]
) -> None:
    """What differs from the previous point; an empty set (nothing recorded) never marks."""
    for n in range(1, len(points)):
        changes: list[Change] = []
        if points[n].band_bounds != points[n - 1].band_bounds:
            changes.append("band_table")
        if models[n] and models[n - 1] and models[n] != models[n - 1]:
            changes.append("model")
        if detectors[n] and detectors[n - 1] and detectors[n] != detectors[n - 1]:
            changes.append("analysis")
        points[n].changes = changes
