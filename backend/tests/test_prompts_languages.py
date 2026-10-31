"""No Polish scaffolding in any other language; every prompt names its language."""

import re

import pytest
from test_prompts_polish import CHANGES
from test_prompts_support import (
    Samples,
    make_metrics,
    make_none_metrics,
    make_question,
    render_cases,
)

from app.features.assessment.prompts import FILLER_EXAMPLES, VAGUE_PLACEHOLDERS
from app.features.follow_ups.prompts import JUDGEMENT_WORDS
from app.features.languages.profiles import PROFILES, SUPPORTED
from app.features.prompt_ab.loader import current_prompt_modules
from app.features.prompt_ab.prompt_sets import ProfilePromptSet
from app.features.questions.prompts import (
    EXTENSION_POLICY_NAMES,
    OPEN_QUESTION_OPENERS,
    POLICY_NAMES,
    policy_names,
)
from app.features.questions.schemas import Rubric
from app.features.topics.prompts import OBJECTIVE_VERBS, quoted

# The letters only Polish uses; ó is left out because Spanish has it ("Solo ampliación").
POLISH_ONLY = re.compile(r"[ąćęłńśźżĄĆĘŁŃŚŹŻ]")
OTHERS = [code for code in SUPPORTED if code != "pl"]

_EN_PASSAGES = [
    (201, "Athenian democracy developed in the fifth century BC around the citizen assembly."),
    (202, "Cleisthenes divided the citizens into ten tribes in 508 BC."),
    (203, "Ostracism let the citizens exile a man they saw as a danger to the state."),
]
_EN_RUBRIC = Rubric.model_validate(
    {
        "essential_points": [
            {
                "id": "p1",
                "text": "The assembly was the main organ of power.",
                "weight": 0.6,
                "alternatives": ["the ekklesia"],
                "passage_ids": [201],
                "basis": "source",
            },
            {
                "id": "p2",
                "text": "Cleisthenes created ten tribes.",
                "weight": 0.4,
                "passage_ids": [202],
                "basis": "expanded",
            },
        ],
        "misconceptions": ["Every resident of Athens could vote."],
        "major_error_rules": ["Crediting the reforms to Solon."],
        "model_answer": "The assembly held power, and Cleisthenes created ten tribes in 508 BC.",
        "beyond_material": ["The assembly met about forty times a year."],
    }
)
ENGLISH_SAMPLES = Samples(
    passages=_EN_PASSAGES,
    title="Athenian democracy",
    description="Institutions of classical Athens.",
    objectives=["Explain the role of the assembly.", "Discuss the reforms of Cleisthenes."],
    existing=["What was the Council of Five Hundred?"],
    slots=[("short", "recall"), ("open", "analysis")],
    section_topics=[
        ("s0t0", "The assembly", "How the citizens governed."),
        ("s0t1", "Ostracism", "Exile by vote."),
    ],
    chapter_topics=[("e7", "Reforms of Cleisthenes", "The ten tribes.")],
    rubric=_EN_RUBRIC,
    full_metrics=make_metrics(filler_examples=["um", "you know"]),
    none_metrics=make_none_metrics(),
    question=make_question(
        _EN_RUBRIC, _EN_PASSAGES, "Discuss the assembly and the reforms of Cleisthenes."
    ),
    transcript="Um, the assembly ruled Athens, and Cleisthenes made ten tribes in 510 BC.",
    points=[
        ("The assembly was the main organ of power.", "covered", "Clearly stated."),
        ("Cleisthenes created ten tribes.", "partial", "The date is off."),
    ],
    mistakes=[("in 510 BC", "The reforms date from 508 BC.")],
)

POLISH_TABLE_ENTRIES = [
    *OBJECTIVE_VERBS["pl"],
    *OPEN_QUESTION_OPENERS["pl"],
    *FILLER_EXAMPLES["pl"],
    VAGUE_PLACEHOLDERS["pl"],
    *JUDGEMENT_WORDS["pl"],
]


def flat(text: str) -> str:
    return re.sub(r"[ \n]+", " ", text)


def rendered(code: str) -> dict[str, str]:
    prompt_set = ProfilePromptSet(current_prompt_modules(), PROFILES[code])
    return render_cases(prompt_set, samples=ENGLISH_SAMPLES)


@pytest.mark.parametrize("code", OTHERS)
def test_no_polish_scaffolding(code):
    forbidden = [old for changes in CHANGES.values() for old, _ in changes]
    forbidden += list(POLICY_NAMES.values())
    # Each Polish example as the prompt quotes it; bare too, unless it is a short English word
    # as well ("no").
    pl = PROFILES["pl"]
    forbidden += [quoted(word, pl) for word in POLISH_TABLE_ENTRIES]
    forbidden += [word for word in POLISH_TABLE_ENTRIES if len(word) > 2]
    for name, text in rendered(code).items():
        assert not POLISH_ONLY.search(text), (name, POLISH_ONLY.findall(text))
        assert "Polish" not in text, name
        for old in forbidden:
            assert old not in text, (name, old)


@pytest.mark.parametrize("code", SUPPORTED)
def test_language_named_everywhere(code):
    profile = PROFILES[code]
    texts = rendered(code)
    names = policy_names(profile).values()
    for name, text in texts.items():
        if not name.endswith(".system"):
            continue
        assert f"in {profile.name_en}" in flat(text), name
        if name.startswith("assessment."):
            assert profile.register_label in flat(text), name
        for policy in names:
            assert text.count(policy) == text.count(quoted(policy, profile)), (name, policy)
    assert profile.unavailable_label in texts["assessment.assess_unavailable.user"]


TABLES = {
    "OBJECTIVE_VERBS": (OBJECTIVE_VERBS, ("Wyjaśnić…", "Omówić…", "Porównać…")),
    "OPEN_QUESTION_OPENERS": (OPEN_QUESTION_OPENERS, ("Omów…", "Wyjaśnij…", "Porównaj…")),
    "EXTENSION_POLICY_NAMES": (EXTENSION_POLICY_NAMES, ("Rozszerz temat", "Tylko poszerzenie")),
    "FILLER_EXAMPLES": (FILLER_EXAMPLES, ("no", "jakby", "wiesz")),
    "VAGUE_PLACEHOLDERS": (VAGUE_PLACEHOLDERS, "ta rzecz"),
    "JUDGEMENT_WORDS": (JUDGEMENT_WORDS, ("dobrze", "błędnie")),
}


@pytest.mark.parametrize("name", TABLES)
def test_tables_cover_every_language(name):
    table, polish = TABLES[name]
    assert set(table) == set(SUPPORTED)
    assert table["pl"] == polish


@pytest.mark.parametrize("name", TABLES)
def test_ukrainian_examples_are_cyrillic(name):
    table, _ = TABLES[name]
    entry = table["uk"]
    words = (entry,) if isinstance(entry, str) else entry
    for word in words:
        assert not re.search(r"[A-Za-z]", word), word
