"""A fixed English sample for the new prompts: original text written for this tool."""

from app.features.questions.schemas import QuestionWithRubric

ENGLISH_TITLE = "The Industrial Revolution in Britain"
ENGLISH_DESCRIPTION = "Why industry changed first in Britain, and what it meant for work."
ENGLISH_OBJECTIVES = [
    "Explain why the textile industry grew first.",
    "Describe how steam power changed factory work.",
]

ENGLISH_PASSAGES: list[tuple[int, str]] = [
    (
        1,
        "In eighteenth-century Britain, cotton cloth was in high demand at home and abroad, but "
        "spinning by hand was slow. Inventions such as the spinning jenny and the water frame let "
        "one worker produce many threads at once. Because the new machines were large and needed "
        "a steady source of power, owners began to gather them in mills beside fast rivers, and "
        "the textile trade became the first industry to be organised in factories.",
    ),
    (
        2,
        "James Watt improved the steam engine in the 1770s by adding a separate condenser, which "
        "saved a great deal of coal. Later engines turned wheels and shafts directly, so a mill "
        "no longer had to stand next to a river. Coal was plentiful in Britain, and canals and "
        "later railways carried it cheaply to the growing towns where steam-powered factories "
        "were built.",
    ),
    (
        3,
        "Factory work followed the rhythm of the machines rather than the seasons. Men, women and "
        "children worked shifts of twelve hours or more under strict rules and fines. Early "
        "reformers collected evidence of injuries and exhaustion, and Parliament passed the "
        "Factory Act of 1833, which limited the hours of children in textile mills and sent "
        "inspectors to check that the law was kept.",
    ),
]

# Negative ids mark samples: they never match a row of a database.
ENGLISH_QUESTION = QuestionWithRubric.model_validate(
    {
        "version_id": -1,
        "family_id": -1,
        "version": 1,
        "text": "Explain why the textile industry led the Industrial Revolution in Britain.",
        "style": "open",
        "difficulty": "analysis",
        "primary_topic_id": None,
        "objective_id": None,
        "status": "active",
        "suggested_prep_s": 30,
        "suggested_answer_s": 120,
        "source_policy": "materials",
        "rubric": {
            "essential_points": [
                {
                    "id": "p1",
                    "text": "Demand for cotton cloth was high and hand spinning was slow.",
                    "weight": 0.35,
                    "passage_ids": [1],
                },
                {
                    "id": "p2",
                    "text": "New spinning machines were gathered in powered mills.",
                    "weight": 0.35,
                    "passage_ids": [1],
                },
                {
                    "id": "p3",
                    "text": "Steam power and cheap coal let factories move to the towns.",
                    "weight": 0.3,
                    "passage_ids": [2, 3],
                },
            ],
            "misconceptions": ["The railways came before the first textile mills."],
            "major_error_rules": ["Placing the factory system before the new spinning machines."],
            "model_answer": (
                "Cotton cloth was in demand and hand spinning was slow, so machines such as the "
                "spinning jenny were invented. They needed power, so they were gathered in mills, "
                "and once Watt's steam engine and cheap coal arrived, factories spread to the "
                "towns."
            ),
        },
        "passages": [
            {"id": pid, "locator": f"p. {n}", "heading": None, "text": text}
            for n, (pid, text) in enumerate(ENGLISH_PASSAGES, start=1)
        ],
    }
)

ENGLISH_TRANSCRIPT = (
    "Um, so the textile industry came first because people wanted a lot of cotton cloth and "
    "spinning it by hand took ages. Then machines like the spinning jenny could make many "
    "threads at once, and they were put together in big mills by rivers. Later James Watt made "
    "the steam engine better in 1850, so the mills could be built in towns near the coal, and "
    "that whole thing kind of spread to other industries too."
)

# A fixed judgement of the transcript for the follow-up: (rubric text, status, note) and
# (learner's quote, correction).
ENGLISH_POINTS: list[tuple[str, str, str]] = [
    (ENGLISH_QUESTION.rubric.essential_points[0].text, "covered", "Stated clearly."),
    (ENGLISH_QUESTION.rubric.essential_points[1].text, "covered", "Mills by rivers named."),
    (ENGLISH_QUESTION.rubric.essential_points[2].text, "partial", "The date is wrong."),
]
ENGLISH_MISTAKES: list[tuple[str, str]] = [
    ("in 1850", "Watt improved the steam engine in the 1770s."),
]
