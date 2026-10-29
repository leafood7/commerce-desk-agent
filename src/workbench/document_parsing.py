"""Bounded, deterministic parsers; uploaded text is data, never tool instructions."""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

MAX_TEXT = 1_000_000


class IngestionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Block:
    text: str
    page: int | None = None
    section: str = ""


def validate_container(path: Path, extension: str):
    if extension not in {".txt", ".md", ".pdf", ".docx"}:
        raise IngestionError("unsupported_format")
    if extension == ".pdf" and not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
        raise IngestionError("invalid_pdf")
    if extension == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if (
                    len(members) > 2000
                    or sum(item.file_size for item in members) > 50 * 1024 * 1024
                ):
                    raise IngestionError("archive_limit_exceeded")
                if any(item.flag_bits & 1 for item in members):
                    raise IngestionError("encrypted_document")
                if not {"[Content_Types].xml", "word/document.xml"} <= set(archive.namelist()):
                    raise IngestionError("invalid_docx")
        except (zipfile.BadZipFile, OSError) as exc:
            raise IngestionError("invalid_docx") from exc


def text_blocks(text, page=None):
    headings, paragraphs, buffer = [], [], []

    def flush():
        if buffer:
            paragraphs.append(Block("\n".join(buffer).strip(), page, " / ".join(headings)[:500]))
            buffer.clear()

    for line in text.replace("\r\n", "\n").splitlines():
        match = re.fullmatch(r"\s*(#{1,6})\s+(.+)", line)
        if match:
            flush()
            level = len(match.group(1))
            headings = headings[: level - 1] + [match.group(2)]
            paragraphs.append(Block(line.strip(), page, " / ".join(headings)[:500]))
        elif not line.strip():
            flush()
        else:
            buffer.append(line.rstrip())
    flush()
    return paragraphs


def parse_document(path: Path, extension: str):
    validate_container(path, extension)
    warnings = []
    if extension in {".txt", ".md"}:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            raise IngestionError("utf8_required") from exc
        blocks = text_blocks(text)
    elif extension == ".pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise IngestionError("encrypted_document")
            if len(reader.pages) > 200:
                raise IngestionError("page_limit_exceeded")
            blocks = []
            total = 0
            for position, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total += len(text)
                if total > MAX_TEXT:
                    raise IngestionError("text_limit_exceeded")
                if not text.strip():
                    warnings.append({"code": "page_without_text", "page": position})
                blocks.extend(text_blocks(text, position))
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError("pdf_parse_failed") from exc
    else:
        try:
            with zipfile.ZipFile(path) as archive, archive.open("word/document.xml") as member:
                xml = member.read(4 * 1024 * 1024 + 1)
            if len(xml) > 4 * 1024 * 1024 or b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                raise IngestionError("xml_limit_exceeded")
            tree = ElementTree.fromstring(xml)
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            body = tree.find("w:body", namespace)
            blocks, section = [], ""
            for child in body if body is not None else []:
                if child.tag.endswith("}tbl"):
                    for row in child.findall("w:tr", namespace):
                        cells = [
                            " ".join(node.text or "" for node in cell.findall(".//w:t", namespace))
                            for cell in row.findall("w:tc", namespace)
                        ]
                        if any(cells):
                            blocks.append(Block(" | ".join(cells), section=section))
                else:
                    text = "".join(node.text or "" for node in child.findall(".//w:t", namespace))
                    style = child.find("w:pPr/w:pStyle", namespace)
                    if style is not None and "heading" in "".join(style.attrib.values()).lower():
                        section = text[:500]
                    if text.strip():
                        blocks.append(Block(text.strip(), section=section))
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError("docx_parse_failed") from exc
    if sum(len(block.text) for block in blocks) > MAX_TEXT:
        raise IngestionError("text_limit_exceeded")
    if not blocks or not any(block.text.strip() for block in blocks):
        raise IngestionError("no_extractable_text")
    return blocks, {
        "parser": extension.lstrip("."),
        "warnings": warnings,
        "block_count": len(blocks),
        "text_chars": sum(len(b.text) for b in blocks),
    }


def chunks_from_blocks(blocks, strategy="structural", size=600, overlap=80):
    if strategy not in {"structural", "fixed"} or not 0 <= overlap < size:
        raise ValueError("Invalid chunk configuration")
    result = []

    def emit(text, page_start, page_end, section):
        text = text.strip()
        if text:
            result.append(
                {
                    "content": text,
                    "page_start": page_start,
                    "page_end": page_end,
                    "section": section[:500],
                    "position": len(result),
                }
            )

    if strategy == "fixed":
        # Respect page boundaries so a citation never invents a page location.
        groups = []
        for block in blocks:
            if groups and groups[-1][0] == block.page:
                groups[-1][1].append(block.text)
            else:
                groups.append((block.page, [block.text]))
        for page, pieces in groups:
            text = "\n\n".join(pieces)
            for start in range(0, len(text), size - overlap):
                emit(text[start : start + size], page, page, "")
    else:
        buffer, section, first_page, last_page = "", "", None, None
        for block in blocks:
            if buffer and (
                block.section != section
                or block.page != last_page
                or len(buffer) + len(block.text) + 2 > size
            ):
                emit(buffer, first_page, last_page, section)
                buffer = ""
            section = block.section
            if len(block.text) > size:
                if buffer:
                    emit(buffer, first_page, last_page, section)
                    buffer = ""
                for start in range(0, len(block.text), size - overlap):
                    emit(block.text[start : start + size], block.page, block.page, section)
            else:
                if not buffer:
                    first_page = block.page
                buffer = buffer + ("\n\n" if buffer else "") + block.text
                last_page = block.page
        if buffer:
            emit(buffer, first_page, last_page, section)
    if len(result) > 2500:
        raise IngestionError("chunk_limit_exceeded")
    return result
