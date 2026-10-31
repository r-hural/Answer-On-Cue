"""Golden set: synthetic Polish answers with expected findings, checked offline and live."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.features.assessment.schemas import (
    AssessmentInput,
    AssessmentResult,
    Band,
    PointStatus,
    RegisterKey,
)
from app.features.delivery.schemas import AnswerMetrics
from app.features.questions.schemas import PassageRef, QuestionWithRubric

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "assessment" / "golden"
SUBJECTS_FILE = "subjects.json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssuesAny(_Strict):
    dimensions: list[RegisterKey]
    count: int


class IssueQuote(_Strict):
    dimension: RegisterKey
    text: str


class PairExpect(_Strict):
    with_: str = Field(alias="with")  # the id of the case to compare with
    factual_within: float | None = None
    language_lower: bool = False


class Expect(_Strict):
    status: Literal["assessed", "no_answer", "no_speech"]
    bands: list[Band] | None = None
    points_all_in: list[PointStatus] | None = None
    points_missing: list[str] | None = None
    max_factual_mistakes: int | None = None
    min_factual_mistakes: int | None = None
    mistake_keywords: list[str] | None = None  # any of them, in some correction
    max_language_issues: int | None = None
    no_issue_dimensions: list[RegisterKey] | None = None
    min_issues: dict[RegisterKey, int] | None = None
    min_issues_any: IssuesAny | None = None
    min_transcription_doubts: int | None = None
    no_major_mistake_quoting: str | None = None
    no_issue_quoting: IssueQuote | None = None
    max_correctness: int | None = None
    no_ai_call: bool = False
    pair: PairExpect | None = None
    min_disputed_points: int | None = None  # source policy: points the sources dispute
    no_mistake_on_points: list[str] | None = None  # these points are never judged wrong


class GoldenCase(_Strict):
    id: str
    question_version_id: int
    transcript: str | None
    outcome: Literal["answered", "dont_know", "skipped"]
    delivery: AnswerMetrics | None
    expect: Expect


class GoldenSubject(BaseModel):
    name: str
    passages: list[PassageRef]
    questions: list[QuestionWithRubric]


def load_subjects(directory: Path = GOLDEN_DIR) -> list[GoldenSubject]:
    """Subjects with each question's passages filled in from the subject's passage list."""
    data = json.loads((directory / SUBJECTS_FILE).read_text(encoding="utf-8"))
    subjects = []
    for raw in data["subjects"]:
        by_id = {p["id"]: p for p in raw["passages"]}
        questions = []
        for q in raw["questions"]:
            rubric = q["rubric"]
            cited = [
                i
                for point in rubric["essential_points"] + rubric.get("enrichment_points", [])
                for i in point.get("passage_ids", [])
            ] + [i for issue in rubric.get("source_issues", []) for i in issue["passage_ids"]]
            passages = [by_id[i] for i in dict.fromkeys(cited)]
            questions.append({**q, "passages": passages})
        subjects.append(GoldenSubject.model_validate({**raw, "questions": questions}))
    return subjects


def load_questions(directory: Path = GOLDEN_DIR) -> dict[int, QuestionWithRubric]:
    return {q.version_id: q for s in load_subjects(directory) for q in s.questions}


def load_cases(directory: Path = GOLDEN_DIR) -> list[GoldenCase]:
    files = sorted(p for p in directory.glob("*.json") if p.name != SUBJECTS_FILE)
    return [GoldenCase.model_validate_json(p.read_text(encoding="utf-8")) for p in files]


def case_input(case: GoldenCase, questions: dict[int, QuestionWithRubric]) -> AssessmentInput:
    return AssessmentInput(
        question=questions[case.question_version_id],
        transcript=case.transcript,
        outcome=case.outcome,
        delivery=case.delivery,
    )


def _issues(result: AssessmentResult, dimension: str) -> list:
    return [i for i in result.ai.language_issues if i.dimension == dimension]


def check_case(
    case: GoldenCase, result: AssessmentResult, *, pair_result: AssessmentResult | None = None
) -> list[str]:
    """Failure reasons in English; an empty list means the case passed."""
    e = case.expect
    fails: list[str] = []
    if result.status != e.status:
        return [f"status {result.status}, expected {e.status}"]
    if e.no_ai_call and result.attempts_used != 0:
        fails.append(f"{result.attempts_used} AI calls made, expected 0")
    if e.bands is not None and result.band not in e.bands:
        fails.append(f"band {result.band}, expected one of {e.bands}")
    ai = result.ai
    if ai is None:
        return fails
    if e.points_all_in is not None:
        bad = [f"{j.point_id}={j.status}" for j in ai.points if j.status not in e.points_all_in]
        if bad:
            fails.append(f"points outside {e.points_all_in}: {bad}")
    if e.points_missing is not None:
        status = {j.point_id: j.status for j in ai.points}
        bad = [
            f"{pid}={status.get(pid)}" for pid in e.points_missing if status.get(pid) != "missing"
        ]
        if bad:
            fails.append(f"points should be missing: {bad}")
    mistakes = ai.factual_mistakes
    if e.max_factual_mistakes is not None and len(mistakes) > e.max_factual_mistakes:
        fails.append(f"{len(mistakes)} factual mistakes, at most {e.max_factual_mistakes}")
    if e.min_factual_mistakes is not None and len(mistakes) < e.min_factual_mistakes:
        fails.append(f"{len(mistakes)} factual mistakes, at least {e.min_factual_mistakes}")
    if e.mistake_keywords is not None:
        words = [k.lower() for k in e.mistake_keywords]
        if not any(k in m.correction.lower() for m in mistakes for k in words):
            fails.append(f"no correction contains any of: {e.mistake_keywords}")
    issues = ai.language_issues
    if e.max_language_issues is not None and len(issues) > e.max_language_issues:
        fails.append(f"{len(issues)} language issues, at most {e.max_language_issues}")
    for dimension in e.no_issue_dimensions or []:
        if found := _issues(result, dimension):
            fails.append(f"unnecessary '{dimension}' issues: {[i.quote for i in found]}")
    for dimension, count in (e.min_issues or {}).items():
        if len(_issues(result, dimension)) < count:
            fails.append(
                f"too few '{dimension}' issues: {len(_issues(result, dimension))} < {count}"
            )
    if e.min_issues_any is not None:
        found = [i for i in issues if i.dimension in e.min_issues_any.dimensions]
        if len(found) < e.min_issues_any.count:
            fails.append(
                f"too few issues in {e.min_issues_any.dimensions}: "
                f"{len(found)} < {e.min_issues_any.count}"
            )
    doubts = ai.transcription_doubts
    if e.min_transcription_doubts is not None and len(doubts) < e.min_transcription_doubts:
        fails.append(f"{len(doubts)} transcription doubts, at least {e.min_transcription_doubts}")
    if e.no_major_mistake_quoting is not None:
        needle = e.no_major_mistake_quoting.lower()
        if [m for m in mistakes if m.major and needle in m.quote.lower()]:
            fails.append(f"major factual mistake attributed to the phrase '{needle}'")
    if e.no_issue_quoting is not None:
        needle = e.no_issue_quoting.text.lower()
        found = [
            i for i in _issues(result, e.no_issue_quoting.dimension) if needle in i.quote.lower()
        ]
        if found:
            fails.append(
                f"'{e.no_issue_quoting.dimension}' issue on an honest qualification: "
                f"{[i.quote for i in found]}"
            )
    if e.max_correctness is not None and ai.correctness > e.max_correctness:
        fails.append(f"correctness {ai.correctness}, at most {e.max_correctness}")
    if e.min_disputed_points is not None:
        disputed = [j.point_id for j in ai.points if j.disputed]
        if len(disputed) < e.min_disputed_points:
            fails.append(f"{len(disputed)} disputed points, at least {e.min_disputed_points}")
    if e.no_mistake_on_points is not None:
        status = {j.point_id: j.status for j in ai.points}
        bad = [pid for pid in e.no_mistake_on_points if status.get(pid) == "wrong"]
        if bad:
            fails.append(f"points judged wrong: {bad}")
    if e.pair is not None:
        fails += _check_pair(e.pair, result, pair_result)
    return fails


def _check_pair(
    pair: PairExpect, result: AssessmentResult, base: AssessmentResult | None
) -> list[str]:
    if base is None or base.scores is None or result.scores is None:
        return [f"no assessment of the base case {pair.with_} to compare with"]
    fails = []
    gap = abs(result.scores.factual - base.scores.factual)
    if pair.factual_within is not None and gap > pair.factual_within:
        fails.append(
            f"factual score differs from {pair.with_} by {gap:.1f} points "
            f"(at most {pair.factual_within:g})"
        )
    if pair.language_lower:
        mine, theirs = result.scores.language, base.scores.language
        if mine is None or theirs is None or mine >= theirs:
            fails.append(f"language {mine} is not lower than in {pair.with_} ({theirs})")
    return fails
