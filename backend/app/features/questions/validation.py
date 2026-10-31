"""Code checks on each generated question before it is saved (source policies, rubric checks)."""

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.features.questions.prompts import GeneratedQuestion
from app.features.questions.schemas import Basis, EssentialPoint, Rubric, SourceIssue

TEXT_MIN, TEXT_MAX = 10, 400
POINTS_MIN, POINTS_MAX = 2, 8
DUPLICATE_RATIO = 0.85
# (prep min, prep max), (answer min, answer max) in seconds
DURATIONS = {"short": ((0, 60), (15, 90)), "open": ((0, 120), (45, 300))}

REASON_TEXT = "invalid question length"
REASON_POINTS = "invalid number of points"
REASON_WEIGHT = "invalid point weight"
REASON_NO_SOURCE = "no source for a point"
REASON_DUPLICATE = "duplicate"
REASON_BEYOND = "point beyond the materials"
REASON_BEYOND_ANSWER = "model answer beyond the materials"
REASON_NOT_OUTSIDE = "point taken from the materials"
REASON_REMOVED_PASSAGE = "cites a passage that was removed in a newer version of the document"

_PUNCTUATION = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


@dataclass
class Validated:
    question: GeneratedQuestion | None
    reason: str | None = None


def normalize_text(text: str) -> str:
    return _SPACES.sub(" ", _PUNCTUATION.sub(" ", text.casefold())).strip()


def is_near_duplicate(text: str, others: list[str]) -> bool:
    normalized = normalize_text(text)
    return any(
        SequenceMatcher(None, normalized, normalize_text(other)).ratio() >= DUPLICATE_RATIO
        for other in others
    )


def normalize_weights(weights: list[float]) -> list[float]:
    """Sum 1.0 at 4 decimals; the rounding remainder goes to the largest weight."""
    total = sum(weights)
    rounded = [round(w / total, 4) for w in weights]
    largest = max(range(len(weights)), key=lambda i: weights[i])
    rest = sum(w for i, w in enumerate(rounded) if i != largest)
    rounded[largest] = round(1.0 - rest, 4)
    return rounded


def ensure_unique_point_ids(points: list[EssentialPoint]) -> None:
    """Assessment judges every point by id: blank or repeated ids become p1…pn."""
    keys = [p.id.strip().casefold() for p in points]
    if all(keys) and len(set(keys)) == len(keys):
        return
    for n, point in enumerate(points, start=1):
        point.id = f"p{n}"


def _clamp(value: int, bounds: tuple[int, int]) -> int:
    return max(bounds[0], min(bounds[1], value))


def question_basis(rubric: Rubric, policy: str) -> Basis:
    """The question label, derived in code from its points (never asked of the AI)."""
    if policy == "outside":
        return "expanded"
    if policy != "expanded":
        return "source"
    bases = {p.basis for p in rubric.essential_points}
    if bases <= {"source"} and not rubric.beyond_material:
        return "source"
    if bases == {"expanded"}:
        return "expanded"
    return "mixed"


def clean_source_issues(issues: list[SourceIssue], allowed_passage_ids: set[int]) -> None:
    """Keeps only allowed passage ids; a contradiction needs two passages, else it is unclear."""
    for issue in issues:
        issue.passage_ids = [
            i for i in dict.fromkeys(issue.passage_ids) if i in allowed_passage_ids
        ]
        if issue.kind == "contradiction" and len(issue.passage_ids) < 2:
            issue.kind = "unclear"


def validate_question(
    question: GeneratedQuestion,
    allowed_passage_ids: set[int],
    existing_texts: list[str],
    *,
    policy: str = "materials",
) -> Validated:
    """allowed_passage_ids: the topic's linked, non-excluded passages. Returns a cleaned copy.

    Under "materials" every point is "source" and needs a valid passage; under "expanded"
    "expanded" points lose their passage ids (external knowledge has no document citation).
    """
    q = question.model_copy(deep=True)
    q.text = q.text.strip()
    if not TEXT_MIN <= len(q.text) <= TEXT_MAX:
        return Validated(None, REASON_TEXT)
    points = q.rubric.essential_points
    if not POINTS_MIN <= len(points) <= POINTS_MAX:
        return Validated(None, REASON_POINTS)
    if any(not (math.isfinite(p.weight) and p.weight > 0) for p in points):
        return Validated(None, REASON_WEIGHT)
    if policy == "outside":
        # Beyond the materials only: every point expanded, and no passage is cited anywhere.
        if any(p.basis != "expanded" for p in points):
            return Validated(None, REASON_NOT_OUTSIDE)
        allowed_passage_ids = set()
    expanded = policy in ("expanded", "outside")
    if not expanded:
        if any(p.basis != "source" for p in points):
            return Validated(None, REASON_BEYOND)
        if q.rubric.beyond_material:
            return Validated(None, REASON_BEYOND_ANSWER)
    for point in points:
        if point.basis == "expanded":
            point.passage_ids = []
            continue
        point.passage_ids = [
            i for i in dict.fromkeys(point.passage_ids) if i in allowed_passage_ids
        ]
        if not point.passage_ids:
            return Validated(None, REASON_NO_SOURCE)
    q.rubric.beyond_material = [t.strip() for t in q.rubric.beyond_material if t.strip()]
    clean_source_issues(q.rubric.source_issues, allowed_passage_ids)
    for enrichment in q.rubric.enrichment_points:
        enrichment.passage_ids = [i for i in enrichment.passage_ids if i in allowed_passage_ids]
    if is_near_duplicate(q.text, existing_texts):
        return Validated(None, REASON_DUPLICATE)
    for point, weight in zip(points, normalize_weights([p.weight for p in points]), strict=True):
        point.weight = weight
    ensure_unique_point_ids(points)
    prep, answer = DURATIONS[q.style]
    q.suggested_prep_s = _clamp(q.suggested_prep_s, prep)
    q.suggested_answer_s = _clamp(q.suggested_answer_s, answer)
    return Validated(q)
