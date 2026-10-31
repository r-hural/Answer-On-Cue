import io

import pytest
from docx import Document as DocxDocument
from docx.enum.style import WD_STYLE_TYPE
from test_library_extraction import make_pdf, para, txt

from app.features.languages.profiles import PROFILES
from app.features.library import extraction
from app.features.library.backmatter import back_matter
from app.features.library.models import SourcePassage


def locators(data: bytes, filename: str, code: str) -> list[str]:
    result = extraction.extract(data, filename, profile=PROFILES[code])
    assert result.status == "imported", result.message
    return [p.locator for p in result.passages]


def test_pdf_locators_per_language():
    pages = [f"Strona {i}. " + para("P", 300) for i in (1, 2, 3)]
    data = make_pdf(pages)
    expected = {"pl": "s.", "de": "S.", "es": "pág.", "uk": "с."}
    for code, label in expected.items():
        assert locators(data, "a.pdf", code) == [f"{label} {n}" for n in (1, 2, 3)]


def test_paragraph_and_document_labels():
    markdown = "# Introduction\n\n" + "\n\n".join(para(f"Akapit {n}", 250) for n in (1, 2, 3))
    assert locators(txt(markdown), "n.md", "en") == ["Introduction, paragraph 1–3"]
    plain = txt(para("Tekst", 400))
    assert locators(plain, "a.txt", "de") == ["Dokument, Absatz 1"]
    assert locators(plain, "a.txt", "fr") == ["Document, paragraphe 1"]
    assert locators(plain, "a.txt", "pl") == ["Dokument, akapit 1"]


def localized_docx() -> bytes:
    doc = DocxDocument()
    for name in ("Überschrift 1", "Titre 2", "Заголовок 1"):
        doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    for text, style in (
        ("Kapitel", "Überschrift 1"),
        ("Teil", "Titre 2"),
        ("Розділ", "Заголовок 1"),
    ):
        doc.add_paragraph(text, style=style)
        doc.add_paragraph(para(text, 250), style="Normal")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_localized_heading_styles():
    assert extraction._heading_level("Überschrift 1") == 1
    assert extraction._heading_level("Titre 2") == 2
    assert extraction._heading_level("Заголовок 1") == 1
    assert extraction._heading_level("Normal") == 0
    result = extraction.extract(localized_docx(), "a.docx", profile=PROFILES["de"])
    assert result.status == "imported", result.message
    assert [p.heading for p in result.passages] == ["Kapitel", "Kapitel › Teil", "Розділ"]
    assert [p.locator for p in result.passages] == [
        "Kapitel, Absatz 1",
        "Teil, Absatz 1",
        "Розділ, Absatz 1",
    ]


def upload(client, subject_id: int, filename: str, data: bytes) -> dict:
    response = client.post(
        f"/api/subjects/{subject_id}/documents",
        files={"file": (filename, data, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def first_locators(client, version_id: int) -> list[str]:
    page = client.get(f"/api/document-versions/{version_id}/passages").json()
    return [item["locator"] for item in page["items"]]


@pytest.fixture
def db_ready(settings):
    from test_topics_support import upgrade

    upgrade(settings.effective_database_url)


def test_upload_uses_the_subject_language(client, db_ready):
    assert client.put("/api/languages/en", json={"enabled": True}).status_code == 200
    english = client.post("/api/subjects", json={"name": "History", "language": "en"}).json()
    polish = client.post("/api/subjects", json={"name": "Historia", "language": "pl"}).json()
    text = txt(para("Treaty", 400) + "\n\n" + para("Peace", 400))

    document = upload(client, english["id"], "notes.txt", text)
    assert all(
        loc.startswith("Document, paragraph")
        for loc in first_locators(client, document["latest_version"]["id"])
    )
    polish_doc = upload(client, polish["id"], "notatki.txt", text)
    assert all(
        loc.startswith("Dokument, akapit")
        for loc in first_locators(client, polish_doc["latest_version"]["id"])
    )

    newer = txt(para("Treaty", 400) + "\n\n" + para("Congress", 400))
    response = client.post(
        f"/api/documents/{document['id']}/versions",
        files={"file": ("notes-2.txt", newer, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    version = response.json()["latest_version"]
    assert version["version"] == 2
    assert all(
        loc.startswith("Document, paragraph") for loc in first_locators(client, version["id"])
    )


def passages_of(*texts: str) -> list[SourcePassage]:
    return [SourcePassage(ordinal=n, text=t) for n, t in enumerate(texts)]


def suggested(*texts: str) -> list[str]:
    return [p.text for p in back_matter(passages_of(*texts))]


def test_back_matter_union():
    assert suggested("content", "Einzelnachweise", "n1", "n2") == ["n1", "n2"]
    assert suggested("content", "References\n1. Smith", "n1") == ["n1"]
    assert suggested("c1", "This text references earlier notes.", "c2") == []
    assert suggested("c1", "Treść… Bibliografia …", "b1") == ["b1"]
    assert suggested("c1", "Література:", "b1") == ["b1"]


def test_extract_requires_profile():
    with pytest.raises(TypeError):
        extraction.extract(txt(para("Tekst", 400)), "a.txt")  # type: ignore[call-arg]
