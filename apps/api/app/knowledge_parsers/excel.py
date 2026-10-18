"""Read worksheet/cell evidence, explicit units, merges and unexecuted formulas."""
from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError
from zlib import error as ZipCompressionError

from app.knowledge_files import KnowledgeFileError, KnowledgeSection, ParsedKnowledgeFile, _content_hash
from app.knowledge_parsers.office_package import MAX_TABLE_CELLS, validate_office_package
from app.knowledge_parsers.office_tables import TableCell, explicit_parameters


_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    return str(value)


def parse_xlsx(filename: str, data: bytes) -> ParsedKnowledgeFile:
    try:
        from defusedxml.ElementTree import fromstring
        from defusedxml.common import DefusedXmlException
        from openpyxl import load_workbook
        from openpyxl.cell.cell import MergedCell
        from openpyxl.utils.cell import quote_sheetname, range_boundaries
        from openpyxl.utils.exceptions import InvalidFileException
        from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
        from lxml.etree import XMLSyntaxError
    except ImportError as exc:
        raise KnowledgeFileError("Excel 解析组件未安装，请修复应用的 openpyxl/defusedxml 依赖") from exc
    validate_office_package(data)
    workbook = cached = None
    try:
        # openpyxl expands merged cells on load. Check their real ranges before
        # allocation; archive-byte bounds alone do not bound a sparse merge.
        with ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                    continue
                xml = fromstring(archive.read(name), forbid_dtd=True)
                if sum(1 for _ in xml.iter(f"{_MAIN_NS}c")) > MAX_TABLE_CELLS:
                    raise KnowledgeFileError("Excel 工作表过大，请拆分后导入（每表最多 100000 个单元格位置）")
                merge_cells = 0
                for merged in xml.iter(f"{_MAIN_NS}mergeCell"):
                    left, top, right, bottom = range_boundaries(merged.attrib["ref"])
                    merge_cells += (right - left + 1) * (bottom - top + 1)
                    if merge_cells > MAX_TABLE_CELLS:
                        raise KnowledgeFileError("Excel 合并区域过大，请拆分后导入（每表最多 100000 个单元格位置）")
        workbook = load_workbook(io.BytesIO(data), data_only=False, keep_links=False)
        cached = load_workbook(io.BytesIO(data), data_only=True, keep_links=False)
        sections: list[KnowledgeSection] = []
        parameters = []
        has_values = False
        for sheet in workbook.worksheets:
            sheet_name = quote_sheetname(sheet.title)
            if sheet.max_row * sheet.max_column > MAX_TABLE_CELLS:
                raise KnowledgeFileError("Excel 工作表范围过大，请移除远端空白格式或拆分后导入（每表最多 100000 个单元格位置）")
            merges = {}
            for merged in sheet.merged_cells.ranges:
                for row in range(merged.min_row, merged.max_row + 1):
                    for col in range(merged.min_col, merged.max_col + 1):
                        merges[(row, col)] = merged
            rows = []
            sheet_has_values = False
            for row in sheet.iter_rows():
                table_row = []
                for cell in row:
                    merged = merges.get((cell.row, cell.column))
                    anchor = sheet.cell(merged.min_row, merged.min_col) if merged else cell
                    coordinate = str(merged) if merged else cell.coordinate
                    locator = f"{sheet_name}!{coordinate}"
                    raw = anchor.value
                    if raw is None:
                        table_row.append(TableCell("", locator))
                        continue
                    cached_cell = cached[sheet.title][anchor.coordinate]
                    formula = anchor.data_type == "f"
                    if formula:
                        if isinstance(raw, ArrayFormula):
                            expression = raw.text
                            if not expression:
                                raise KnowledgeFileError("Excel 数组公式缺少原表达式，无法核对，请重新另存为后导入")
                            formula_kind = f"数组公式，作用范围 {raw.ref}"
                        elif isinstance(raw, DataTableFormula):
                            expression = "数据表公式：" + ", ".join(f"{key}={value}" for key, value in raw)
                            formula_kind = f"数据表范围 {raw.ref}"
                        else:
                            expression = str(raw)
                            formula_kind = "公式"
                        result = "无可用缓存结果，未执行计算" if cached_cell.value is None else f"缓存结果（未重新计算）：{_scalar(cached_cell.value)}"
                        text = f"{expression}\n{formula_kind}；{result}"
                    else:
                        text = _scalar(raw)
                    if anchor.number_format not in {"General", "@"}:
                        text += f"\n原单元格显示格式：{anchor.number_format}（正文保留原始值，不模拟 Excel 显示）"
                    if anchor.data_type == "e" or cached_cell.data_type == "e":
                        text += "\n该单元格记录错误值，不能当作有效参数结果"
                    table_row.append(TableCell(text, locator))
                    if isinstance(cell, MergedCell):
                        continue
                    if not str(raw).strip():
                        continue
                    sheet_has_values = has_values = True
                    annotations = []
                    if merged:
                        annotations.append(f"合并区域 {merged}；主单元格 {anchor.coordinate}")
                    if sheet.sheet_state != "visible":
                        annotations.append(f"工作表状态：{sheet.sheet_state}")
                    if sheet.row_dimensions[cell.row].hidden or sheet.column_dimensions[cell.column_letter].hidden:
                        annotations.append("原文件中该行或列隐藏")
                    if annotations:
                        text = "；".join(annotations) + "\n" + text
                    sections.append(KnowledgeSection(locator=locator, title=sheet.title, text=text))
                rows.append(table_row)
            parameters.extend(explicit_parameters(rows))
            if not sheet_has_values:
                sections.append(KnowledgeSection(locator=sheet_name, title=sheet.title,
                    text="空工作表（未发现非空单元格，不作为参数证据）"))
        if not has_values:
            raise KnowledgeFileError(f"{filename} 没有可提取的单元格内容，空工作表不能当作解析成功")
        return ParsedKnowledgeFile(filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content_hash=_content_hash(data), title=workbook.properties.title or Path(filename).stem,
            full_text="\n\n".join(f"[{section.locator}]\n{section.text}" for section in sections),
            sections=tuple(sections), parameters=tuple(parameters))
    except (BadZipFile, ZipCompressionError, KeyError, IndexError, TypeError, ValueError, ParseError, XMLSyntaxError, DefusedXmlException, InvalidFileException) as exc:
        if isinstance(exc, KnowledgeFileError):
            raise
        raise KnowledgeFileError(f"{filename} 不是可解析的 Excel 文件，请检查文件是否损坏或重新另存为 xlsx") from exc
    finally:
        if workbook is not None:
            workbook.close()
        if cached is not None:
            cached.close()
