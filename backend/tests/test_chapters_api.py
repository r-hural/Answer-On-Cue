import pytest
from sqlalchemy import select, text
from test_chapters_support import add_job, no_writes, upload
from test_reports_history import count_statements
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.library.models import Chapter, Document, DocumentVersion

NOT_FOUND = "Chapter not found."
DUPLICATE = "This subject already has a chapter with this name."
INVALID = "The chapter name must be 1 to 200 characters long."
DEFAULT = "The first chapter of a subject cannot be deleted."
NOT_EMPTY = "Move this chapter's documents and topics first."
MOVE_TARGET = "Choose a chapter or enter a new chapter name."
FOREIGN = "This chapter belongs to another subject."


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def subject(client, name: str = "Historia PL") -> dict:
    response = client.post("/api/subjects", json={"name": name, "language": "pl"})
    assert response.status_code == 201, response.text
    return response.json()


def chapters(client, subject_id: int) -> list[dict]:
    response = client.get(f"/api/subjects/{subject_id}/chapters")
    assert response.status_code == 200, response.text
    return response.json()


def new_chapter(client, subject_id: int, name: str) -> dict:
    response = client.post(f"/api/subjects/{subject_id}/chapters", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_rename_list_delete(client, db):
    s = subject(client)
    default = chapters(client, s["id"])[0]
    body = new_chapter(client, s["id"], "Rewolucja francuska")
    assert (body["name"], body["is_default"], body["ordinal"]) == ("Rewolucja francuska", False, 1)
    assert body["subject_id"] == s["id"]
    assert (body["document_count"], body["topic_count"], body["question_count"]) == (0, 0, 0)

    again = client.post(f"/api/subjects/{s['id']}/chapters", json={"name": " REWOLUCJA francuska"})
    assert again.status_code == 409 and again.json()["detail"] == DUPLICATE
    for bad in ("", "   ", "x" * 201):
        response = client.post(f"/api/subjects/{s['id']}/chapters", json={"name": bad})
        assert response.status_code == 422 and response.json()["detail"] == INVALID

    renamed = client.patch(f"/api/chapters/{default['id']}", json={"name": "Starożytna Grecja"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Starożytna Grecja"
    taken = client.patch(f"/api/chapters/{default['id']}", json={"name": "rewolucja FRANCUSKA"})
    assert taken.status_code == 409 and taken.json()["detail"] == DUPLICATE
    assert (
        client.patch(f"/api/chapters/{default['id']}", json={"name": "grecja"}).status_code == 200
    )

    listed = chapters(client, s["id"])
    assert [c["id"] for c in listed] == [default["id"], body["id"]]
    assert listed[0]["is_default"] is True

    refused = client.delete(f"/api/chapters/{default['id']}")
    assert refused.status_code == 409 and refused.json()["detail"] == DEFAULT
    upload(client, s["id"], "b.txt", chapter_id=body["id"])
    busy = client.delete(f"/api/chapters/{body['id']}")
    assert busy.status_code == 409 and busy.json()["detail"] == NOT_EMPTY
    empty = new_chapter(client, s["id"], "Pusty")
    assert empty["ordinal"] == 2
    assert client.delete(f"/api/chapters/{empty['id']}").status_code == 204
    assert len(chapters(client, s["id"])) == 2

    for response in (
        client.patch("/api/chapters/99999", json={"name": "X"}),
        client.delete("/api/chapters/99999"),
    ):
        assert response.status_code == 404 and response.json()["detail"] == NOT_FOUND
    missing = client.get("/api/subjects/99999/chapters")
    assert missing.status_code == 404 and missing.json()["detail"] == "Subject not found."
    assert client.post("/api/subjects/99999/chapters", json={"name": "X"}).status_code == 404


def test_upload_into_chapter(client, db):
    s = subject(client)
    other = subject(client, "Biologia")
    default = chapters(client, s["id"])[0]
    assert client.get(f"/api/subjects/{s['id']}").json()["chapter_count"] == 1
    plain = upload(client, s["id"], "a.txt")
    assert plain["chapter_id"] == default["id"]

    named = upload(client, s["id"], "b.txt", chapter_name="Rewolucja francuska")
    assert named["chapter_id"] != default["id"]
    assert client.get(f"/api/subjects/{s['id']}").json()["chapter_count"] == 2
    same = upload(client, s["id"], "c.txt", chapter_name="  rewolucja FRANCUSKA ")
    assert same["chapter_id"] == named["chapter_id"]
    by_id = upload(client, s["id"], "d.txt", chapter_id=named["chapter_id"])
    assert by_id["chapter_id"] == named["chapter_id"]
    assert len(chapters(client, s["id"])) == 2

    foreign = chapters(client, other["id"])[0]["id"]
    assert upload(client, s["id"], "e.txt", 422, chapter_id=foreign)["detail"] == FOREIGN
    both = upload(client, s["id"], "f.txt", 422, chapter_id=default["id"], chapter_name="Nowy")
    assert both["detail"] == MOVE_TARGET
    assert upload(client, s["id"], "g.txt", 422, chapter_name="x" * 201)["detail"] == INVALID
    listed = client.get(f"/api/subjects/{s['id']}/documents").json()
    assert [d["chapter_id"] for d in listed] == [default["id"]] + [named["chapter_id"]] * 3


def test_new_version_keeps_the_document_chapter(client, db):
    s = subject(client)
    document = upload(client, s["id"], "a.txt", chapter_name="Rewolucja francuska")
    response = client.post(
        f"/api/documents/{document['id']}/versions",
        # A replacement must be readable (at least 200 characters of text).
        files={
            "file": ("a.txt", ("Nowa treść rozdziału o rewolucji. " * 8).encode(), "text/plain")
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["chapter_id"] == document["chapter_id"]
    assert response.json()["version_count"] == 2
    assert (
        client.get(f"/api/documents/{document['id']}").json()["chapter_id"]
        == (document["chapter_id"])
    )


def test_version_flags(client, db):
    s = make_subject(db)
    version, passages = make_version(db, s.id, count=4, excluded=(3,))
    other, other_passages = make_version(db, s.id, count=2, excluded=())
    make_topic(db, s.id, "Polis", passages[:2])
    make_topic(db, s.id, "Ateny", passages[1:3])
    make_topic(db, s.id, "Tylko wykluczony", passages[3:])
    removed = make_topic(db, s.id, "Usunięty", passages[:1])
    removed.status = "removed"
    make_topic(db, s.id, "Inna wersja", other_passages)
    db.commit()

    def flags():
        listed = client.get(f"/api/subjects/{s.id}/documents").json()
        return {d["latest_version"]["id"]: d["latest_version"] for d in listed}

    first = flags()[version.id]
    assert first["topics_extracted_at"] is None and first["questions_generated_at"] is None
    assert first["topic_count"] == 2
    assert flags()[other.id]["topic_count"] == 1

    add_job(db, "topics.extract", s.id, version.id, "running")
    add_job(db, "topics.extract", s.id, version.id, "failed")
    add_job(db, "questions.generate", s.id, version.id, "partial")
    assert flags()[version.id]["topics_extracted_at"] is None
    assert flags()[version.id]["questions_generated_at"] is None
    add_job(db, "topics.extract", s.id, version.id, "done")
    shown = flags()
    assert shown[version.id]["topics_extracted_at"] is not None
    assert shown[version.id]["questions_generated_at"] is None
    add_job(db, "questions.generate", s.id, version.id, "done")
    shown = flags()
    assert shown[version.id]["questions_generated_at"] is not None
    assert shown[other.id]["topics_extracted_at"] is None
    assert shown[other.id]["questions_generated_at"] is None
    detail = client.get(f"/api/documents/{version.document_id}").json()
    assert detail["latest_version"]["topics_extracted_at"] is not None
    assert detail["versions"][0]["topic_count"] == 2


def test_counts_in_chapter_out(client, db):
    s = make_subject(db)
    db.commit()
    default = db.scalars(select(Chapter.id).where(Chapter.subject_id == s.id)).one()
    second = new_chapter(client, s.id, "Druga")["id"]
    _, passages = make_version(db, s.id, count=4, excluded=())
    make_version(db, s.id, count=2, excluded=())
    moved, _ = make_version(db, s.id, count=2, excluded=())
    db.get(Document, moved.document_id).chapter_id = second
    a = make_topic(db, s.id, "Polis", passages[:2])
    b = make_topic(db, s.id, "Ateny", passages[2:])
    gone = make_topic(db, s.id, "Usunięty")
    gone.status = "removed"
    c = make_topic(db, s.id, "Druga część")
    c.chapter_id = second
    db.commit()
    make_question(db, s.id, a.id)
    make_question(db, s.id, a.id, "Drugie pytanie.", status="needs_review")
    make_question(db, s.id, b.id, "Wycofane.", status="retired")
    make_question(db, s.id, c.id, "W drugim rozdziale.")
    make_question(db, s.id, None, "Bez tematu.")
    counts = {
        c["id"]: (c["document_count"], c["topic_count"], c["question_count"])
        for c in chapters(client, s.id)
    }
    assert counts == {default: (2, 2, 2), second: (1, 1, 1)}


def test_chapters_list_budget(client, db):
    s = make_subject(db)
    make_version(db, s.id, count=2, excluded=())

    def call():
        assert client.get(f"/api/subjects/{s.id}/chapters").status_code == 200

    one = count_statements(client, call)
    for n in range(2):
        chapter_id = new_chapter(client, s.id, f"R{n}")["id"]
        for _ in range(2):
            version, passages = make_version(db, s.id, count=2, excluded=())
            db.get(Document, version.document_id).chapter_id = chapter_id
        db.commit()
        for t in range(4):
            topic = make_topic(db, s.id, f"T{n}-{t}", passages)
            topic.chapter_id = chapter_id
            db.commit()
            make_question(db, s.id, topic.id, f"Pytanie {n}-{t}.")
    assert len(chapters(client, s.id)) == 3
    assert sum(c["document_count"] for c in chapters(client, s.id)) == 5
    assert sum(c["topic_count"] for c in chapters(client, s.id)) == 8
    assert count_statements(client, call) == one
    no_writes(call)


def _more_versions(db, version: DocumentVersion, extra: int) -> None:
    for n in range(extra):
        db.add(
            DocumentVersion(
                document_id=version.document_id,
                version=n + 2,
                filename="a.txt",
                stored_path="documents/a.txt",
                mime="text/plain",
                size_bytes=1,
                sha256="1" * 64,
                status="imported",
                extracted_text="x",
                char_count=1,
            )
        )
    db.commit()


def test_documents_list_budget(client, db):
    s = make_subject(db)
    make_version(db, s.id, count=2, excluded=())

    def call():
        assert client.get(f"/api/subjects/{s.id}/documents").status_code == 200

    one = count_statements(client, call)
    for n in range(3):
        version, passages = make_version(db, s.id, count=2, excluded=())
        make_topic(db, s.id, f"Temat {n}", passages)
        add_job(db, "topics.extract", s.id, version.id, "done")
        add_job(db, "questions.generate", s.id, version.id, "done")
        _more_versions(db, version, n)
    listed = client.get(f"/api/subjects/{s.id}/documents").json()
    assert [d["version_count"] for d in listed] == [1, 1, 2, 3]
    assert count_statements(client, call) == one
    no_writes(call)


def test_subjects_list_budget(client, db):
    make_subject(db, "Historia")

    def call():
        assert client.get("/api/subjects").status_code == 200

    one = count_statements(client, call)
    for n in range(3):
        extra = make_subject(db, f"Przedmiot {n}")
        db.commit()
        new_chapter(client, extra.id, "Druga")
    listed = client.get("/api/subjects").json()
    assert len(listed) == 4
    assert sorted(s["chapter_count"] for s in listed) == [1, 2, 2, 2]
    assert count_statements(client, call) == one
    no_writes(call)


def test_subject_deletion_removes_its_chapters(client, db):
    first = subject(client, "Historia")
    second = subject(client, "Biologia")
    for s in (first, second):
        new_chapter(client, s["id"], "Druga")
    before = client.get(f"/api/subjects/{first['id']}/deletion-preview").json()
    extra = new_chapter(client, first["id"], "Pusta")
    assert client.get(f"/api/subjects/{first['id']}/deletion-preview").json() == before
    assert client.delete(f"/api/chapters/{extra['id']}").status_code == 204
    response = client.delete(
        f"/api/subjects/{first['id']}",
        params={"confirm": "true", "fingerprint": before["fingerprint"]},
    )
    assert response.status_code == 200, response.text

    def left(subject_id):
        return db.execute(
            text("SELECT COUNT(*) FROM chapter WHERE subject_id = :s"), {"s": subject_id}
        ).scalar_one()

    assert left(first["id"]) == 0
    assert left(second["id"]) == 2
