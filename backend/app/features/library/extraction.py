import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Literal

from app.features.languages.profiles import LanguageProfile

MERGE_LIMIT = 1200
SPLIT_LIMIT = 2000
MIN_TOTAL_CHARS = 200
MIN_PAGE_CHARS = 20
LOCATOR_MAX = 120
MAX_PDF_PAGES = 2000
MAX_DOCX_UNCOMPRESSED = 200 * 1024 * 1024
HEADING_SEPARATOR = " › "
# DOCX heading style names of every supported language (a subject's file may use any of them).
HEADING_STYLES = ("Heading", "Nagłówek", "Überschrift", "Titre", "Título", "Titolo", "Заголовок")

TOO_LITTLE_TEXT_MESSAGE = (
    "The document does not contain enough text to learn from (it may be a scan). "
    "Scans are not supported yet."
)
FAILED_MESSAGE = "The file could not be read. Check that it is not damaged or password-protected."

Status = Literal["imported", "too_little_text", "failed"]


class UnsupportedFileType(Exception):
    pass


@dataclass
class PassageDraft:
    ordinal: int
    heading: str | None
    locator: str
    text: str
    char_start: int
    char_end: int


@dataclass
class ExtractionResult:
    status: Status
    text: str
    passages: list[PassageDraft]
    page_count: int | None
    message: str | None


@dataclass
class _Paragraph:
    text: str
    heading: str | None
    locator_label: str  # the heading or "Dokument" (docx/txt), or "s. 3" (pdf); per language
    number: int | None  # paragraph number within the heading; None for pdf
    page: int | None
    start: int = 0
    end: int = 0


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\x00", ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text)


def extract(data: bytes, filename: str, *, profile: LanguageProfile) -> ExtractionResult:
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    readers = {".pdf": _read_pdf, ".docx": _read_docx, ".txt": _read_text, ".md": _read_text}
    reader = readers.get(extension)
    if reader is None:
        raise UnsupportedFileType(extension)
    try:
        paragraphs, page_count, pdf_page_texts = reader(data, profile)
    except Exception:
        return ExtractionResult("failed", "", [], None, FAILED_MESSAGE)
    return _build_result(paragraphs, page_count, pdf_page_texts, profile)


# --- readers -------------------------------------------------------------------------------


def _read_pdf(data: bytes, profile: LanguageProfile):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("encrypted")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError("too many pages")
    paragraphs: list[_Paragraph] = []
    page_texts: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = normalize(page.extract_text() or "")
        page_texts.append(text)
        for block in re.split(r"\n\s*\n", text):
            block = block.strip()
            if block:
                paragraphs.append(
                    _Paragraph(
                        block, None, f"{profile.page_label} {page_number}", None, page_number
                    )
                )
    return paragraphs, len(reader.pages), page_texts


def _read_docx(data: bytes, profile: LanguageProfile):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_UNCOMPRESSED:
            raise ValueError("decompressed size too large")
    document = Document(io.BytesIO(data))
    state = _HeadingState(profile.document_label)
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = normalize(paragraph.text).strip()
            if not text:
                continue
            level = _heading_level(paragraph.style.name if paragraph.style is not None else "")
            if level:
                state.set_heading(level, text)
            else:
                state.add(text)
        elif tag == "tbl":
            table = Table(child, document)
            for row in table.rows:
                cells = [normalize(cell.text).strip() for cell in row.cells]
                text = " | ".join(c for c in cells if c)
                if text:
                    state.add(text)
    return state.paragraphs, None, None


def _read_text(data: bytes, profile: LanguageProfile):
    text = normalize(_decode(data))
    state = _HeadingState(profile.document_label)
    buffer: list[str] = []

    def flush() -> None:
        block = "\n".join(buffer).strip()
        buffer.clear()
        if block:
            state.add(block)

    for line in text.split("\n"):
        heading = _parse_markdown_heading(line)
        if heading:
            flush()
            state.set_heading(*heading)
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return state.paragraphs, None, None


def _parse_markdown_heading(line: str) -> tuple[int, str] | None:
    """`# Title` .. `###### Title` as (level, title); plain string ops, no backtracking."""
    stripped = line.lstrip("#")
    level = len(line) - len(stripped)
    if not 1 <= level <= 6 or not stripped[:1].isspace():
        return None
    title = stripped.strip()
    return (level, title) if title else None


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("iso-8859-2")


def _heading_level(style_name: str) -> int:
    if not style_name.startswith(HEADING_STYLES):
        return 0
    match = re.search(r"(\d+)\s*$", style_name)
    return max(1, int(match.group(1))) if match else 1


class _HeadingState:
    def __init__(self, document_label: str) -> None:
        self.document_label = document_label
        self.path: list[str] = []
        self.count = 0
        self.paragraphs: list[_Paragraph] = []

    def set_heading(self, level: int, text: str) -> None:
        self.path = self.path[: level - 1] + [text]
        self.count = 0

    def add(self, text: str) -> None:
        self.count += 1
        heading = HEADING_SEPARATOR.join(self.path) if self.path else None
        label = self.path[-1] if self.path else self.document_label
        self.paragraphs.append(_Paragraph(text, heading, label, self.count, None))


# --- passages ------------------------------------------------------------------------------


def _build_result(paragraphs, page_count, pdf_page_texts, profile) -> ExtractionResult:
    text = ""
    for paragraph in paragraphs:
        if text:
            text += "\n\n"
        paragraph.start = len(text)
        text += paragraph.text
        paragraph.end = len(text)

    units: list[_Paragraph] = []
    for paragraph in paragraphs:
        units.extend(_split_long(paragraph))
    passages = _merge(units, text, profile.paragraph_label)

    non_whitespace = len(re.sub(r"\s", "", text))
    scanned = False
    if pdf_page_texts is not None:
        sparse = sum(1 for t in pdf_page_texts if len(re.sub(r"\s", "", t)) < MIN_PAGE_CHARS)
        scanned = not pdf_page_texts or sparse * 2 > len(pdf_page_texts)
    if scanned or non_whitespace < MIN_TOTAL_CHARS:
        return ExtractionResult(
            "too_little_text", text, passages, page_count, TOO_LITTLE_TEXT_MESSAGE
        )
    return ExtractionResult("imported", text, passages, page_count, None)


def _split_long(paragraph: _Paragraph) -> list[_Paragraph]:
    if len(paragraph.text) <= SPLIT_LIMIT:
        return [paragraph]
    pieces: list[_Paragraph] = []
    text = paragraph.text
    position = 0
    while position < len(text):
        if len(text) - position <= SPLIT_LIMIT:
            end = len(text)
        else:
            window = text[position : position + SPLIT_LIMIT]
            cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
            end = position + cut + 1 if cut > 0 else position + SPLIT_LIMIT
        piece = text[position:end]
        stripped = piece.strip()
        if stripped:
            offset = paragraph.start + position + (len(piece) - len(piece.lstrip()))
            pieces.append(
                _Paragraph(
                    stripped,
                    paragraph.heading,
                    paragraph.locator_label,
                    paragraph.number,
                    paragraph.page,
                    offset,
                    offset + len(stripped),
                )
            )
        position = end
        while position < len(text) and text[position].isspace():
            position += 1
    return pieces


def _merge(units: list[_Paragraph], full_text: str, paragraph_label: str) -> list[PassageDraft]:
    passages: list[PassageDraft] = []
    group: list[_Paragraph] = []

    def flush() -> None:
        if not group:
            return
        first, last = group[0], group[-1]
        passages.append(
            PassageDraft(
                ordinal=len(passages),
                heading=first.heading,
                locator=_locator(first, last, paragraph_label),
                text="\n\n".join(p.text for p in group),
                char_start=first.start,
                char_end=last.end,
            )
        )
        group.clear()

    for unit in units:
        if group and (
            unit.heading != group[0].heading
            or unit.page != group[0].page
            or unit.end - group[0].start > MERGE_LIMIT
        ):
            flush()
        group.append(unit)
    flush()
    return passages


def _locator(first: _Paragraph, last: _Paragraph, paragraph_label: str) -> str:
    if first.number is None:
        return first.locator_label
    suffix = f"{paragraph_label} {first.number}"
    if last.number != first.number:
        suffix += f"–{last.number}"
    label = first.locator_label
    room = LOCATOR_MAX - len(suffix) - 2
    if len(label) > room:
        label = label[: max(room - 1, 1)] + "…"
    return f"{label}, {suffix}"
