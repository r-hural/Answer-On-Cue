import pytest
from test_topics_support import make_subject, make_version, upgrade

from app.db.session import SessionLocal
from app.features.library.backmatter import back_matter
from app.features.library.models import SourcePassage


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def _book(db, marker=" Przypisy", count=6):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id, count=count, excluded=())
    if marker:
        passages[2].text += marker
        db.commit()
    return version, passages


def test_back_matter_starts_after_the_marker_passage():
    texts = ["a", "b Przypisy", "c", "d"]
    passages = [SourcePassage(ordinal=n, text=t) for n, t in enumerate(texts)]
    assert [p.text for p in back_matter(passages)] == ["c", "d"]
    assert back_matter(passages[:1]) == []


def test_suggestion_endpoint(client, db):
    version, passages = _book(db)
    body = client.get(f"/api/document-versions/{version.id}/back-matter").json()
    assert body == {
        "passage_ids": [p.id for p in passages[3:]],
        "count": 3,
        "first_locator": passages[3].locator,
        "last_locator": passages[5].locator,
        "already_excluded": False,
    }
    assert client.get("/api/document-versions/999/back-matter").status_code == 404


def test_no_marker_no_suggestion(client, db):
    version, passages = _book(db, marker="")
    body = client.get(f"/api/document-versions/{version.id}/back-matter").json()
    assert body["count"] == 0 and body["passage_ids"] == []
    assert body["already_excluded"] is False
    # Looking at the suggestion never excludes anything.
    db.expire_all()
    assert all(not p.excluded for p in passages)


def test_bulk_exclusion_and_restore(client, db):
    version, passages = _book(db)
    ids = [p.id for p in passages[3:]]
    url = f"/api/document-versions/{version.id}/passages/exclusion"
    assert client.post(url, json={"passage_ids": ids, "excluded": True}).json() == {"updated": 3}
    db.expire_all()
    assert [p.excluded for p in passages] == [False] * 3 + [True] * 3
    assert client.post(url, json={"passage_ids": ids, "excluded": False}).json() == {"updated": 3}
    db.expire_all()
    assert not any(p.excluded for p in passages)


def test_bulk_foreign_ids_422_nothing_changed(client, db):
    version, passages = _book(db)
    other, other_passages = make_version(db, make_subject(db, "Inny").id, count=2, excluded=())
    url = f"/api/document-versions/{version.id}/passages/exclusion"
    response = client.post(
        url, json={"passage_ids": [passages[3].id, other_passages[0].id], "excluded": True}
    )
    assert response.status_code == 422
    assert (
        response.json()["detail"] == "Some passages do not belong to this version of the document."
    )
    db.expire_all()
    assert not any(p.excluded for p in passages + other_passages)


def test_already_excluded_flag(client, db):
    version, passages = _book(db)
    url = f"/api/document-versions/{version.id}/passages/exclusion"
    client.post(url, json={"passage_ids": [p.id for p in passages[3:]], "excluded": True})
    body = client.get(f"/api/document-versions/{version.id}/back-matter").json()
    assert body["count"] == 3 and body["already_excluded"] is True
