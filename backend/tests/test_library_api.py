import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from docx import Document as DocxDocument
from fastapi.testclient import TestClient
from fpdf import FPDF

from app.config import Settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parent.parent


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")


@pytest.fixture
def library_settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", frontend_dist=tmp_path / "dist")


@pytest.fixture
def api(library_settings) -> TestClient:
    upgrade(library_settings.effective_database_url)
    # Deleting a subject needs the app's own page (header and local Host).
    return TestClient(
        create_app(library_settings),
        base_url="http://127.0.0.1",
        headers={"X-AnswerOnCue": "1"},
    )


def make_scan_pdf(pages: int = 3) -> bytes:
    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.rect(20, 20, 100, 100, style="F")
    return bytes(pdf.output())


def make_docx() -> bytes:
    doc = DocxDocument()
    doc.add_heading("Wstęp", level=1)
    doc.add_paragraph("To jest pierwszy akapit dokumentu testowego. " * 8)
    doc.add_heading("Cel", level=2)
    doc.add_paragraph("To jest drugi akapit dokumentu testowego. " * 8)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def create_subject(api: TestClient, name: str = "Historia", **extra) -> dict:
    response = api.post("/api/subjects", json={"name": name, "language": "pl", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def upload(api: TestClient, subject_id: int, filename: str, data: bytes, **form):
    return api.post(
        f"/api/subjects/{subject_id}/documents",
        files={"file": (filename, data, "application/octet-stream")},
        data=form,
    )


def test_create_and_list_subjects_sorted_polish(api):
    for name in ["Zoologia", "Ćwiczenia", "anatomia", "Łacina", "Logika"]:
        create_subject(api, name)
    listed = api.get("/api/subjects").json()
    assert [s["name"] for s in listed] == [
        "anatomia",
        "Ćwiczenia",
        "Łacina",
        "Logika",
        "Zoologia",
    ]
    assert set(listed[0]) == {
        "id",
        "name",
        "description",
        "document_count",
        "created_at",
        "updated_at",
        "chapter_count",
        "source_review_count",
        "language",
    }
    assert listed[0]["document_count"] == 0


def test_duplicate_subject_name_409_case_insensitive(api):
    create_subject(api, "Łacina")
    response = api.post("/api/subjects", json={"name": "  łACINA ", "language": "pl"})
    assert response.status_code == 409
    assert response.json()["detail"] == "A subject with this name already exists."


def test_subject_name_validation(api):
    assert api.post("/api/subjects", json={"name": "   ", "language": "pl"}).status_code == 422
    assert api.post("/api/subjects", json={"name": "x" * 201, "language": "pl"}).status_code == 422
    assert api.post("/api/subjects", json={"name": "x" * 200, "language": "pl"}).status_code == 201


def test_get_and_rename_subject(api):
    subject = create_subject(api, "  Historia ", description="Notatki")
    assert subject["name"] == "Historia"
    assert api.get(f"/api/subjects/{subject['id']}").json()["description"] == "Notatki"
    renamed = api.patch(f"/api/subjects/{subject['id']}", json={"name": "Historia sztuki"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Historia sztuki"
    assert renamed.json()["description"] == "Notatki"
    other = create_subject(api, "Fizyka")
    clash = api.patch(f"/api/subjects/{other['id']}", json={"name": "historia SZTUKI"})
    assert clash.status_code == 409
    same = api.patch(f"/api/subjects/{subject['id']}", json={"name": "HISTORIA sztuki"})
    assert same.status_code == 200


def test_storage_path_safe(api, library_settings):
    subject = create_subject(api)
    response = upload(api, subject["id"], "..\\..\\evil.pdf", make_scan_pdf())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["latest_version"]["filename"] == "..\\..\\evil.pdf"
    root = library_settings.data_dir / "documents" / "subject-1" / "document-1"
    assert [p.name for p in root.iterdir()] == ["v1.pdf"]
    stored = list((library_settings.data_dir).rglob("*.pdf"))
    assert stored == [root / "v1.pdf"]
    assert not (library_settings.data_dir.parent / "evil.pdf").exists()


@pytest.mark.parametrize(
    "name", ["CON.txt", "a" * 400 + ".txt", "zażółć gęślą.txt", "../../x.txt", "/abs/x.txt"]
)
def test_odd_filenames_stay_inside_documents(api, library_settings, name):
    subject = create_subject(api)
    response = upload(api, subject["id"], name, b"Zwykly tekst. " * 30)
    assert response.status_code == 201, response.text
    documents = (library_settings.data_dir / "documents").resolve()
    files = [p for p in library_settings.data_dir.rglob("*") if p.is_file() and p.suffix == ".txt"]
    assert [p.name for p in files] == ["v1.txt"]
    assert files[0].resolve().is_relative_to(documents)


def test_upload_docx_creates_document_and_passages(api):
    subject = create_subject(api)
    response = upload(api, subject["id"], "praca.docx", make_docx(), title="Moja praca")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Moja praca"
    assert body["latest_version"]["status"] == "imported"
    assert body["latest_version"]["passage_count"] > 0
    version_id = body["latest_version"]["id"]
    page = api.get(f"/api/document-versions/{version_id}/passages").json()
    assert page["total"] == body["latest_version"]["passage_count"]
    assert page["items"][0]["locator"].startswith("Wstęp, akapit")
    assert api.get(f"/api/subjects/{subject['id']}").json()["document_count"] == 1
    listed = api.get(f"/api/subjects/{subject['id']}/documents").json()
    assert [d["id"] for d in listed] == [body["id"]]
    detail = api.get(f"/api/documents/{body['id']}").json()
    assert detail["version_count"] == 1
    assert len(detail["versions"]) == 1


def test_default_title_is_filename_stem(api):
    subject = create_subject(api)
    body = upload(api, subject["id"], "notatki z wykładu.txt", b"Tekst. " * 50).json()
    assert body["title"] == "notatki z wykładu"


def test_upload_unsupported_415(api):
    subject = create_subject(api)
    response = upload(api, subject["id"], "slajdy.pptx", b"x")
    assert response.status_code == 415
    assert response.json()["detail"] == "Supported formats: PDF, DOCX, TXT, MD."


def test_upload_empty_422(api):
    subject = create_subject(api)
    response = upload(api, subject["id"], "pusty.txt", b"")
    assert response.status_code == 422
    assert response.json()["detail"] == "The file is empty."


def test_upload_too_large_413(api, monkeypatch):
    from app.features.library import service

    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 1024)
    subject = create_subject(api)
    response = upload(api, subject["id"], "duzy.txt", b"a" * 5000)
    assert response.status_code == 413
    assert response.json()["detail"] == "The file is too large (limit 25 MB)."


def test_upload_scan_pdf_kept_with_status(api):
    subject = create_subject(api)
    response = upload(api, subject["id"], "skan.pdf", make_scan_pdf())
    assert response.status_code == 201
    version = response.json()["latest_version"]
    assert version["status"] == "too_little_text"
    assert version["message"].startswith("The document does not contain enough text")


def test_new_version_increments(api, library_settings):
    subject = create_subject(api)
    document = upload(api, subject["id"], "a.txt", b"Wersja pierwsza. " * 30).json()
    response = api.post(
        f"/api/documents/{document['id']}/versions",
        files={"file": ("a.txt", b"Wersja druga. " * 30, "text/plain")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version_count"] == 2
    assert body["latest_version"]["version"] == 2
    root = library_settings.data_dir / "documents" / "subject-1" / "document-1"
    assert sorted(p.name for p in root.iterdir()) == ["v1.txt", "v2.txt"]
    detail = api.get(f"/api/documents/{document['id']}").json()
    assert [v["version"] for v in detail["versions"]] == [1, 2]


def test_identical_version_409(api):
    subject = create_subject(api)
    data = b"Ta sama tresc. " * 30
    document = upload(api, subject["id"], "a.txt", data).json()
    response = api.post(
        f"/api/documents/{document['id']}/versions",
        files={"file": ("b.txt", data, "text/plain")},
    )
    assert response.status_code == 409
    assert (
        response.json()["detail"] == "This file is identical to the latest version of the document."
    )
    assert api.get(f"/api/documents/{document['id']}").json()["version_count"] == 1


def test_preview_first_5000_chars(api):
    subject = create_subject(api)
    text = "Zdanie testowe numer jeden. " * 400
    document = upload(api, subject["id"], "dlugi.txt", text.encode()).json()
    version_id = document["latest_version"]["id"]
    preview = api.get(f"/api/document-versions/{version_id}/preview").json()
    assert preview["status"] == "imported"
    assert len(preview["text"]) == 5000
    assert preview["char_count"] == len(text.strip())
    assert set(preview) == {"status", "message", "char_count", "page_count", "text"}


def test_passages_pagination_limit(api):
    subject = create_subject(api)
    document = upload(api, subject["id"], "a.txt", ("Zdanie. " * 600).encode()).json()
    version_id = document["latest_version"]["id"]
    page = api.get(f"/api/document-versions/{version_id}/passages?offset=1&limit=2").json()
    assert len(page["items"]) == 2
    assert page["items"][0]["ordinal"] == 1
    assert api.get(f"/api/document-versions/{version_id}/passages?limit=201").status_code == 422


def test_exclude_passage(api):
    subject = create_subject(api)
    document = upload(api, subject["id"], "a.txt", b"Zdanie testowe. " * 30).json()
    version_id = document["latest_version"]["id"]
    passage = api.get(f"/api/document-versions/{version_id}/passages").json()["items"][0]
    assert passage["excluded"] is False
    response = api.patch(f"/api/passages/{passage['id']}", json={"excluded": True})
    assert response.status_code == 200
    assert response.json()["excluded"] is True
    items = api.get(f"/api/document-versions/{version_id}/passages").json()["items"]
    assert items[0]["excluded"] is True


def test_deletion_preview_and_confirm(api, library_settings):
    subject = create_subject(api)
    other = create_subject(api, "Fizyka")
    first = upload(api, subject["id"], "praca.docx", make_docx()).json()
    api.post(
        f"/api/documents/{first['id']}/versions",
        files={"file": ("a.txt", b"Inna wersja. " * 30, "text/plain")},
    )
    upload(api, subject["id"], "b.txt", b"Drugi dokument. " * 30)
    upload(api, other["id"], "c.txt", b"Fizyka. " * 50)
    root = library_settings.data_dir / "documents"
    assert (root / f"subject-{subject['id']}").is_dir()

    preview = api.get(f"/api/subjects/{subject['id']}/deletion-preview").json()
    versions = [
        v
        for d in api.get(f"/api/subjects/{subject['id']}/documents").json()
        for v in api.get(f"/api/documents/{d['id']}").json()["versions"]
    ]
    assert preview["subject"] == "Historia"
    assert preview["documents"] == 2
    assert preview["versions"] == 3
    assert preview["files"] == 3
    assert preview["passages"] == sum(v["passage_count"] for v in versions)
    assert preview["bytes"] == sum(v["size_bytes"] for v in versions)

    refused = api.delete(f"/api/subjects/{subject['id']}")
    assert refused.status_code == 400
    assert refused.json()["detail"].startswith("Confirm the deletion")
    assert (root / f"subject-{subject['id']}").is_dir()

    deleted = api.delete(f"/api/subjects/{subject['id']}?confirm=true")
    assert deleted.status_code == 200
    assert deleted.json() == preview
    assert api.get(f"/api/subjects/{subject['id']}").status_code == 404
    assert not (root / f"subject-{subject['id']}").exists()
    assert (root / f"subject-{other['id']}").is_dir()
    assert api.get(f"/api/subjects/{other['id']}/documents").json()


def test_unknown_ids_404(api):
    upload_file = {"file": ("a.txt", b"x" * 300, "text/plain")}
    cases = [
        ("get", "/api/subjects/99", {}),
        ("patch", "/api/subjects/99", {"json": {"name": "x"}}),
        ("get", "/api/subjects/99/deletion-preview", {}),
        ("delete", "/api/subjects/99?confirm=true", {}),
        ("post", "/api/subjects/99/documents", {"files": upload_file}),
        ("get", "/api/subjects/99/documents", {}),
        ("get", "/api/documents/99", {}),
        ("post", "/api/documents/99/versions", {"files": upload_file}),
        ("get", "/api/document-versions/99/passages", {}),
        ("get", "/api/document-versions/99/preview", {}),
        ("patch", "/api/passages/99", {"json": {"excluded": True}}),
    ]
    for method, url, kwargs in cases:
        response = getattr(api, method)(url, **kwargs)
        assert response.status_code == 404, url
        detail = response.json()["detail"]
        assert detail.endswith("not found."), (url, detail)


@pytest.mark.parametrize("failing", ["extract", "replace"])
def test_failed_import_leaves_no_files(library_settings, monkeypatch, failing):
    from app.features.library import extraction, service

    upgrade(library_settings.effective_database_url)
    client = TestClient(create_app(library_settings), raise_server_exceptions=False)
    subject = create_subject(client)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    if failing == "extract":
        monkeypatch.setattr(extraction, "extract", boom)
    else:
        monkeypatch.setattr(service.os, "replace", boom)
    response = upload(client, subject["id"], "a.txt", b"Tekst. " * 50)
    assert response.status_code == 500
    documents = library_settings.data_dir / "documents"
    leftovers = [p for p in documents.rglob("*") if p.is_file()] if documents.exists() else []
    assert leftovers == []
    assert client.get(f"/api/subjects/{subject['id']}/documents").json() == []
