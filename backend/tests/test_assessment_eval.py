import re

import pytest
from test_assessment_support import ai

from app.features.assessment import eval as assessment_eval
from app.features.assessment.schemas import LanguageIssue, TranscriptionDoubt
from app.features.assessment.service import ASSESS_TASK
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import RecordingTextGenerator
from app.features.providers.types import ProviderError

HEADINGS = [
    "### 1. What was correct",
    "### 2. Factual mistakes",
    "### 3. What was missing",
    "### 4. Additional details",
    "### 5. Language and wording",
    "### 6. Exam answer style",
    "### 7. Reaction time and fluency",
    "### 8. A better version of the answer",
    "### 9. Model answer",
]


def _canned(messages):
    """A valid assessment for whichever golden question the message carries."""
    four = '"id":"p4"' in messages[0].content
    statuses = ("covered", "missing", "missing", "covered") if four else ("covered", "partial")
    return ai(
        statuses,
        correctness=80,
        language=75,
        language_issues=[
            LanguageIssue(
                dimension="formality", quote="jakby", suggestion="—", explanation="A filler."
            )
        ],
        transcription_doubts=[TranscriptionDoubt(quote="wetto", likely_meant="veto", reason="…")],
    )


class Factory:
    def __init__(self, error: ProviderError | None = None):
        self.fake = FakeTextGenerator({ASSESS_TASK: _canned}, error=error)

    def __call__(self, settings, recorder):
        return RecordingTextGenerator(lambda: self.fake, "openai", "fake", "assessment", recorder)


def test_parse_args_case_repeatable(tmp_path):
    args = assessment_eval.parse_args(["--case", "bio-strong", "--case", "hist-empty"])
    assert args.case == ["bio-strong", "hist-empty"] and args.out is None
    args = assessment_eval.parse_args(["--out", str(tmp_path)])
    assert args.case == [] and args.out == tmp_path


def test_unknown_case_exits_2_with_message(settings, capsys):
    factory = Factory()
    code = assessment_eval.main(
        ["--case", "no-such-case"], settings=settings, generator_factory=factory
    )
    assert code == 2
    assert "Unknown case: no-such-case" in capsys.readouterr().out
    assert factory.fake.calls == []


def test_selected_case_pulls_in_its_pair_base(settings, tmp_path, capsys):
    factory = Factory()
    code = assessment_eval.main(
        ["--case", "bio-casual", "--out", str(tmp_path)],
        settings=settings,
        generator_factory=factory,
    )
    assert code == 0
    assert len(factory.fake.calls) == 2  # bio-formal and bio-casual
    report = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "| bio-formal |" in report and "| bio-casual |" in report
    assert "| bio-strong |" not in report


def test_full_report(settings, tmp_path, capsys):
    factory = Factory()
    code = assessment_eval.main(
        ["--out", str(tmp_path)], settings=settings, generator_factory=factory
    )
    assert code == 0  # even though some cases fail with these canned answers
    printed = capsys.readouterr().out.strip().splitlines()
    (path,) = list(tmp_path.glob("*.md"))
    assert printed[-1] == str(path)
    assert re.fullmatch(r"\d{8}-\d{6}\.md", path.name)
    assert path.parent == tmp_path
    assert len(factory.fake.calls) == 11  # hist-empty makes no call
    report = path.read_text(encoding="utf-8")
    assert "Provider: openai, model: gpt-6.1-sol, prompt version: assessment-v3" in report
    assert "AI calls: 11; tokens: input 0, output 0" in report
    assert "| Case | Result | Band | Factual | Language | AI calls | Notes |" in report
    assert "| hist-empty | PASSED | none (no_speech) | — | — | 0 | — |" in report
    assert "| bio-strong | FAILED |" in report
    for case_id in assessment_eval.FEEDBACK_CASES:
        start = report.index(f"## Full feedback: {case_id}")
        section = report[start:].split("\n## ", 1)[0]
        positions = [section.index(h) for h in HEADINGS]
        assert positions == sorted(positions)
    assert report.count("## Full feedback:") == 4
    partial = report[report.index("## Full feedback: bio-partial") :]
    assert "p2: Źródłem energii jest światło" in partial  # missing point, rubric text
    assert "“jakby” → —" in partial
    assert "Possible speech-recognition error: “wetto” (probably: “veto”)" in partial
    assert "Fluency: Cannot be assessed reliably" in partial
    assert "Fotosynteza to proces, w którym rośliny wytwarzają związki organiczne" in partial


def test_provider_errors_are_reported_not_raised(settings, tmp_path):
    error = ProviderError("rate_limit", "openai", "Too many requests.")
    factory = Factory(error=error)
    code = assessment_eval.main(
        ["--case", "hist-misheard", "--out", str(tmp_path)],
        settings=settings,
        generator_factory=factory,
    )
    assert code == 0
    report = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "| hist-misheard | FAILED | — | — | — | — | provider error (rate_limit)" in report
    assert "Error: provider error (rate_limit): Too many requests." in report


def test_missing_key_exits_1(settings, capsys):
    # Keys cleared explicitly: an environment key must never turn this into a live call.
    keyless = settings.model_copy(update={"openai_api_key": None, "anthropic_api_key": None})
    code = assessment_eval.main(["--case", "bio-strong"], settings=keyless)
    assert code == 1
    assert "No API key" in capsys.readouterr().out


def test_default_out_dir_under_data(settings, capsys):
    assert (
        assessment_eval.main(
            ["--case", "hist-empty"], settings=settings, generator_factory=Factory()
        )
        == 0
    )
    (path,) = list((settings.data_dir / "assessment-eval").glob("*.md"))
    assert capsys.readouterr().out.strip().endswith(str(path))


@pytest.mark.parametrize("case_id", ["bio-strong", "hist-empty"])
def test_select_cases_without_pairs(case_id):
    cases = assessment_eval.load_cases()
    assert [c.id for c in assessment_eval.select_cases(cases, [case_id])] == [case_id]
