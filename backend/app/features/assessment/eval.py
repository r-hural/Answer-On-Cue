"""Manual live eval: the golden set through assess() with the real assessment generator.

python -m app.features.assessment.eval [--case <id> …] [--out <dir>]

Writes data/assessment-eval/<YYYYMMDD-HHMMSS>.md (or under --out) and prints its path. Exits 0
even when cases fail: a person reads the report and judges. Live and billable: one AI call per
answered case plus at most one retry each.
"""

import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.features.assessment.golden import (
    GoldenCase,
    case_input,
    check_case,
    load_cases,
    load_questions,
)
from app.features.assessment.prompts import ASSESSMENT_PROMPT_VERSION
from app.features.assessment.schemas import (
    BAND_LABELS,
    AssessmentError,
    AssessmentResult,
)
from app.features.assessment.scoring import display_value
from app.features.assessment.service import assess
from app.features.languages.profiles import get_profile
from app.features.providers.base import TextGenerator
from app.features.providers.registry import get_text_generator, resolve_role
from app.features.providers.types import ProviderError
from app.features.providers.usage import InMemoryUsageRecorder, UsageRecorder
from app.features.questions.schemas import QuestionWithRubric

# The golden set is Polish data.
GOLDEN_LANGUAGE = "pl"
FEEDBACK_CASES = ("bio-partial", "bio-casual", "hist-grammar", "hist-misheard")
LANGUAGE_KEYS = ("grammar", "vocabulary")
STYLE_KEYS = ("formality", "terminology", "structure", "precision")
REGISTER_LABELS = {
    "grammar": "Grammar",
    "vocabulary": "Vocabulary",
    "formality": "Formality",
    "terminology": "Terminology",
    "structure": "Structure",
    "precision": "Precision",
}
DISPLAY_LABELS = {
    "auto": "shown automatically",
    "one_click": "one click",
    "on_request": "on request",
}

GeneratorFactory = Callable[[Settings, UsageRecorder], TextGenerator]


def _live_generator(settings: Settings, recorder: UsageRecorder) -> TextGenerator:
    return get_text_generator(settings, "assessment", recorder)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="assessment-eval", description="Grade the golden set (live, billable)."
    )
    parser.add_argument("--case", action="append", default=[], help="case id (can be repeated)")
    parser.add_argument("--out", type=Path, help="report directory (default data/assessment-eval)")
    return parser.parse_args(argv)


def select_cases(all_cases: list[GoldenCase], wanted: list[str]) -> list[GoldenCase]:
    """The chosen cases plus the pair bases they need, in golden-set order; all when none."""
    if not wanted:
        return all_cases
    by_id = {c.id: c for c in all_cases}
    needed = set(wanted)
    for cid in wanted:
        if by_id[cid].expect.pair is not None:
            needed.add(by_id[cid].expect.pair.with_)
    return [c for c in all_cases if c.id in needed]


Outcome = AssessmentResult | str  # a result, or the error text of a failed call


def run_cases(
    cases: list[GoldenCase],
    questions: dict[int, QuestionWithRubric],
    generator: TextGenerator,
    settings: Settings | None = None,
) -> dict[str, Outcome]:
    scoring = {}
    if settings is not None:
        scoring = dict(bounds=settings.factual_band_bounds, weights=settings.factual_weights)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    outcomes: dict[str, Outcome] = {}
    for case in cases:
        print(f"==> {case.id}", flush=True)
        try:
            outcomes[case.id] = assess(
                case_input(case, questions),
                generator,
                operation_id=f"eval-{stamp}:{case.id}",
                profile=get_profile(GOLDEN_LANGUAGE),
                **scoring,
            )
        except AssessmentError as error:
            outcomes[case.id] = f"{error.message} {'; '.join(error.errors)}"
        except ProviderError as error:
            outcomes[case.id] = f"provider error ({error.kind}): {error.message}"
    return outcomes


def check_all(cases: list[GoldenCase], outcomes: dict[str, Outcome]) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    for case in cases:
        outcome = outcomes[case.id]
        if isinstance(outcome, str):
            reasons[case.id] = [outcome]
            continue
        pair = None
        if case.expect.pair is not None:
            base = outcomes.get(case.expect.pair.with_)
            pair = base if isinstance(base, AssessmentResult) else None
        reasons[case.id] = check_case(case, outcome, pair_result=pair)
    return reasons


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _shown(value: float | None) -> str:
    return "—" if value is None else str(display_value(value))


def _bullets(lines: list[str]) -> list[str]:
    return [f"- {line}" for line in lines] or ["- (none)"]


def feedback_section(
    case: GoldenCase, result: AssessmentResult, question: QuestionWithRubric
) -> list[str]:
    """Every field of one assessment under the nine feedback headings."""
    ai, s = result.ai, result.scores
    points = {p.id: p for p in question.rubric.essential_points}
    out = [
        f"## Full feedback: {case.id}",
        "",
        f"**Question:** {question.text}",
        "",
        f"**Transcript:** {case.transcript}",
        "",
    ]
    if ai is None or s is None:
        return [*out, f"No AI assessment (status: {result.status}).", ""]
    out += [
        f"**Scores:** correctness {_shown(s.correctness)}, completeness {_shown(s.completeness)}, "
        f"factual {_shown(s.factual)} ({BAND_LABELS[result.band]}), "
        f"language {_shown(s.language)}, “Knowledge and language” {_shown(s.total)}",
        "",
        "### 1. What was correct",
        "",
    ]
    out += _bullets(
        [
            f"{j.point_id} ({j.status}): {points[j.point_id].text} — “{j.evidence}” — {j.note}"
            for j in ai.points
            if j.status in ("covered", "partial")
        ]
        + [f"Evidence of correctness: “{q}”" for q in ai.correctness_evidence]
    )
    out += ["", "### 2. Factual mistakes", ""]
    out += _bullets(
        [
            f"“{m.quote}” → {m.correction}"
            + (f" (major mistake, rule {m.rule_index})" if m.major else "")
            for m in ai.factual_mistakes
        ]
        + [
            f"{j.point_id} (wrong): “{j.evidence}” — {j.note}"
            for j in ai.points
            if j.status == "wrong"
        ]
        + [
            f"Possible speech-recognition error: “{d.quote}”"
            + (f" (probably: “{d.likely_meant}”)" if d.likely_meant else "")
            + f" — {d.reason}"
            for d in ai.transcription_doubts
        ]
    )
    out += ["", "### 3. What was missing", ""]
    out += _bullets(
        [
            f"{j.point_id}: {points[j.point_id].text} — {j.note}"
            for j in ai.points
            if j.status == "missing"
        ]
    )
    out += ["", "### 4. Additional details", ""]
    out += _bullets(
        [f"Enrichment: {e}" for e in ai.enrichment_mentioned]
        + [
            f"Outside the material ({o.judgement}): “{o.quote}” — {o.note}"
            for o in ai.outside_material
        ]
        + [f"Uncertainty: {f}" for f in ai.uncertainty_flags]
    )
    register = ai.language_register.by_key()
    for heading, keys in (
        ("### 5. Language and wording", LANGUAGE_KEYS),
        ("### 6. Exam answer style", STYLE_KEYS),
    ):
        out += ["", heading, ""]
        lines = []
        for key in keys:
            judgement = register[key]
            quotes = "".join(f" “{q}”" for q in judgement.quotes)
            lines.append(f"{REGISTER_LABELS[key]}: {judgement.summary}{quotes}")
            lines += [
                f"  - “{i.quote}” → {i.suggestion} ({i.explanation})"
                for i in ai.language_issues
                if i.dimension == key
            ]
        out += _bullets(lines)
    out += ["", "### 7. Reaction time and fluency", ""]
    fluency = "Cannot be assessed reliably" if ai.fluency is None else str(ai.fluency)
    out += _bullets(
        [
            f"Fluency: {fluency} (coverage: {ai.fluency_coverage}, "
            f"confidence: {ai.fluency_confidence})",
            ai.fluency_feedback,
        ]
        + [f"Hesitation: “{h}”" for h in ai.hesitation_examples]
    )
    out += ["", "### 8. A better version of the answer", "", ai.better_version, ""]
    out += _bullets([f"To improve: {i}" for i in ai.improvements])
    display = DISPLAY_LABELS.get(result.model_answer_display or "", "—")
    out += [
        "",
        "### 9. Model answer",
        "",
        f"({display}) {question.rubric.model_answer}",
        "",
    ]
    return out


def _scoring_lines(outcomes: dict[str, Outcome]) -> list[str]:
    results = [o for o in outcomes.values() if isinstance(o, AssessmentResult)]
    if not results:
        return []
    (wc, wp), bounds = results[0].factual_weights, results[0].band_bounds
    return [
        f"- Weights: correctness {wc}%, completeness {wp}%, language 20%; "
        f"band thresholds: {', '.join(map(str, bounds))}"
    ]


def render_report(
    cases: list[GoldenCase],
    outcomes: dict[str, Outcome],
    reasons: dict[str, list[str]],
    questions: dict[int, QuestionWithRubric],
    *,
    provider: str,
    model: str,
    recorder: InMemoryUsageRecorder,
    started: datetime,
) -> str:
    passed = sum(not reasons[c.id] for c in cases)
    events = recorder.events
    tokens_in = sum(e.usage.input_tokens for e in events)
    tokens_out = sum(e.usage.output_tokens for e in events)
    lines = [
        f"# Golden set assessment — {started:%Y-%m-%d %H:%M:%S}",
        "",
        f"- Provider: {provider}, model: {model}, prompt version: {ASSESSMENT_PROMPT_VERSION}",
        f"- AI calls: {len(events)}; tokens: input {tokens_in}, output {tokens_out}",
        f"- Passed: {passed} of {len(cases)}",
        *_scoring_lines(outcomes),
        "",
        "| Case | Result | Band | Factual | Language | AI calls | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        outcome = outcomes[case.id]
        verdict = "PASSED" if not reasons[case.id] else "FAILED"
        notes = "; ".join(reasons[case.id]) or "—"
        if isinstance(outcome, str):
            row = [case.id, verdict, "—", "—", "—", "—", notes]
        else:
            s = outcome.scores
            row = [
                case.id,
                verdict,
                BAND_LABELS[outcome.band] if outcome.band else f"none ({outcome.status})",
                _shown(s.factual) if s else "—",
                _shown(s.language) if s else "—",
                str(outcome.attempts_used),
                notes,
            ]
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    lines.append("")
    for case in cases:
        outcome = outcomes[case.id]
        if case.id not in FEEDBACK_CASES:
            continue
        if isinstance(outcome, str):
            lines += [f"## Full feedback: {case.id}", "", f"Error: {outcome}", ""]
            continue
        lines += feedback_section(case, outcome, questions[case.question_version_id])
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    settings: Settings | None = None,
    generator_factory: GeneratorFactory | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    all_cases = load_cases()
    known = [c.id for c in all_cases]
    unknown = [cid for cid in args.case if cid not in known]
    if unknown:
        print(f"Unknown case: {', '.join(unknown)}. Available: {', '.join(known)}.")
        return 2
    cases = select_cases(all_cases, args.case)
    questions = load_questions()
    settings = settings or get_settings()
    recorder = InMemoryUsageRecorder()
    started = datetime.now()
    try:
        generator = (generator_factory or _live_generator)(settings, recorder)
    except ProviderError as error:
        print(error.message)
        return 1
    provider, model = resolve_role(settings, "assessment")
    outcomes = run_cases(cases, questions, generator, settings)
    reasons = check_all(cases, outcomes)
    report = render_report(
        cases,
        outcomes,
        reasons,
        questions,
        provider=provider,
        model=model,
        recorder=recorder,
        started=started,
    )
    out_dir = args.out or settings.data_dir / "assessment-eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{started:%Y%m%d-%H%M%S}.md"
    path.write_text(report + "\n", encoding="utf-8")
    passed = sum(not r for r in reasons.values())
    print(f"passed {passed} of {len(cases)}")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
