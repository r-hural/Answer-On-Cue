"""Fixed prompt inputs, the rendered prompt cases and the Polish snapshots (no tests here)."""

from dataclasses import dataclass
from pathlib import Path

from app.features.assessment.schemas import AssessmentInput
from app.features.delivery.schemas import AnswerMetrics, PauseMarker, Thresholds
from app.features.questions.schemas import QuestionWithRubric, Rubric

MODULES = ("topics", "questions", "assessment", "follow_ups")
SNAPSHOTS = Path(__file__).parent / "fixtures" / "prompts" / "pl-before"


@dataclass(frozen=True)
class Samples:
    passages: list[tuple[int, str]]
    title: str
    description: str
    objectives: list[str]
    existing: list[str]
    slots: list[tuple[str, str]]
    section_topics: list[tuple[str, str, str]]
    chapter_topics: list[tuple[str, str, str]]
    rubric: Rubric
    full_metrics: AnswerMetrics
    none_metrics: AnswerMetrics
    question: QuestionWithRubric
    transcript: str
    points: list[tuple[str, str, str]]
    mistakes: list[tuple[str, str]]


def make_metrics(**over) -> AnswerMetrics:
    base = dict(
        preparation_used_ms=4000,
        speech_start_latency_ms=1200,
        total_response_latency_ms=5200,
        answer_duration_ms=60000,
        first_to_last_speech_ms=57000,
        speech_ms=48000,
        leading_silence_ms=1200,
        trailing_silence_ms=1800,
        internal_silence_ms=9000,
        silence_ratio=0.15,
        longest_internal_silence_ms=2700,
        long_pause_count=1,
        very_long_pause_count=0,
        long_pauses_per_min=1.0,
        words=130,
        words_per_minute=130.0,
        filler_count=3,
        filler_per_min=3.0,
        filler_examples=["yyy", "jakby"],
        fillers_lower_bound=True,
        pauses=[PauseMarker(start_ms=4200, end_ms=6900, kind="long")],
        flags=[],
        unavailable={},
        approximate=True,
        interrupted=False,
        detector="silero",
        thresholds=Thresholds(),
    )
    base.update(over)
    return AnswerMetrics(**base)


def make_none_metrics() -> AnswerMetrics:
    return make_metrics(
        preparation_used_ms=3000,
        speech_start_latency_ms=None,
        total_response_latency_ms=None,
        answer_duration_ms=20000,
        first_to_last_speech_ms=None,
        speech_ms=15000,
        leading_silence_ms=None,
        trailing_silence_ms=None,
        internal_silence_ms=None,
        silence_ratio=None,
        longest_internal_silence_ms=None,
        long_pause_count=None,
        very_long_pause_count=None,
        long_pauses_per_min=None,
        words=None,
        words_per_minute=None,
        filler_count=None,
        filler_per_min=None,
        filler_examples=[],
        pauses=[],
        approximate=False,
    )


def make_question(
    rubric: Rubric, passages: list[tuple[int, str]], text: str, policy: str = "materials"
) -> QuestionWithRubric:
    return QuestionWithRubric.model_validate(
        {
            "version_id": 11,
            "family_id": 11,
            "version": 1,
            "text": text,
            "style": "open",
            "difficulty": "analysis",
            "primary_topic_id": 3,
            "objective_id": None,
            "status": "active",
            "suggested_prep_s": 30,
            "suggested_answer_s": 120,
            "source_policy": policy,
            "rubric": rubric.model_dump(),
            "passages": [
                {"id": pid, "locator": f"s. {n}", "heading": None, "text": body}
                for n, (pid, body) in enumerate(passages, start=1)
            ],
        }
    )


_PL_PASSAGES = [
    (
        101,
        "Demokracja ateńska rozwinęła się w V wieku p.n.e. Głównym organem była "
        "eklezja, w której zasiadali pełnoprawni obywatele.",
    ),
    (
        102,
        "Reformy Klejstenesa z 508 roku p.n.e. podzieliły obywateli na dziesięć fyl. Każda "
        "fyla wybierała pięćdziesięciu członków Rady Pięciuset.",
    ),
    (
        103,
        "Ostracyzm pozwalał wygnać z miasta obywatela uznanego za groźnego dla ustroju. "
        "Głosowano, pisząc imię na skorupie.",
    ),
]

_PL_RUBRIC = Rubric.model_validate(
    {
        "essential_points": [
            {
                "id": "p1",
                "text": "Eklezja była głównym organem władzy.",
                "weight": 0.6,
                "alternatives": ["eklezja jako główny organ"],
                "passage_ids": [101],
                "basis": "source",
            },
            {
                "id": "p2",
                "text": "Klejstenes podzielił obywateli na dziesięć fyl.",
                "weight": 0.4,
                "passage_ids": [102],
                "basis": "expanded",
            },
        ],
        "enrichment_points": [
            {"text": "Perykles wprowadził diety dla sędziów.", "passage_ids": [103]}
        ],
        "misconceptions": ["Wszyscy mieszkańcy Aten mieli prawa polityczne."],
        "major_error_rules": ["Reformy Klejstenesa przypisane Solonowi."],
        "model_answer": (
            "Eklezja była głównym organem demokracji ateńskiej, a reformy Klejstenesa "
            "z 508 roku p.n.e. podzieliły obywateli na dziesięć fyl."
        ),
        "beyond_material": ["Eklezja zbierała się kilkadziesiąt razy w roku."],
    }
)

SAMPLES = Samples(
    passages=_PL_PASSAGES,
    title="Demokracja ateńska",
    description="Instytucje i procedury ustroju Aten w epoce klasycznej.",
    objectives=["Wyjaśnić rolę eklezji w ustroju Aten.", "Omówić reformy Klejstenesa."],
    existing=["Czym była Rada Pięciuset?"],
    slots=[("short", "recall"), ("open", "analysis")],
    section_topics=[
        ("s0t0", "Eklezja", "Rola zgromadzenia obywateli w Atenach."),
        ("s0t1", "Ostracyzm", "Procedura wygnania obywatela groźnego dla ustroju."),
    ],
    chapter_topics=[("e7", "Reformy Klejstenesa", "Podział obywateli na fyle i Rada Pięciuset.")],
    rubric=_PL_RUBRIC,
    full_metrics=make_metrics(),
    none_metrics=make_none_metrics(),
    question=make_question(
        _PL_RUBRIC,
        _PL_PASSAGES,
        "Omów rolę eklezji i reform Klejstenesa w demokracji ateńskiej.",
    ),
    transcript=(
        "Eklezja była główną instytucją Aten, a Klejstenes podzielił obywateli na dziesięć "
        "fyl w 510 roku."
    ),
    points=[
        ("Eklezja była głównym organem władzy.", "covered", "Opis roli eklezji jest właściwy."),
        ("Klejstenes podzielił obywateli na dziesięć fyl.", "partial", "Data reform jest błędna."),
    ],
    mistakes=[("w 510 roku", "Reformy Klejstenesa przeprowadzono w 508 roku p.n.e.")],
)


def _assessment_input(samples: Samples, **over) -> AssessmentInput:
    base = dict(
        question=samples.question,
        transcript=samples.transcript,
        outcome="answered",
        delivery=samples.full_metrics,
    )
    base.update(over)
    return AssessmentInput(**base)


def _cases(prompt_set, module: str, s: Samples) -> dict[str, tuple[str, str]]:
    if module == "topics":
        return {
            "extract": prompt_set.extract_section(s.passages),
            "consolidate": prompt_set.consolidate(s.section_topics),
            "consolidate_chapter": prompt_set.consolidate_chapter(
                s.section_topics, s.chapter_topics
            ),
        }
    if module == "questions":

        def generate(policy: str, existing: list[str]) -> tuple[str, str]:
            return prompt_set.generate(
                policy, s.title, s.description, s.objectives, s.passages, existing, s.slots
            )

        questions = [(s.question.text, s.rubric)]
        return {
            "generate_materials": generate("materials", s.existing),
            "generate_expanded": generate("expanded", s.existing),
            "generate_outside": generate("outside", s.existing),
            "generate_no_existing": generate("materials", []),
            "verify_materials": prompt_set.verify("materials", s.passages, questions),
            "verify_expanded": prompt_set.verify("expanded", s.passages, questions),
        }
    if module == "assessment":

        def with_policy(policy: str) -> QuestionWithRubric:
            return s.question.model_copy(update={"source_policy": policy})

        interrupted = s.full_metrics.model_copy(update={"interrupted": True})
        bare = s.question.model_copy(update={"passages": []})
        return {
            "assess_materials": prompt_set.assess(_assessment_input(s)),
            "assess_expanded": prompt_set.assess(
                _assessment_input(s, question=with_policy("expanded"))
            ),
            "assess_outside": prompt_set.assess(
                _assessment_input(s, question=with_policy("outside"))
            ),
            "assess_interrupted": prompt_set.assess(
                _assessment_input(s, delivery=interrupted, attempt_kind="retry", interrupted=True)
            ),
            "assess_no_delivery": prompt_set.assess(
                _assessment_input(s, question=bare, delivery=None)
            ),
            "assess_unavailable": prompt_set.assess(_assessment_input(s, delivery=s.none_metrics)),
        }
    if module == "follow_ups":

        def follow_up(policy: str, passages, points, mistakes) -> tuple[str, str]:
            return prompt_set.follow_up(
                policy, s.question.text, s.rubric, passages, s.transcript, points, mistakes
            )

        return {
            "follow_up_materials": follow_up("materials", s.passages, s.points, s.mistakes),
            "follow_up_expanded": follow_up("expanded", s.passages, s.points, s.mistakes),
            "follow_up_outside": follow_up("outside", s.passages, s.points, s.mistakes),
            "follow_up_empty": follow_up("materials", [], [], []),
        }
    raise ValueError(module)


def render_cases(prompt_set, modules=MODULES, samples: Samples = SAMPLES) -> dict[str, str]:
    """Every case as "<module>.<case>.system" and "<module>.<case>.user"."""
    rendered: dict[str, str] = {}
    for module in modules:
        for case, (system, user) in _cases(prompt_set, module, samples).items():
            rendered[f"{module}.{case}.system"] = system
            rendered[f"{module}.{case}.user"] = user
    return rendered


def module_of(name: str) -> str:
    return name.split(".", 1)[0]


def capture(prompt_set) -> None:
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    for name, text in render_cases(prompt_set).items():
        (SNAPSHOTS / f"{name}.txt").write_text(text, encoding="utf-8", newline="\n")


def snapshot_names() -> list[str]:
    return sorted(path.name.removesuffix(".txt") for path in SNAPSHOTS.glob("*.txt"))


def read_snapshot(name: str) -> str:
    return (SNAPSHOTS / f"{name}.txt").read_text(encoding="utf-8")
