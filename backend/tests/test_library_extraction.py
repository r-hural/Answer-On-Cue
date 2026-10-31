import io
from pathlib import Path

import pytest
from docx import Document as DocxDocument
from fpdf import FPDF

from app.features.languages.profiles import PROFILES
from app.features.library.extraction import UnsupportedFileType, extract

ARIAL = Path("C:/Windows/Fonts/arial.ttf")
SCAN_MESSAGE = (
    "The document does not contain enough text to learn from (it may be a scan). "
    "Scans are not supported yet."
)
FAILED_MESSAGE = "The file could not be read. Check that it is not damaged or password-protected."


def para(label: str, length: int = 300) -> str:
    base = f"{label} to zdanie o długości testowej. "
    return (base * (length // len(base) + 1))[:length].rstrip()


def txt(content: str, encoding: str = "utf-8") -> bytes:
    return content.encode(encoding)


def make_docx() -> bytes:
    doc = DocxDocument()
    doc.add_heading("Wstęp", level=1)
    doc.add_paragraph(para("Pierwszy", 250))
    doc.add_heading("Cel pracy", level=2)
    doc.add_paragraph(para("Drugi", 250))
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "x"
    table.rows[0].cells[1].text = "y"
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def make_pdf(pages: list[str]) -> bytes:
    if not ARIAL.exists():
        pytest.skip("Arial font missing")
    pdf = FPDF()
    pdf.add_font("Arial", fname=str(ARIAL))
    for page_text in pages:
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.multi_cell(0, 8, page_text)
    return bytes(pdf.output())


def make_scan_pdf(pages: int = 3) -> bytes:
    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.rect(20, 20, 100, 100, style="F")
    return bytes(pdf.output())


def test_txt_utf8_paragraphs_and_locators():
    content = f"# Wstęp\n\n{para('A', 100)}\n\n{para('B', 100)}\n\n{para('C', 100)}\n"
    result = extract(txt(content), "notatki.txt", profile=PROFILES["pl"])
    assert result.status == "imported"
    assert len(result.passages) == 1
    passage = result.passages[0]
    assert passage.heading == "Wstęp"
    assert passage.locator == "Wstęp, akapit 1–3"


def test_txt_cp1250_polish():
    content = f"zażółć gęślą jaźń. {para('X', 300)}"
    result = extract(txt(content, "cp1250"), "stary.txt", profile=PROFILES["pl"])
    assert "zażółć gęślą jaźń" in result.text


def test_markdown_heading_path():
    content = f"# A\n\n## B\n\n{para('Treść', 300)}\n"
    result = extract(txt(content), "n.md", profile=PROFILES["pl"])
    assert result.passages[0].heading == "A › B"


def test_docx_headings_and_table():
    result = extract(make_docx(), "praca.docx", profile=PROFILES["pl"])
    assert result.status == "imported"
    headings = [p.heading for p in result.passages]
    assert headings == ["Wstęp", "Wstęp › Cel pracy"]
    assert result.passages[1].locator.startswith("Cel pracy, akapit 1")
    assert "x | y" in result.passages[1].text


def test_pdf_pages_locators():
    pages = [f"Strona {i} zażółć gęślą jaźń. " + para("P", 300) for i in (1, 2, 3)]
    result = extract(make_pdf(pages), "a.pdf", profile=PROFILES["pl"])
    assert result.status == "imported"
    assert result.page_count == 3
    assert [p.locator for p in result.passages] == ["s. 1", "s. 2", "s. 3"]
    assert "zażółć gęślą jaźń" in result.text
    assert all(p.heading is None for p in result.passages)


def test_pdf_scan_detected():
    result = extract(make_scan_pdf(), "skan.pdf", profile=PROFILES["pl"])
    assert result.status == "too_little_text"
    assert result.message == SCAN_MESSAGE


def test_short_document_too_little_text():
    result = extract(txt("a" * 150), "krotki.txt", profile=PROFILES["pl"])
    assert result.status == "too_little_text"
    assert result.message == SCAN_MESSAGE


def test_long_paragraph_split_on_sentence():
    sentence = "To jest zdanie testowe numer jeden. "
    long_paragraph = (sentence * 200)[:4500].rsplit(".", 1)[0] + "."
    result = extract(txt(long_paragraph), "dlugi.txt", profile=PROFILES["pl"])
    assert len(result.passages) == 3
    for passage in result.passages:
        assert len(passage.text) <= 2000
        assert passage.text.endswith(".")


def test_merge_limit_1200():
    blocks = [f"# H1\n\n{para(str(i), 300)}" for i in range(5)]
    content = blocks[0] + "\n\n" + "\n\n".join(para(str(i), 300) for i in range(1, 5))
    content += "\n\n# H2\n\n" + "\n\n".join(para(str(i), 300) for i in range(5, 10))
    result = extract(txt(content), "m.txt", profile=PROFILES["pl"])
    assert all(len(p.text) <= 1200 for p in result.passages)
    by_heading = {p.heading for p in result.passages}
    assert by_heading == {"H1", "H2"}
    for passage in result.passages:
        if passage.heading == "H1":
            assert not any(f" {i} " in passage.text for i in range(5, 10))


def test_char_offsets_match_text():
    content = "# T\n\n" + "\n\n".join(para(str(i), 500) for i in range(8))
    content += "\n\n" + (para("Długi", 4500))
    result = extract(txt(content), "o.txt", profile=PROFILES["pl"])
    assert result.passages
    for passage in result.passages:
        assert result.text[passage.char_start : passage.char_end] == passage.text


def test_corrupt_pdf_failed():
    result = extract(b"%PDF-1.4 garbage", "zly.pdf", profile=PROFILES["pl"])
    assert result.status == "failed"
    assert result.message == FAILED_MESSAGE


def test_unsupported_extension():
    with pytest.raises(UnsupportedFileType):
        extract(b"x", "slajdy.pptx", profile=PROFILES["pl"])
