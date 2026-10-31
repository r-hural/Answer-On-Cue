"""Grade one answer: rules without an AI call, one AI call, validation, one bounded retry."""

from app.features.assessment import prompts
from app.features.assessment.schemas import (
    DEFAULT_BAND_BOUNDS,
    DEFAULT_FACTUAL_WEIGHTS,
    AiAssessment,
    AssessmentError,
    AssessmentInput,
    AssessmentResult,
    BandBounds,
    FactualWeights,
    Scores,
)
from app.features.assessment.scoring import (
    classify_band,
    compute_scores,
    model_answer_display,
    validate_bounds,
    validate_weights,
)
from app.features.languages.profiles import LanguageProfile
from app.features.providers import limits
from app.features.providers.base import TextGenerator
from app.features.providers.types import Message, ProviderError

ASSESS_TASK = "assessment.assess"
MAX_OUTPUT_TOKENS = 6000
FAILED_MESSAGE = "The assessment failed: the model returned an invalid result."
EVIDENCE_REQUIRED = ("covered", "partial", "wrong")


def has_speech(inp: AssessmentInput) -> bool:
    if inp.transcript is None or not any(c.isalpha() for c in inp.transcript.strip()):
        return False
    return inp.delivery is None or inp.delivery.speech_ms > 0


def validate_output(ai: AiAssessment, inp: AssessmentInput) -> list[str]:
    """Rules the schema cannot express; any error makes the output invalid."""
    rubric = inp.question.rubric
    errors: list[str] = []
    expected = [p.id for p in rubric.essential_points]
    seen = [j.point_id for j in ai.points]
    missing = [pid for pid in expected if pid not in seen]
    duplicated = sorted({pid for pid in seen if seen.count(pid) > 1})
    unknown = [pid for pid in seen if pid not in expected]
    if missing:
        errors.append(f"points: missing judgement for point ids {missing}")
    if duplicated:
        errors.append(f"points: point ids judged more than once {duplicated}")
    if unknown:
        errors.append(f"points: unknown point ids {unknown}; use only {expected}")
    for j in ai.points:
        # An empty string is as good as null: not worth a retry call.
        if j.status == "missing" and (j.evidence or "").strip():
            errors.append(f"points[{j.point_id}]: evidence must be null for status missing")
        if j.status in EVIDENCE_REQUIRED and not (j.evidence or "").strip():
            errors.append(f"points[{j.point_id}]: evidence quote required for status {j.status}")
    if ai.language is None:
        errors.append("language: required for an answered transcript")
    if (ai.fluency_coverage == "none") != (ai.fluency is None):
        errors.append("fluency: must be null exactly when fluency_coverage is none")
    if inp.delivery is None and ai.fluency is not None:
        errors.append("fluency: must be null without measured delivery data")
    for n, m in enumerate(ai.factual_mistakes):
        if m.rule_index is not None and not 0 <= m.rule_index < len(rubric.major_error_rules):
            errors.append(
                f"factual_mistakes[{n}]: rule_index {m.rule_index} is not a valid index into "
                f"major_error_rules (0..{len(rubric.major_error_rules) - 1})"
            )
    if not 1 <= len(ai.improvements) <= 2:
        errors.append(f"improvements: give 1 or 2 items, not {len(ai.improvements)}")
    conflict_points = {c.point_id for c in ai.source_conflicts}
    for n, c in enumerate(ai.source_conflicts):
        if c.point_id is not None and c.point_id not in expected:
            errors.append(
                f"source_conflicts[{n}]: unknown point id {c.point_id!r}; use one of "
                f"{expected} or null"
            )
    for j in ai.points:
        if j.disputed and j.point_id not in conflict_points:
            errors.append(
                f"points[{j.point_id}]: a disputed point needs a source_conflicts entry with "
                "its point_id"
            )
    return errors


def clean_conflicts(ai: AiAssessment, inp: AssessmentInput) -> AiAssessment:
    """Code decides what stays disputed: passage ids must be among the passages sent, and a
    conflict needs two of them; a point without a kept conflict is graded normally."""
    sent = {p.id for p in prompts.cited_passages(inp.question)}
    ai = ai.model_copy(deep=True)
    kept = []
    for conflict in ai.source_conflicts:
        conflict.passage_ids = [i for i in dict.fromkeys(conflict.passage_ids) if i in sent]
        if len(conflict.passage_ids) >= 2:
            kept.append(conflict)
    ai.source_conflicts = kept
    disputed = {c.point_id for c in kept if c.point_id is not None}
    for j in ai.points:
        j.disputed = j.point_id in disputed
    return ai


def retry_message(errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors)
    return (
        "Your previous assessment was invalid for these reasons:\n"
        f"{listed}\n"
        "Return the complete corrected assessment for the same answer."
    )


def _result(
    inp: AssessmentInput, bounds: BandBounds, weights: FactualWeights, **fields
) -> AssessmentResult:
    base = dict(
        scores=None,
        band=None,
        model_answer_display=None,
        ai=None,
        band_bounds=bounds,
        factual_weights=weights,
        prompt_version=None,
        question_version_id=inp.question.version_id,
        provenance=None,
        attempts_used=0,
    )
    base.update(fields)
    return AssessmentResult(**base)


def no_answer_result(
    question_version_id: int,
    *,
    bounds: BandBounds = DEFAULT_BAND_BOUNDS,
    weights: FactualWeights = DEFAULT_FACTUAL_WEIGHTS,
) -> AssessmentResult:
    """The fixed Weak zero of "I don't know" or a skip; it needs no rubric and no AI call."""
    return AssessmentResult(
        status="no_answer",
        scores=Scores(correctness=0, completeness=0, factual=0, language=None, total=0),
        band="weak",
        model_answer_display=model_answer_display("weak", "no_answer"),
        ai=None,
        band_bounds=validate_bounds(bounds),
        factual_weights=validate_weights(weights),
        prompt_version=None,
        question_version_id=question_version_id,
        provenance=None,
        attempts_used=0,
    )


def assess(
    inp: AssessmentInput,
    generator: TextGenerator,
    *,
    operation_id: str,
    profile: LanguageProfile,
    bounds: BandBounds = DEFAULT_BAND_BOUNDS,
    weights: FactualWeights = DEFAULT_FACTUAL_WEIGHTS,
) -> AssessmentResult:
    """ProviderError propagates unchanged; invalid output twice raises AssessmentError.

    Neither path returns scores or a band, so a failure never becomes a Weak score.
    """
    bounds = validate_bounds(bounds)
    weights = validate_weights(weights)
    if inp.outcome in ("dont_know", "skipped"):
        return no_answer_result(inp.question.version_id, bounds=bounds, weights=weights)
    if not has_speech(inp):
        return _result(inp, bounds, weights, status="no_speech")

    messages = [Message(role="user", content=prompts.user_message(inp, profile=profile))]
    attempts = 0
    for op_id in limits.attempt_ids(operation_id):
        attempts += 1
        try:
            response = generator.generate_structured(
                ASSESS_TASK,
                prompts.assessment_system(inp.question.source_policy, profile=profile),
                messages,
                AiAssessment,
                operation_id=op_id,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except ProviderError as error:
            # Output that failed the schema (or was cut off) is invalid output too: one retry
            # with the same messages. Every other provider failure propagates unchanged.
            if error.kind != "invalid_output":
                raise
            errors = [f"output did not match the schema: {error.message}"]
            continue
        errors = validate_output(response.value, inp)
        if not errors:
            break
        messages = [
            *messages,
            Message(role="assistant", content=response.value.model_dump_json()),
            Message(role="user", content=retry_message(errors)),
        ]
    else:
        raise AssessmentError(FAILED_MESSAGE, errors)

    ai = clean_conflicts(response.value, inp)
    scores = compute_scores(ai, inp.question.rubric, weights)
    band = classify_band(scores.factual, bounds)
    return _result(
        inp,
        bounds,
        weights,
        status="assessed",
        scores=scores,
        band=band,
        model_answer_display=model_answer_display(band, "assessed"),
        ai=ai,
        prompt_version=prompts.ASSESSMENT_PROMPT_VERSION,
        provenance=response.provenance,
        attempts_used=attempts,
    )
