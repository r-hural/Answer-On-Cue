"""Seeds the e2e database for the document versions flow and prints
{"subject_id", "document_id", "round_id", "q1", "q2", "text"}.

Usage (from backend/): python ../frontend/e2e/seed_doc_versions.py "<subject name>"
The subject gets one imported text document of six Polish paragraphs (uploaded through the
library service into AOC_DATA_DIR), a topic linking all its passages, question Q1 citing
paragraph 2 and Q2 citing paragraph 4, and one finished practice round that answered Q2.
`text` is the version 1 text. Only the backend's services and test builders are imported;
nothing in backend/ is edited. The caller passes a new name each run.
"""

import json
import sys
from datetime import UTC, datetime

sys.path[:0] = [".", "tests"]

from test_coverage_support import make_answer, make_round  # noqa: E402
from test_topics_support import make_question, make_subject, make_topic  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.library import service as library  # noqa: E402
from app.features.library.models import Document  # noqa: E402
from app.features.recording import models as _recording_models  # noqa: E402,F401

SENTENCES = (
    "Demokracja ateńska opierała się na zgromadzeniu ludowym, w którym głosowali obywatele.",
    "Rada Pięciuset przygotowywała projekty ustaw dla zgromadzenia ludowego.",
    "Reformy Klejstenesa podzieliły obywateli na dziesięć fyl terytorialnych.",
    "Ostracyzm pozwalał wygnać z Aten polityka uznanego za groźnego dla demokracji.",
    "Sądy ludowe, czyli heliaja, rozstrzygały sprawy przy udziale setek sędziów.",
    "Perykles wprowadził diety dla obywateli pełniących urzędy publiczne.",
)


def paragraph(sentence: str) -> str:
    """About 700 characters, so every paragraph is a passage of its own."""
    return " ".join([sentence] * 9)


def main(name: str) -> None:
    settings = Settings()
    text = "\n\n".join(paragraph(s) for s in SENTENCES)
    with SessionLocal(settings.effective_database_url) as session:
        subject = make_subject(session, name)
        session.commit()
        document = library.create_document(
            session,
            settings,
            subject.id,
            "demokracja.txt",
            text.encode(),
            "Demokracja ateńska",
        )
        passages = list(session.get(Document, document.id).versions[-1].passages)
        assert len(passages) == len(SENTENCES), len(passages)
        topic = make_topic(session, subject.id, "Ustrój Aten", passages)
        q1 = make_question(
            session, subject.id, topic.id, "Jakie zadania miała Rada Pięciuset?",
            passage_ids=(passages[1].id,),
        )  # fmt: skip
        q2 = make_question(
            session, subject.id, topic.id, "Na czym polegał ostracyzm w Atenach?",
            passage_ids=(passages[3].id,),
        )  # fmt: skip
        at = datetime.now(UTC).replace(microsecond=0)
        round_ = make_round(session, subject.id, at=at)
        make_answer(session, round_, q2, at=at, band="good")
        session.commit()
        print(
            json.dumps(
                {
                    "subject_id": subject.id,
                    "document_id": document.id,
                    "round_id": round_.id,
                    "q1": q1.id,
                    "q2": q2.id,
                    "text": text,
                }
            )
        )


if __name__ == "__main__":
    main(sys.argv[1])
