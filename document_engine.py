"""In-memory document extraction, bounded chunks and source-grounded evidence."""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pdfplumber
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_PAGES = 500


class DocumentError(ValueError):
    pass


@dataclass(frozen=True)
class Page:
    number: int
    text: str


@dataclass
class SourceDocument:
    name: str
    sha256: str
    pages: list[Page]
    pagination: str = "Original PDF pages"
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(f"[Page {p.number}]\n{p.text}" for p in self.pages)


@dataclass(frozen=True)
class Chunk:
    text: str
    pages: tuple[int, ...]


def _text_pages(text: str) -> list[Page]:
    markers = list(re.finditer(r"(?m)^\s*\[Page (\d+)\]\s*$", text))
    if markers:
        if text[:markers[0].start()].strip():
            raise DocumentError("Text before the first [Page X] marker is ambiguous. Add [Page 1] at the beginning.")
        numbers = [int(m.group(1)) for m in markers]
        if numbers != list(range(1, len(numbers) + 1)):
            raise DocumentError("Page markers must begin with [Page 1] and increase consecutively.")
        return [Page(numbers[i], text[m.end():markers[i + 1].start() if i + 1 < len(markers) else len(text)].strip()) for i, m in enumerate(markers)]
    return [Page(i + 1, part.strip()) for i, part in enumerate(text.split("\f"))]


def _docx_pages(raw: bytes) -> list[Page]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise DocumentError("The expanded DOCX is too large.")
        if any(item.flag_bits & 1 for item in archive.infolist()):
            raise DocumentError("Encrypted DOCX files are not supported.")
    document = Document(io.BytesIO(raw))
    parts = [""]
    for block in document.element.body:
        if block.tag == qn("w:p"):
            if block.find("./" + qn("w:pPr") + "/" + qn("w:pageBreakBefore")) is not None and parts[-1].strip():
                parts.append("")
            for node in block.iter():
                if node.tag == qn("w:t"):
                    parts[-1] += node.text or ""
                elif node.tag == qn("w:tab"):
                    parts[-1] += "\t"
                elif node.tag == qn("w:br"):
                    if node.get(qn("w:type")) == "page":
                        if parts[-1].strip():
                            parts.append("")
                    else:
                        parts[-1] += "\n"
            parts[-1] += "\n"
        elif block.tag == qn("w:tbl"):
            table = Table(block, document)
            for row in table.rows:
                parts[-1] += " | ".join(cell.text for cell in row.cells) + "\n"
    if len(parts) > 1 and not parts[-1].strip():
        parts.pop()
    return [Page(i + 1, part.strip()) for i, part in enumerate(parts)]


def parse_document(raw: bytes, name: str) -> SourceDocument:
    if not raw:
        raise DocumentError("The document is empty.")
    if len(raw) > MAX_FILE_BYTES:
        raise DocumentError("Documents must be 25 MB or smaller.")
    suffix = Path(name).suffix.lower()
    warnings = []
    try:
        if suffix == ".txt":
            # utf-8-sig also accepts ordinary UTF-8; other encodings fail explicitly.
            pages = _text_pages(raw.decode("utf-8-sig", errors="strict"))
            pagination = "TXT page markers or form-feed sections; not rendered page numbers"
        elif suffix == ".pdf":
            if not raw.startswith(b"%PDF-"):
                raise DocumentError("The file is not a valid PDF.")
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                if len(pdf.pages) > MAX_PAGES:
                    raise DocumentError(f"Limit: {MAX_PAGES} pages per review.")
                pages = [Page(i + 1, p.extract_text(x_tolerance=2, y_tolerance=3) or "") for i, p in enumerate(pdf.pages)]
            pagination = "Original PDF pages"
        elif suffix == ".docx":
            pages = _docx_pages(raw)
            pagination = "DOCX explicit page-break sections; convert to PDF for exact page citations"
            warnings.append("DOCX headers, footers, text boxes and tracked deletions are outside the review scope; export a clean PDF for full visual coverage.")
        else:
            raise DocumentError("Supported formats: PDF, DOCX and UTF-8 TXT.")
    except UnicodeError as exc:
        raise DocumentError("TXT files must use UTF-8 encoding. Save the file as UTF-8 and retry.") from exc
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("Cannot read this document. It may be damaged, encrypted or in the wrong format.") from exc
    if len(pages) > MAX_PAGES or sum(len(p.text) for p in pages) > MAX_TEXT_CHARS:
        raise DocumentError("Document exceeds the 500-page or 2-million-character limit.")
    if not any(p.text.strip() for p in pages):
        raise DocumentError("No selectable text was found. Scanned documents need local OCR before review.")
    empty = [str(p.number) for p in pages if not p.text.strip()]
    if empty:
        warnings.append("No text extracted from page(s) " + ", ".join(empty) + ". Review those pages manually or run local OCR.")
    if any("\x00" in p.text for p in pages):
        raise DocumentError("The document contains NUL characters and may be a binary file.")
    safe_name = re.split(r"[\\/]", name)[-1]
    return SourceDocument(safe_name, hashlib.sha256(raw).hexdigest(), pages, pagination, warnings)


def chunk_document(document: SourceDocument, max_chars: int = 4500, overlap: int = 180) -> list[Chunk]:
    """Keep page identity and overlap both within pages and across page boundaries."""
    if max_chars < 512 or not 0 <= overlap < max_chars // 3:
        raise ValueError("Invalid chunk size or overlap.")
    # Layout whitespace (for example aligned signature columns) need not use
    # inference context. Source pages remain untouched for evidence checking.
    pages = [Page(p.number, re.sub(r"[ \t]+", " ", p.text).strip()) for p in document.pages if p.text.strip()]
    combined = "\n\n".join(f"[Page {page.number}]\n{page.text}" for page in pages)
    if combined and len(combined) <= max_chars:
        return [Chunk(combined, tuple(page.number for page in pages))]
    chunks = []
    previous: Page | None = None
    for page in pages:
        if not page.text.strip():
            continue
        start = 0
        while start < len(page.text):
            prefix = f"[Page {page.number}]\n"
            numbers = (page.number,)
            if start == 0 and previous and overlap:
                prefix = f"[Page {previous.number}]\n{previous.text[-overlap:]}\n\n" + prefix
                numbers = (previous.number, page.number)
            end = min(start + max_chars - len(prefix), len(page.text))
            if end < len(page.text):
                split = max(page.text.rfind("\n", start + (end - start) // 2, end), page.text.rfind(" ", start + (end - start) // 2, end))
                if split > start:
                    end = split + 1
            chunks.append(Chunk(prefix + page.text[start:end], numbers))
            if end == len(page.text):
                break
            start = max(start + 1, end - overlap)
        previous = page
    return chunks


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00ad": ""}))
    return re.sub(r"\s+", " ", re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)).strip()


@dataclass(frozen=True)
class Verification:
    verified: bool
    page: int | None
    score: float
    exact: bool = False

    @property
    def label(self) -> str:
        if self.verified:
            suffix = "" if self.exact else "; approximate match — compare wording"
            return f"✅ Verified in Text (Page {self.page}{suffix})"
        return "⚠️ Unverified — Check Manually"


def verify_quote(quote: str, pages: list[Page], threshold: float = 0.85) -> Verification:
    needle = normalize(quote)
    if not needle or len(needle) > 12000 or not re.search(r"\w", needle):
        return Verification(False, None, 0.0)
    best = Verification(False, None, 0.0)
    normalized_pages = [(page, normalize(page.text)) for page in pages]
    # Search all pages exactly first, avoiding expensive fuzzy comparisons when
    # a verbatim quote occurs late in a long agreement.
    for page, haystack in normalized_pages:
        if needle in haystack:
            score = SequenceMatcher(None, needle, needle, autojunk=False).ratio()
            return Verification(True, page.number, score, True)
    for page, haystack in normalized_pages:
        # Very short or purely numeric quotes require an exact match.
        if len(needle) < 16:
            continue
        # Anchor candidate windows on matching blocks, including late-page quotes.
        matcher = SequenceMatcher(None, needle, haystack, autojunk=False)
        blocks = sorted(matcher.get_matching_blocks(), key=lambda b: b.size, reverse=True)[:12]
        candidates = {max(0, b.b - b.a) for b in blocks if b.size}
        for start in candidates:
            for delta in (-8, 0, 8):
                candidate = haystack[max(0, start + delta):max(0, start + delta) + len(needle)]
                ratio = SequenceMatcher(None, needle, candidate, autojunk=False).ratio()
                # Fuzzy matches must not validate altered dollar amounts or deadlines.
                if re.findall(r"\d+(?:[.,]\d+)*", needle) != re.findall(r"\d+(?:[.,]\d+)*", candidate):
                    ratio = 0.0
                # A missing/added negation can invert a legal obligation.
                if re.findall(r"\b(?:not|never|no)\b", needle) != re.findall(r"\b(?:not|never|no)\b", candidate):
                    ratio = 0.0
                if ratio > best.score:
                    best = Verification(ratio >= threshold, page.number if ratio >= threshold else None, ratio)
    return best
