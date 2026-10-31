"""Round knowledge summary (round scores): a pure function of the item results."""

from statistics import fmean
from typing import Literal

from pydantic import BaseModel

from app.features.assessment.schemas import BAND_LABELS, AssessmentResult, AttemptKind, Band


class ItemForSummary(BaseModel):
    result: AssessmentResult | None  # None = unresolved (technical failure or still processing)
    role: Literal["main", "followup"] = "main"
    attempt_kind: AttemptKind = "initial"
    interrupted: bool = False
    given_up: bool = False  # not assessed: left out of every score and of unresolved


class RoundKnowledgeSummary(BaseModel):
    primary_score: float | None  # mean total of included main initial items; no_answer counts 0
    mean_correctness: float | None
    mean_completeness: float | None
    mean_factual: float | None
    mean_language: float | None
    language_assessable: int
    mean_fluency: float | None
    fluency_assessable: int
    band_distribution: dict[Band, int]
    assessed: int
    no_answer: int
    no_speech: int
    unresolved: int
    not_assessed: int = 0  # given up: not part of the score, never Weak
    provisional: bool  # any unresolved or no_speech among included items


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def included(item: ItemForSummary, series: AttemptKind = "initial") -> bool:
    """Original main answers only; follow-ups, repeats, retries and interrupted ones are apart.
    `series` picks another series of main answers instead (a retry round's own score)."""
    return item.role == "main" and item.attempt_kind == series and not item.interrupted


def round_knowledge_summary(
    items: list[ItemForSummary], *, series: AttemptKind = "initial"
) -> RoundKnowledgeSummary:
    picked = [i for i in items if included(i, series)]
    summary = _summarize([i.result for i in picked if not i.given_up])
    summary.not_assessed = sum(i.given_up for i in picked)
    return summary


def follow_up_summary(items: list[ItemForSummary]) -> RoundKnowledgeSummary:
    """The follow-ups' own series: never part of the round score."""
    return _summarize([i.result for i in items if i.role == "followup" and not i.interrupted])


def _summarize(results: list[AssessmentResult | None]) -> RoundKnowledgeSummary:
    unresolved = sum(r is None for r in results)
    resolved = [r for r in results if r is not None]
    scored = [r for r in resolved if r.scores is not None]  # assessed and no_answer
    languages = [r.scores.language for r in scored if r.scores.language is not None]
    fluencies = [r.ai.fluency for r in resolved if r.ai is not None and r.ai.fluency is not None]
    bands: dict[Band, int] = dict.fromkeys(BAND_LABELS, 0)
    for r in resolved:
        if r.band is not None:
            bands[r.band] += 1
    no_speech = sum(r.status == "no_speech" for r in resolved)
    return RoundKnowledgeSummary(
        primary_score=_mean([r.scores.total for r in scored]),
        mean_correctness=_mean([r.scores.correctness for r in scored]),
        mean_completeness=_mean([r.scores.completeness for r in scored]),
        mean_factual=_mean([r.scores.factual for r in scored]),
        mean_language=_mean(languages),
        language_assessable=len(languages),
        mean_fluency=_mean([float(f) for f in fluencies]),
        fluency_assessable=len(fluencies),
        band_distribution=bands,
        assessed=sum(r.status == "assessed" for r in resolved),
        no_answer=sum(r.status == "no_answer" for r in resolved),
        no_speech=no_speech,
        unresolved=unresolved,
        provisional=unresolved > 0 or no_speech > 0,
    )
