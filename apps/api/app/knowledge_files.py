"""知识文件解析：全文、真实出处定位和显式参数。

出处只用载体真实存在的定位：PDF 页码、Markdown 段落、Word 段落/表格、Excel 工作表/单元格。
扫描件没有文本层时明确失败，不 OCR、不猜参数。
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
PDF_SUFFIXES = frozenset({".pdf"})
WORD_SUFFIXES = frozenset({".docx"})
EXCEL_SUFFIXES = frozenset({".xlsx"})
SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES | PDF_SUFFIXES | WORD_SUFFIXES | EXCEL_SUFFIXES


class KnowledgeFileError(ValueError):
    """文件无法解析为可核对正文时抛出，调用方必须显式失败。"""


@dataclass(frozen=True)
class KnowledgeSection:
    locator: str
    title: str
    text: str


@dataclass(frozen=True)
class KnowledgeParameter:
    key: str
    value: str
    unit: str | None
    locator: str


@dataclass(frozen=True)
class ParsedKnowledgeFile:
    filename: str
    content_type: str
    content_hash: str
    title: str
    full_text: str
    sections: tuple[KnowledgeSection, ...]
    parameters: tuple[KnowledgeParameter, ...] = field(default_factory=tuple)


def parse_knowledge_file(filename: str, data: bytes) -> ParsedKnowledgeFile:
    suffix = Path(filename).suffix.lower()
    if suffix in MARKDOWN_SUFFIXES:
        return _parse_markdown(filename, data)
    if suffix in PDF_SUFFIXES:
        return _parse_pdf(filename, data)
    if suffix in WORD_SUFFIXES:
        from app.knowledge_parsers.word import parse_docx
        return parse_docx(filename, data)
    if suffix in EXCEL_SUFFIXES:
        from app.knowledge_parsers.excel import parse_xlsx
        return parse_xlsx(filename, data)
    raise KnowledgeFileError(f"不支持的知识文件格式：{suffix or filename}")


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise KnowledgeFileError("文本文件不是 UTF-8 编码，无法核对正文")


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_markdown(filename: str, data: bytes) -> ParsedKnowledgeFile:
    text = _decode_text(data)
    document = _markdown_body(text)
    sections = _markdown_sections(document)
    if not sections:
        raise KnowledgeFileError(f"{filename} 没有可用的正文内容")
    parameters: list[KnowledgeParameter] = []
    for section in sections:
        parameters.extend(_parameters_from_text(section.locator, section.text))
    return ParsedKnowledgeFile(
        filename=filename,
        content_type="text/markdown" if Path(filename).suffix.lower() in {".md", ".markdown"} else "text/plain",
        content_hash=_content_hash(data),
        title=sections[0].title or Path(filename).stem,
        full_text=document,
        sections=tuple(sections),
        parameters=tuple(parameters),
    )


def _markdown_body(text: str) -> str:
    """去掉 frontmatter，保留正文和标题标记作为完整正文。"""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for position in range(1, len(lines)):
            if lines[position].strip() == "---":
                return "\n".join(lines[position + 1 :]).strip("\n")
        raise KnowledgeFileError("Markdown frontmatter 没有结束标记")
    return text.strip("\n")


def _markdown_sections(document: str) -> list[KnowledgeSection]:
    """按 Markdown 标题分段；段落文本保留原标题行，标题层级不丢失。"""
    blocks: list[tuple[str, list[str]]] = []
    current_title = ""
    current_heading = ""
    current_lines: list[str] = []
    for line in document.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if heading:
            if current_lines or current_title:
                blocks.append((current_title, current_lines))
            current_title = heading.group(2).strip()
            current_heading = line.rstrip()
            current_lines = [current_heading]
            continue
        current_lines.append(line)
    blocks.append((current_title, current_lines))
    sections: list[KnowledgeSection] = []
    for position, (title, lines_of_section) in enumerate(blocks, start=1):
        content = "\n".join(lines_of_section).strip("\n")
        if not content.strip():
            continue
        sections.append(
            KnowledgeSection(
                locator=f"第{position}段",
                title=title,
                text=content,
            )
        )
    return sections





def _parse_pdf(filename: str, data: bytes) -> ParsedKnowledgeFile:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except PdfReadError as exc:
        raise KnowledgeFileError(f"{filename} 不是可解析的 PDF：{exc}") from exc
    if not pages or not any(page.strip() for page in pages):
        raise KnowledgeFileError(f"{filename} 没有可提取的文本层，需人工确认或提供文本版本")
    sections: list[KnowledgeSection] = []
    parameters: list[KnowledgeParameter] = []
    full_pages: list[str] = []
    for number, page_text in enumerate(pages, start=1):
        text = page_text.strip()
        full_pages.append(text)
        if not text:
            continue
        locator = f"第{number}页"
        sections.append(KnowledgeSection(locator=locator, title=locator, text=text))
        parameters.extend(_parameters_from_text(locator, text))
    return ParsedKnowledgeFile(
        filename=filename,
        content_type="application/pdf",
        content_hash=_content_hash(data),
        title=Path(filename).stem,
        full_text="\n\n".join(full_pages),
        sections=tuple(sections),
        parameters=tuple(parameters),
    )


_PARAMETER_TABLE_HEADER = ("参数", "值", "单位")
_PARAMETER_LINE = re.compile(r"^\s*[-*]\s+([^\s:：=]+)\s*[:：=]\s*(.+?)\s*$")


def _parameters_from_text(locator: str, text: str) -> list[KnowledgeParameter]:
    """只解析两种显式格式：参数表格和“- 参数: 值”列表项。

    散文里的数字和没有键的句子不构成参数。
    """
    parameters: list[KnowledgeParameter] = []
    lines = text.splitlines()
    for position, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|"):
            if "|" not in stripped[1:]:
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] == _PARAMETER_TABLE_HEADER[0] and cells[1] == _PARAMETER_TABLE_HEADER[1]:
                for row in lines[position + 2 :]:
                    row_cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                    if len(row_cells) < 2 or not row_cells[0] or set(row_cells[0]) <= {"-", ":"}:
                        break
                    unit = row_cells[2] if len(row_cells) > 2 and row_cells[2] not in {"-", "—"} else None
                    parameters.append(
                        KnowledgeParameter(
                            key=row_cells[0],
                            value=row_cells[1],
                            unit=unit,
                            locator=locator,
                        )
                    )
            continue
        matched = _PARAMETER_LINE.match(line)
        if matched:
            value, unit = _split_value_unit(matched.group(2))
            parameters.append(
                KnowledgeParameter(
                    key=matched.group(1).strip(),
                    value=value,
                    unit=unit,
                    locator=locator,
                )
            )
    return parameters


def _split_value_unit(raw: str) -> tuple[str, str | None]:
    """单位只在显式写法 `值（单位）` 下记录。"""
    match = re.match(r"^(.*?)\s*[（(]([^（）()]+)[）)]\s*$", raw)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return raw.strip(), None