"""Scores computed in code from the AI's judgements; the model's arithmetic is never trusted."""

import math

from app.features.assessment.schemas import (
    DEFAULT_BAND_BOUNDS,
    DEFAULT_FACTUAL_WEIGHTS,
    AiAssessment,
    Band,
    BandBounds,
    FactualWeights,
    ModelAnswerDisplay,
    PointJudgement,
    Scores,
)
from app.features.questions.schemas import Rubric

POINT_CREDIT = {"covered": 1.0, "partial": 0.5, "missing": 0.0, "wrong": 0.0}


def completeness_from_points(points: list[PointJudgement], rubric: Rubric) -> float:
    """100 × Σ weight × credit, with the rubric weights normalized to sum 1.0.

    Disputed points (the sources disagree) are left out and the other weights renormalised;
    when every point is disputed, all are graded as usual."""
    disputed = {j.point_id for j in points if j.disputed}
    weights = {p.id: p.weight for p in rubric.essential_points}
    if disputed and not set(weights) <= disputed:
        weights = {pid: w for pid, w in weights.items() if pid not in disputed}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 0.0
    credit = {j.point_id: POINT_CREDIT[j.status] for j in points}
    return 100 * sum(w / total_weight * credit.get(pid, 0.0) for pid, w in weights.items())


def validate_weights(weights: FactualWeights) -> FactualWeights:
    wc, wp = weights
    if not (10 <= wc <= 70 and 10 <= wp <= 70 and wc + wp == 80):
        raise ValueError("Correctness and completeness weights must each be 10–70 and sum to 80.")
    return (wc, wp)


def compute_scores(
    ai: AiAssessment, rubric: Rubric, weights: FactualWeights = DEFAULT_FACTUAL_WEIGHTS
) -> Scores:
    """total = wc·correctness + wp·completeness + 0.20·language (wc + wp = 0.80);
    factual = (wc·correctness + wp·completeness) / 0.80."""
    wc, wp = (w / 100 for w in validate_weights(weights))
    correctness = float(ai.correctness)
    completeness = completeness_from_points(ai.points, rubric)
    language = None if ai.language is None else float(ai.language)
    factual = (wc * correctness + wp * completeness) / 0.80
    total = wc * correctness + wp * completeness + 0.20 * (language or 0.0)
    return Scores(
        correctness=correctness,
        completeness=completeness,
        factual=factual,
        language=language,
        total=total,
    )


def display_value(x: float) -> int:
    """Round half up, for display only. Rounding to 9 decimals first absorbs float noise
    (29.499999999999996 is 29.5, shown as 30)."""
    return math.floor(round(x, 9) + 0.5)


def validate_bounds(bounds: BandBounds) -> BandBounds:
    b1, b2, b3 = bounds
    if not all(1 <= b <= 99 for b in bounds) or not b1 < b2 < b3:
        raise ValueError("The band boundaries must be increasing and between 1 and 99.")
    return (b1, b2, b3)


def classify_band(factual: float | None, bounds: BandBounds = DEFAULT_BAND_BOUNDS) -> Band | None:
    """The band of the shown (rounded) factual score, so band and number always agree."""
    b1, b2, b3 = validate_bounds(bounds)
    if factual is None:
        return None
    shown = display_value(factual)
    if shown < b1:
        return "weak"
    if shown < b2:
        return "needs_improvement"
    if shown < b3:
        return "good"
    return "very_good"


def model_answer_display(band: Band | None, status: str) -> ModelAnswerDisplay | None:
    if status == "no_answer" or band == "weak":
        return "auto"
    if band == "needs_improvement":
        return "one_click"
    if band in ("good", "very_good"):
        return "on_request"
    return None
