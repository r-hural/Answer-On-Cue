"""Polish prompts change only by the listed substitutions of the snapshots."""

import pytest
from test_prompts_support import module_of, read_snapshot, render_cases, snapshot_names

from app.features.assessment.prompts import ASSESSMENT_PROMPT_VERSION
from app.features.follow_ups.prompts import FOLLOW_UP_PROMPT_VERSION
from app.features.languages.profiles import PROFILES
from app.features.prompt_ab.loader import current_prompt_modules
from app.features.prompt_ab.prompt_sets import ProfilePromptSet
from app.features.questions.prompts import QUESTIONS_PROMPT_VERSION
from app.features.topics.prompts import TOPICS_PROMPT_VERSION

TOPICS_CHANGES = [
    ("Fragmenty dokumentu:\n", "Document passages:\n"),
    ("\n\nZaproponuj tematy dla tej części.", "\n\nPropose topics for this section."),
    ("Tematy z poszczególnych części:\n", "Section topics:\n"),
    ("\n\nIstniejące tematy rozdziału:\n", "\n\nExisting topics of the chapter:\n"),
]
QUESTIONS_CHANGES = [
    ("Polityka źródeł: ", "Source policy: "),
    ("Cele nauczania:", "Learning objectives:"),
    ("Fragmenty źródłowe:", "Source passages:"),
    (
        "Istniejące pytania (nie powtarzaj ich ani nie parafrazuj):",
        "Existing questions (do not repeat or rephrase them):",
    ),
    ("(brak)", "(none)"),
    ("Liczba pytań do napisania: ", "Number of questions to write: "),
    ("\nPytanie ", "\nQuestion "),
    (": styl=", ": style="),
    (", trudność=", ", difficulty="),
    ("Pytania do sprawdzenia:", "Questions to check:"),
    ("(fragmenty: ", "(passages: "),
    ("; podstawa: ", "; basis: "),
    ("\nPunkty kluczowe:\n", "\nEssential points:\n"),
    ("\nOdpowiedź modelowa: ", "\nModel answer: "),
    ("\nPoza materiałami: ", "\nBeyond the materials: "),
]
ASSESSMENT_CHANGES = [
    ("formal exam Polish", "formal spoken exam Polish"),
    ("<delivery>brak</delivery>", "<delivery>none</delivery>"),
    (
        "Kryteria oceny (major_error_rules są numerowane od 0):",
        "Rubric (major_error_rules are numbered from 0):",
    ),
    ("Fragmenty źródłowe:", "Source passages:"),
    ("(brak)", "(none)"),
    ("Zmierzone dane o wypowiedzi:", "Measured delivery data:"),
    ("Transkrypcja odpowiedzi:", "Answer transcript:"),
    ("- czas przygotowania: ", "- preparation time: "),
    ("- opóźnienie rozpoczęcia mowy: ", "- speech start latency: "),
    ("- całkowite opóźnienie odpowiedzi: ", "- total response latency: "),
    ("- czas odpowiedzi: ", "- answer duration: "),
    ("- czas mowy: ", "- speech time: "),
    ("- udział ciszy wewnętrznej: ", "- internal silence ratio: "),
    ("- najdłuższa pauza wewnętrzna: ", "- longest internal pause: "),
    ("- długie pauzy na minutę: ", "- long pauses per minute: "),
    ("- bardzo długie pauzy: ", "- very long pauses: "),
    ("- długie pauzy: ", "- long pauses: "),
    ("- tempo: ", "- pace: "),
    (" słów/min", " words/min"),
    ("- wtrącenia (wypełniacze): ", "- fillers: "),
    (
        " (dolna granica: rozpoznawanie mowy może pomijać wtrącenia)",
        " (lower bound: speech recognition can omit fillers)",
    ),
    ("- przykłady wtrąceń: brak", "- filler examples: none"),
    ("- przykłady wtrąceń: ", "- filler examples: "),
    ("- zmierzone pauzy: brak", "- measured pauses: none"),
    ("- zmierzone pauzy: ", "- measured pauses: "),
    ("- odpowiedź przerwana: tak", "- answer interrupted: yes"),
    ("- odpowiedź przerwana: nie", "- answer interrupted: no"),
    ("- wartości przybliżone (detektor mowy)", "- approximate values (speech detector)"),
]
FOLLOW_UPS_CHANGES = [
    ("Kryteria oceny pytania głównego:", "Rubric of the main question:"),
    ("Fragmenty źródłowe:", "Source passages:"),
    ("(brak)", "(none)"),
    ("Odpowiedź ucznia (transkrypcja):", "The learner's answer (transcript):"),
    ("Ocena punktów kluczowych:", "Judgement of the essential points:"),
    ("Błędy merytoryczne:", "Factual mistakes:"),
    ("Zadaj jedno pytanie dodatkowe.", "Ask one follow-up question."),
]
CHANGES = {
    "topics": TOPICS_CHANGES,
    "questions": QUESTIONS_CHANGES,
    "assessment": ASSESSMENT_CHANGES,
    "follow_ups": FOLLOW_UPS_CHANGES,
}
CONVERTED = ("topics", "questions", "assessment", "follow_ups")


def apply(changes: list[tuple[str, str]], text: str) -> str:
    for old, new in changes:
        text = text.replace(old, new)
    return text


@pytest.fixture(scope="module")
def rendered() -> dict[str, str]:
    prompt_set = ProfilePromptSet(current_prompt_modules(), PROFILES["pl"])
    return render_cases(prompt_set, modules=CONVERTED)


CONVERTED_NAMES = [name for name in snapshot_names() if module_of(name) in CONVERTED]


def test_all_snapshots_are_there():
    # An empty fixtures folder would otherwise skip the parametrized test silently.
    assert len(snapshot_names()) == 38


@pytest.mark.parametrize("name", CONVERTED_NAMES)
def test_polish_prompts_change_only_as_listed(name, rendered):
    assert rendered[name] == apply(CHANGES[module_of(name)], read_snapshot(name))


@pytest.mark.parametrize("module", CONVERTED)
def test_every_listed_change_is_used(module):
    texts = [read_snapshot(n) for n in snapshot_names() if module_of(n) == module]
    for old, _ in CHANGES[module]:
        assert any(old in text for text in texts), old


def test_versions():
    assert TOPICS_PROMPT_VERSION == "topics-v3"
    assert QUESTIONS_PROMPT_VERSION == "questions-v4"
    assert ASSESSMENT_PROMPT_VERSION == "assessment-v3"
    assert FOLLOW_UP_PROMPT_VERSION == "follow-up-v3"
