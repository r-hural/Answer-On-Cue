"""Builders for the chapters tests (no tests here); the older support files stay unedited."""

import importlib.util
import re

from alembic.config import Config
from sqlalchemy import text
from test_topics_support import BACKEND

BASE_REVISION = "0012_audio_retention"


def migration_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    return cfg


def chapters_migration():
    path = BACKEND / "migrations" / "versions" / "0013_chapters.py"
    spec = importlib.util.spec_from_file_location("migration_0013_chapters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw_subject(conn, name: str) -> int:
    return conn.execute(
        text("INSERT INTO subject (name) VALUES (:name) RETURNING id"), {"name": name}
    ).scalar_one()


def raw_document(conn, subject_id: int, title: str, passages: int = 2) -> tuple[int, list[int]]:
    """A document with one imported version and its passages; returns (document id, passages)."""
    document_id = conn.execute(
        text("INSERT INTO document (subject_id, title) VALUES (:s, :t) RETURNING id"),
        {"s": subject_id, "t": title},
    ).scalar_one()
    version_id = conn.execute(
        text(
            "INSERT INTO document_version (document_id, version, filename, stored_path, mime,"
            " size_bytes, sha256, status, extracted_text, char_count)"
            " VALUES (:d, 1, 'a.txt', 'documents/a.txt', 'text/plain', 1, :sha, 'imported',"
            " 'x', 1) RETURNING id"
        ),
        {"d": document_id, "sha": "0" * 64},
    ).scalar_one()
    ids = [
        conn.execute(
            text(
                "INSERT INTO source_passage (document_version_id, ordinal, locator, text,"
                " char_start, char_end, excluded) VALUES (:v, :o, 'l', 'tekst', 0, 5, 0)"
                " RETURNING id"
            ),
            {"v": version_id, "o": n},
        ).scalar_one()
        for n in range(passages)
    ]
    return document_id, ids


def raw_topic(conn, subject_id: int, title: str, passage_ids=(), status: str = "active") -> int:
    topic_id = conn.execute(
        text(
            "INSERT INTO topic (subject_id, title, ordinal, origin, status)"
            " VALUES (:s, :t, 0, 'ai', :st) RETURNING id"
        ),
        {"s": subject_id, "t": title, "st": status},
    ).scalar_one()
    for pid in passage_ids:
        conn.execute(
            text("INSERT INTO topic_passage (topic_id, passage_id) VALUES (:t, :p)"),
            {"t": topic_id, "p": pid},
        )
    return topic_id


def chapters_of(conn, subject_id: int) -> list[tuple]:
    return list(
        conn.execute(
            text(
                "SELECT id, name, is_default, ordinal FROM chapter WHERE subject_id = :s"
                " ORDER BY id"
            ),
            {"s": subject_id},
        )
    )


def chapter_of(conn, table: str, row_id: int) -> int | None:
    return conn.execute(
        text(f"SELECT chapter_id FROM {table} WHERE id = :i"), {"i": row_id}
    ).scalar_one()


def upload(client, subject_id: int, filename: str, status: int = 201, **form) -> dict:
    """Uploads a small text file; form fields are sent only when given."""
    response = client.post(
        f"/api/subjects/{subject_id}/documents",
        files={"file": (filename, f"Treść pliku {filename}.".encode(), "text/plain")},
        data={k: str(v) for k, v in form.items()},
    )
    assert response.status_code == status, response.text
    return response.json()


def add_job(db, kind: str, subject_id: int, version_id: int | None, status: str, params=None):
    from app.features.jobs import service as jobs

    job = jobs.create_job(db, kind, subject_id, params or {}, 1, document_version_id=version_id)
    job.status = status
    db.commit()
    return job


def no_writes(call) -> None:
    from sqlalchemy import Engine, event

    seen = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement.lstrip().split()[0].upper())

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    assert not {"INSERT", "UPDATE", "DELETE"} & set(seen), seen


def books_world(db) -> dict:
    """ "Historia PL": the default chapter, book A (Greece) and book B (Revolution), 4 passages
    each; topics TA1, TA2 (A), TB1 (B), TM (2 A + 1 B), TN (none), TR (removed, B only)."""
    from sqlalchemy import select
    from test_topics_support import make_question, make_subject, make_topic, make_version

    from app.features.library.models import Chapter

    subject = make_subject(db, "Historia PL")
    a, pa = make_version(db, subject.id, count=4, excluded=())
    b, pb = make_version(db, subject.id, count=4, excluded=())
    a.document.title, b.document.title = "Starożytna Grecja", "Rewolucja francuska"
    db.commit()
    w = {"subject": subject, "A": a, "B": b, "pa": pa, "pb": pb}
    w["default"] = db.scalars(select(Chapter.id).where(Chapter.subject_id == subject.id)).one()
    w["TA1"] = make_topic(db, subject.id, "Polis", pa[:2], ordinal=0)
    w["TA2"] = make_topic(db, subject.id, "Ateny", pa[2:], ordinal=1)
    w["TB1"] = make_topic(db, subject.id, "Stany Generalne", pb[:3], ordinal=2)
    w["TM"] = make_topic(db, subject.id, "Demokracja", [pa[0], pa[1], pb[3]], ordinal=3)
    w["TN"] = make_topic(db, subject.id, "Bez fragmentów", ordinal=4)
    w["TR"] = make_topic(db, subject.id, "Usunięty", pb[:1], ordinal=5)
    w["TR"].status = "removed"
    db.commit()
    w["questions_TB1"] = [
        make_question(
            db,
            subject.id,
            w["TB1"].id,
            "Omów zwołanie Stanów Generalnych.",
            passage_ids=(pb[0].id,),
        ),
        make_question(
            db, subject.id, w["TB1"].id, "Wyjaśnij przyczyny rewolucji.", passage_ids=(pb[1].id,)
        ),
    ]
    w["TM_cites_B"] = make_question(
        db,
        subject.id,
        w["TM"].id,
        "Porównaj demokrację ateńską i rewolucyjną.",
        passage_ids=(pb[3].id,),
    )
    w["TM_cites_A"] = make_question(
        db, subject.id, w["TM"].id, "Omów demokrację w Atenach.", passage_ids=(pa[0].id,)
    )
    return w


E_REF_RE = re.compile(r"\[(e\d+)\]")


def fake_chapter_consolidation(messages):
    """Attaches the first section topic to the first [e…] topic; keeps the rest new."""
    from test_topics_support import REF_RE

    from app.features.topics.prompts import ConsolidatedTopic, Consolidation

    refs = [r for msg in messages for r in REF_RE.findall(msg.content)]
    existing = [r for msg in messages for r in E_REF_RE.findall(msg.content)]
    topics = []
    if existing and refs:
        topics.append(
            ConsolidatedTopic(
                title="Dołączony", description="Opis", merged_from=[refs[0], existing[0]]
            )
        )
        refs = refs[1:]
    topics += [
        ConsolidatedTopic(title=f"Nowy {n}", description="Opis", merged_from=[ref])
        for n, ref in enumerate(refs)
    ]
    return Consolidation(topics=topics)


def weak_extension_round(
    client, db, *, name: str = "Historia", question_policy: str = "materials", **config
):
    """A finished round with an extension offer: topic 0 Weak, the others Good, no new
    questions in the bank (one question per objective, all asked)."""
    from datetime import UTC, datetime

    from test_adaptive_support import answer_item, build_world

    from app.features.rounds.models import Round, RoundItem

    world = build_world(db, name=name, per_objective=(1, 1, 1))
    for items in world.questions.values():
        for question in items:
            question.source_policy = question_policy
    db.commit()
    now = datetime.now(UTC)
    body = {
        "subject_id": world.subject.id,
        "config": {
            "question_count": 3,
            "style": "short",
            "scope": {"kind": "subject"},
            "extension": {"enabled": True, "question_count": 3},
            **config,
        },
    }
    response = client.post("/api/rounds", json=body)
    assert response.status_code == 201, response.text
    round_id = response.json()["id"]
    items = list(
        db.query(RoundItem).filter_by(round_id=round_id, role="main").order_by(RoundItem.ordinal)
    )
    for item in items:
        weak = world.objective_of(item.question_version_id) == 0
        answer_item(db, item, at=now, band="weak" if weak else "good")
    db.expire_all()
    row = db.get(Round, round_id)
    row.status, row.phase, row.finished_at = "finished", "summary", now
    row.version += 1
    db.commit()
    return world, round_id


def two_chapter_world(db) -> dict:
    """Two chapters: book A with topic TA (default chapter), book B with topic TB ("Rewolucja
    francuska"), 4 active materials questions on each topic, citing their book."""
    from sqlalchemy import select
    from test_topics_support import make_question, make_subject, make_topic, make_version

    from app.features.library.models import Chapter

    subject = make_subject(db, "Historia PL")
    a, pa = make_version(db, subject.id, count=4, excluded=())
    b, pb = make_version(db, subject.id, count=4, excluded=())
    default = db.scalars(select(Chapter.id).where(Chapter.subject_id == subject.id)).one()
    second = Chapter(subject_id=subject.id, name="Rewolucja francuska", is_default=False, ordinal=1)
    db.add(second)
    db.commit()
    b.document.chapter_id = second.id
    ta = make_topic(db, subject.id, "Ateny", pa, ordinal=0)
    tb = make_topic(db, subject.id, "Paryż", pb, ordinal=1)
    tb.chapter_id = second.id
    db.commit()
    w = {"subject": subject, "A": a, "B": b, "default": default, "second": second.id,
         "TA": ta, "TB": tb}  # fmt: skip
    for key, topic, passages in (("questions_A", ta, pa), ("questions_B", tb, pb)):
        w[key] = [
            make_question(
                db,
                subject.id,
                topic.id,
                f"Pytanie {n + 1} o temacie {topic.title}.",
                passage_ids=(passages[n].id,),
            )  # fmt: skip
            for n in range(4)
        ]
    return w
