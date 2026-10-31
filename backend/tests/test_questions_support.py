"""Fakes for the question generation tests (no tests here)."""

import re

from test_topics_support import passage_ids_in

from app.features.questions.prompts import (
    GeneratedBatch,
    GeneratedQuestion,
    Verification,
    VerificationItem,
)
from app.features.questions.schemas import EssentialPoint, Rubric

SLOT_RE = re.compile(r"Question (\d+): style=(\w+), difficulty=(\w+)")
QUESTION_RE = re.compile(r'<question index="(\d+)">')

# Deliberately unlike each other, so near-duplicate detection keeps them all.
TEXTS = [
    "Omów genezę demokracji ateńskiej i rolę reform Klejstenesa.",
    "Kim byli heloci i jaką pozycję zajmowali w Sparcie?",
    "Wyjaśnij, czym był ostracyzm i w jakim celu go stosowano.",
    "Porównaj ustrój Sparty z ustrojem Aten w okresie klasycznym.",
    "Jakie uprawnienia miało zgromadzenie ludowe eklezja?",
    "Scharakteryzuj przyczyny wojny peloponeskiej.",
    "Na czym polegała reforma Solona dotycząca długów?",
    "Przedstaw znaczenie bitwy pod Maratonem dla Greków.",
    "Opisz funkcje Rady Pięciuset w ateńskim państwie.",
    "Dlaczego Peryklesa uważa się za symbol złotego wieku Aten?",
    "Jak wyglądało wychowanie młodych Spartan w agoge?",
    "Wymień najważniejsze cechy greckiej polis jako wspólnoty.",
    "Jaką rolę odgrywały kolonie greckie w handlu śródziemnomorskim?",
    "Omów stosunek Ateńczyków do metojków i niewolników.",
]


class QuestionFaker:
    """Answers questions.generate with one question per requested slot, citing given passages."""

    def __init__(self, texts: list[str] | None = None) -> None:
        self.texts = list(texts or TEXTS)
        self.used = 0

    def next_text(self) -> str:
        text = self.texts[self.used % len(self.texts)]
        self.used += 1
        return text

    def __call__(self, messages) -> GeneratedBatch:
        ids = passage_ids_in(messages)
        slots = [m for msg in messages for m in SLOT_RE.findall(msg.content)]
        return GeneratedBatch(
            questions=[
                GeneratedQuestion(
                    text=self.next_text(),
                    style=style,
                    difficulty=difficulty,
                    objective_index=0,
                    rubric=rubric(ids),
                    suggested_prep_s=20,
                    suggested_answer_s=60 if style == "short" else 120,
                )
                for _, style, difficulty in slots
            ]
        )


def rubric(ids: list[int], weights: tuple[float, ...] = (2.0, 1.0, 1.0)) -> Rubric:
    return Rubric(
        essential_points=[
            EssentialPoint(id=f"p{n + 1}", text=f"Punkt {n + 1}", weight=w, passage_ids=ids[:2])
            for n, w in enumerate(weights)
        ],
        model_answer="Zwięzła odpowiedź modelowa.",
    )


def all_good(messages) -> Verification:
    count = len([m for msg in messages for m in QUESTION_RE.findall(msg.content)])
    return Verification(
        items=[
            VerificationItem(index=i, answerable=True, grounded=True, note="") for i in range(count)
        ]
    )
