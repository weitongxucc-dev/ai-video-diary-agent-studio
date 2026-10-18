"""Read actual Word paragraphs/tables; no layout engine or invented page numbers."""
from __future__ import annotations

import io
from zipfile import BadZipFile
from zlib import error as ZipCompressionError

from app.knowledge_files import (
    KnowledgeFileError, KnowledgeParameter, KnowledgeSection, ParsedKnowledgeFile,
    _content_hash, _parameters_from_text,
)
from app.knowledge_parsers.office_package import MAX_TABLE_CELLS, validate_office_package
from app.knowledge_parsers.office_tables import TableCell, explicit_parameters


def parse_docx(filename: str, data: bytes) -> ParsedKnowledgeFile:
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.oxml.exceptions import InvalidXmlError
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from lxml.etree import XMLSyntaxError
    except ImportError as exc:
        raise KnowledgeFileError("Word 解析组件未安装，请修复应用的 python-docx 依赖") from exc

    validate_office_package(data)
    sections: list[KnowledgeSection] = []
    parameters: list[KnowledgeParameter] = []
    headings: list[tuple[int, str]] = []
    real_text: list[str] = []

    def heading_level(paragraph: Paragraph) -> int | None:
        # Outline level follows paragraph overrides and its inherited style.
        properties = [paragraph._p.pPr]
        style = paragraph.style
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            properties.append(style.element.pPr)
            if style.style_id == "Title":
                return 0
            style = style.base_style
        for properties_item in properties:
            if properties_item is not None:
                outline = properties_item.find(qn("w:outlineLvl"))
                if outline is not None:
                    value = int(outline.get(qn("w:val")))
                    return value + 1 if 0 <= value <= 8 else None
        return None

    def paragraph_text(paragraph: Paragraph, locator: str, *, track_headings: bool) -> None:
        text = paragraph.text.strip()
        if not text:
            return
        level = heading_level(paragraph) if track_headings else None
        if level is not None:
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, text))
        title = " / ".join(value for _, value in headings)
        sections.append(KnowledgeSection(locator=locator, title=title, text=text))
        parameters.extend(_parameters_from_text(locator, text))
        real_text.append(text)

    def table_content(table: Table, locator: str) -> None:
        column_count = len(table.columns)
        row_count = len(table.rows)
        if row_count * column_count > MAX_TABLE_CELLS or any(
            sum(cell.grid_span for cell in row._tr.tc_lst) > MAX_TABLE_CELLS
            for row in table.rows
        ):
            raise KnowledgeFileError("Word 表格过大，请拆分表格后导入（最多 100000 个网格位置）")
        rows = []
        cells = {}
        positions = {}
        for row_number, row in enumerate(table.rows, 1):
            entries = []
            for column_number, cell in enumerate(row.cells, 1 + row.grid_cols_before):
                key = cell._tc
                cells.setdefault(key, cell)
                positions.setdefault(key, []).append((row_number, column_number))
                entries.append(key)
            rows.append([None] * row.grid_cols_before + entries)
        references = {}
        for key, coordinates in positions.items():
            first_row, first_column = coordinates[0]
            last_row, last_column = coordinates[-1]
            span = f"R{first_row}C{first_column}"
            if (first_row, first_column) != (last_row, last_column):
                span += f":R{last_row}C{last_column}"
            references[key] = f"{locator}!{span}"
        if not any(cell.text.strip() for cell in cells.values()) and not any(cell.tables for cell in cells.values()):
            sections.append(KnowledgeSection(locator, " / ".join(value for _, value in headings),
                f"空表格（{row_count} 行，{column_count} 列；未发现文字）"))
            return
        table_rows = []
        for keys in rows:
            table_rows.append([TableCell(cells[key].text, references[key]) if key is not None else TableCell("", "") for key in keys])
        parameters.extend(explicit_parameters(table_rows))
        for key, cell in cells.items():
            location = references[key]
            text = cell.text.strip()
            if text:
                real_text.append(text)
                prefix = f"合并单元格 {location}，文字位于主单元格。\n" if len(positions[key]) > 1 else ""
                sections.append(KnowledgeSection(location, " / ".join(value for _, value in headings), prefix + text))
                parameters.extend(_parameters_from_text(location, text))
            nested = 0
            for block in cell.iter_inner_content():
                if isinstance(block, Table):
                    nested += 1
                    table_content(block, f"{location}/表{nested}")

    try:
        document = Document(io.BytesIO(data))
        # These containers are not traversed by python-docx's public body reader.
        # Refuse partial text instead of silently losing imported parameters.
        if any(next(document.element.body.iter(qn(tag)), None) is not None
               for tag in ("w:altChunk", "w:txbxContent", "w:sdt", "w:ins", "w:del")):
            raise KnowledgeFileError("Word 正文包含尚未支持的嵌入内容、文本框、内容控件或修订，请另存为普通正文/表格后导入")
        paragraph_number = table_number = 0
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                paragraph_number += 1
                paragraph_text(block, f"正文第{paragraph_number}段", track_headings=True)
            elif isinstance(block, Table):
                table_number += 1
                table_content(block, f"表{table_number}")
        title = document.core_properties.title.strip() or next((s.text for s in sections if s.title), "")
    except (BadZipFile, ZipCompressionError, KeyError, IndexError, TypeError, ValueError, XMLSyntaxError, InvalidXmlError) as exc:
        if isinstance(exc, KnowledgeFileError):
            raise
        raise KnowledgeFileError(f"{filename} 不是可解析的 Word 文件，请检查文件是否损坏或重新另存为 docx") from exc
    if not real_text:
        raise KnowledgeFileError(f"{filename} 没有可提取的 Word 正文或表格文字；图片文字需 OCR，不能当作已解析")
    return ParsedKnowledgeFile(filename=filename,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        content_hash=_content_hash(data), title=title or filename.rsplit(".", 1)[0],
        full_text="\n\n".join(f"[{s.locator}]\n{s.text}" for s in sections),
        sections=tuple(sections), parameters=tuple(parameters))
