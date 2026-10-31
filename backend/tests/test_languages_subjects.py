import pytest
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.languages.service import LANGUAGE_LOCKED, LANGUAGE_NOT_ENABLED
from app.features.library.models import Subject
from app.features.library.service import neutral_sort_key


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def subject_rows(db) -> list[tuple[str, str]]:
    db.expire_all()
    return [(s.name, s.language) for s in db.query(Subject).order_by(Subject.id)]


def test_create_requires_language(client, db):
    response = client.post("/api/subjects", json={"name": "Historia"})
    assert response.status_code == 422
    assert subject_rows(db) == []
    response = client.post("/api/subjects", json={"name": "Historia", "language": "xx"})
    assert response.status_code == 422
    assert subject_rows(db) == []
    response = client.post("/api/subjects", json={"name": "Historia", "language": "pl"})
    assert response.status_code == 201, response.text
    assert response.json()["language"] == "pl"
    assert subject_rows(db) == [("Historia", "pl")]


def test_create_refuses_disabled_language(client, db):
    response = client.post("/api/subjects", json={"name": "Geschichte", "language": "de"})
    assert response.status_code == 422
    assert response.json()["detail"] == LANGUAGE_NOT_ENABLED
    assert subject_rows(db) == []
    assert client.put("/api/languages/de", json={"enabled": True}).status_code == 200
    response = client.post("/api/subjects", json={"name": "Geschichte", "language": "de"})
    assert response.status_code == 201, response.text
    assert response.json()["language"] == "de"


def test_language_locked(client, db):
    created = client.post("/api/subjects", json={"name": "Historia", "language": "pl"}).json()
    url = f"/api/subjects/{created['id']}"
    response = client.patch(url, json={"language": "de"})
    assert response.status_code == 422
    assert response.json()["detail"] == LANGUAGE_LOCKED
    response = client.patch(url, json={"name": "Nowa", "language": "pl"})
    assert response.status_code == 422
    assert response.json()["detail"] == LANGUAGE_LOCKED
    assert client.get(url).json()["name"] == "Historia"
    assert client.patch(url, json={"foo": 1}).status_code == 422
    response = client.patch(url, json={"name": "Nowa"})
    assert response.status_code == 200, response.text
    assert (response.json()["name"], response.json()["language"]) == ("Nowa", "pl")


def test_patch_without_a_json_object_is_still_validated(client, db):
    """The language check reads the body first; a body it can't read is left to validation."""
    created = client.post("/api/subjects", json={"name": "Historia", "language": "pl"}).json()
    url = f"/api/subjects/{created['id']}"
    for content in (b"", b"not json", b"\xff\xfe", b"[1, 2]", b'"language"'):
        response = client.patch(url, content=content, headers={"Content-Type": "application/json"})
        assert response.status_code == 422, (content, response.text)
        assert response.json()["detail"] != LANGUAGE_LOCKED
    assert client.get(url).json()["name"] == "Historia"
    response = client.patch(url, json={"name": "Nowa"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Nowa"


def test_subject_out_has_language_last(client, db):
    created = client.post("/api/subjects", json={"name": "Historia", "language": "pl"}).json()
    detail = client.get(f"/api/subjects/{created['id']}").json()
    assert list(detail)[-1] == "language"
    assert detail["language"] == "pl"
    listed = client.get("/api/subjects").json()
    assert list(listed[0])[-1] == "language"
    assert listed[0]["language"] == "pl"


def test_neutral_sort_key():
    def order(names):
        return sorted(names, key=neutral_sort_key)

    assert order(["Mapa", "Łacina", "Kino", "Lato", "Lżej"]) == [
        "Kino",
        "Łacina",
        "Lato",
        "Lżej",
        "Mapa",
    ]
    assert order(["Zoologie", "Économie", "Історія", "Biologia", "Ärzte"]) == [
        "Ärzte",
        "Biologia",
        "Économie",
        "Zoologie",
        "Історія",
    ]
    assert order(["łacina", "Lacina"]) == ["Lacina", "łacina"]
    assert neutral_sort_key("Straße")[0] == "strasse"


def test_list_subjects_neutral_order(client, db):
    assert client.put("/api/languages/de", json={"enabled": True}).status_code == 200
    names = [("Zoologia", "pl"), ("Ärzte", "de"), ("Łacina", "pl"), ("Logik", "de"), ("Cma", "pl")]
    for name, language in names:
        response = client.post("/api/subjects", json={"name": name, "language": language})
        assert response.status_code == 201, response.text
    listed = client.get("/api/subjects").json()
    expected = sorted((n for n, _ in names), key=neutral_sort_key)
    assert [s["name"] for s in listed] == expected
    assert {s["name"]: s["language"] for s in listed} == dict(names)
