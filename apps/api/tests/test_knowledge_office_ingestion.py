"""Actual generated Office files and the existing public SQLite repository.

No mock text parser, OCR, semantic vectors, HTTP shortcut or external model.
"""
from __future__ import annotations

from datetime import date
import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET

from docx import Document
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
import pytest

from app.knowledge_files import KnowledgeFileError, parse_knowledge_file
from app.knowledge_parsers import office_package
from app.source_repository import SourceRepository
from app.source_types import SourceConnector, SourceItem


def word_file(power="1200"):
    document = Document()
    document.add_heading("曜核Z9产品资料", 0)
    document.add_heading("技术规格", 1)
    document.add_paragraph("精密稳压控制器，型号 YH-Z9；此段不推断为参数。")
    document.add_heading("电气", 2)
    table = document.add_table(rows=3, cols=3)
    for i, row in enumerate((("参数", "值", "单位"), ("额定功率", power, "W"), ("净重", "500", "g"))):
        for j, value in enumerate(row):
            table.cell(i, j).text = str(value)
    document.add_paragraph("使用方法：请先连接电源。")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def excel_file(power=1200):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "电气规格"
    for row in (("参数", "值", "单位"), ("额定功率", power, "W"), ("净重", 500, "g")):
        sheet.append(row)
    sheet["E1"] = "型号 YH-Z9"
    workbook.create_sheet("留空")
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def zipped_edit(data, path, change):
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, "w", ZIP_DEFLATED) as destination:
        for part in source.infolist():
            raw = source.read(part.filename)
            destination.writestr(part.filename, change(raw) if part.filename == path else raw)
    return output.getvalue()


def persist(repo, data, filename, *, workspace="workspace-a", owner="alice", source_id="source-1"):
    parsed = parse_knowledge_file(filename, data)
    connector = repo.upsert_connector(SourceConnector(
        id=f"files-{workspace}", workspace_id=workspace, platform="knowledge_files", protocol="local_file",
        account_or_rule="资料导入", frequency="manual", permissions=(),
    ))
    source = repo.save_source_items(workspace, [SourceItem(
        id=source_id, workspace_id=workspace, connector_id=connector.id, source_label="file_import",
        source_url=None, content_type=parsed.content_type, author=None, published_at=None,
        body=parsed.full_text, subtitles=None, media_references=(), license="user_provided",
        title=parsed.title, file_reference=parsed.filename, content_hash=parsed.content_hash,
        sections=parsed.sections, parameters=parsed.parameters,
    )])[0]
    item = repo.save_source_item_to_knowledge(workspace, source.id, verified_owner_user_id=owner)
    return parsed, source, item


@pytest.mark.parametrize("factory, filename, locator", [
    (word_file, "产品.docx", "表1!R2C2"),
    (excel_file, "产品.xlsx", "'电气规格'!B2"),
])
def test_real_office_parse_public_ingestion_search_and_fixed_evidence(tmp_path, factory, filename, locator):
    original = tmp_path / filename
    original.write_bytes(factory())
    before = original.read_bytes()
    repo = SourceRepository(tmp_path / "knowledge.sqlite3")
    parsed, source, item = persist(repo, original.read_bytes(), filename)
    assert original.read_bytes() == before
    assert parsed.content_hash == hashlib.sha256(before).hexdigest()
    assert source.body == parsed.full_text == item.body
    assert item.sections == parsed.sections and item.parameters == parsed.parameters
    power = next(p for p in item.parameters if p.key == "额定功率")
    assert (power.value, power.unit, power.locator) == ("1200", "W", locator)
    rows, total = repo.search_knowledge_items("workspace-a", query="YH-Z9", user_id="alice")
    assert total == 1 and rows[0]["id"] == item.id
    reference = repo.get_knowledge_reference_readable_by("workspace-a", rows[0]["id"], rows[0]["version"], user_id="alice")
    assert next(s for s in reference.sections if s.locator == locator).text == "1200"
    reopened = SourceRepository(tmp_path / "knowledge.sqlite3")
    assert reopened.get_knowledge_version_readable_by("workspace-a", item.id, "1", user_id="alice").sections == parsed.sections
    assert repo.search_knowledge_items("workspace-a", query="YH-Z9", user_id="bob") == ([], 0)
    assert repo.search_knowledge_items("other", query="YH-Z9", user_id="alice") == ([], 0)
    assert repo.search_knowledge_items("workspace-a", query="UNMATCHED-QX8800", user_id="alice") == ([], 0)


@pytest.mark.parametrize("factory, filename", [(word_file,"产品.docx"), (excel_file,"产品.xlsx")])
def test_office_duplicate_revision_and_historical_locator_use_existing_store(tmp_path, factory, filename):
    repo = SourceRepository(tmp_path / "knowledge.sqlite3")
    first_bytes = factory()
    first, _, saved = persist(repo, first_bytes, filename)
    _, _, repeated = persist(repo, first_bytes, "副本" + Path(filename).suffix, source_id="source-copy")
    assert repeated.id == saved.id and repeated.version == "1"
    updated, _, current = persist(repo, factory(1500), filename, source_id="source-update")
    assert current.id == saved.id and current.version == "2"
    old = repo.get_knowledge_reference_readable_by("workspace-a", saved.id, "1", user_id="alice")
    assert old.parameters == first.parameters
    assert next(p for p in current.parameters if p.key == "额定功率").value == "1500"
    assert next(p for p in old.parameters if p.key == "额定功率").value == "1200"
    _, _, old_reimport = persist(repo, first_bytes, filename, source_id="source-old-copy")
    assert old_reimport.version == "2" and old_reimport.content_hash == updated.content_hash
    grant = repo.grant_knowledge_item("workspace-a", saved.id, "target", user_id="alice")
    assert repo.search_knowledge_items("target", query="YH-Z9", user_id="bob")[1] == 1
    repo.revoke_knowledge_grant("workspace-a", saved.id, grant.id, user_id="alice")
    assert repo.search_knowledge_items("target", query="YH-Z9", user_id="bob") == ([], 0)
    repo.set_knowledge_item_use_state("workspace-a", saved.id, "excluded")
    assert repo.search_knowledge_items("workspace-a", query="YH-Z9", user_id="alice") == ([], 0)


def test_docx_body_order_heading_hierarchy_blank_paragraph_and_no_invented_pages():
    parsed = parse_knowledge_file("产品.DOCX", word_file())
    assert parsed.title == "曜核Z9产品资料"
    assert next(s for s in parsed.sections if s.locator == "正文第3段").title == "曜核Z9产品资料 / 技术规格"
    assert next(s for s in parsed.sections if s.locator == "表1!R2C2").title == "曜核Z9产品资料 / 技术规格 / 电气"
    assert parsed.full_text.index("精密稳压") < parsed.full_text.index("额定功率") < parsed.full_text.index("使用方法")
    assert all("页" not in section.locator for section in parsed.sections)
    assert all(p.key != "型号" for p in parsed.parameters)
    document = Document()
    document.add_paragraph("")
    document.add_paragraph("第二段")
    stream = BytesIO()
    document.save(stream)
    assert parse_knowledge_file("段落.docx", stream.getvalue()).sections[0].locator == "正文第2段"


def test_docx_merged_cells_and_nested_table_have_unique_real_origins():
    document = Document()
    table = document.add_table(rows=3, cols=3)
    for i, row in enumerate((("参数","值","单位"),("功率","1000","W"),("","1200","W"))):
        for j, value in enumerate(row):
            table.cell(i,j).text = value
    table.cell(1,0).merge(table.cell(2,0))
    nested = table.cell(2,2).add_table(rows=2,cols=2)
    nested.cell(0,0).text="参数"
    nested.cell(0,1).text="值"
    nested.cell(1,0).text="型号"
    nested.cell(1,1).text="Z9"
    stream=BytesIO()
    document.save(stream)
    parsed = parse_knowledge_file("合并.docx", stream.getvalue())
    assert len({s.locator for s in parsed.sections}) == len(parsed.sections)
    merged = next(s for s in parsed.sections if s.locator == "表1!R2C1:R3C1")
    assert "功率" in merged.text and "合并单元格" in merged.text
    assert {(p.value,p.locator) for p in parsed.parameters if p.key=="功率"} == {("1000","表1!R2C2"),("1200","表1!R3C2")}
    assert next(p for p in parsed.parameters if p.key=="型号").locator == "表1!R3C3/表1!R2C2"


def test_docx_empty_tables_are_explicit_and_cannot_be_the_only_body():
    document=Document()
    document.add_table(rows=2,cols=2)
    stream=BytesIO()
    document.save(stream)
    with pytest.raises(KnowledgeFileError, match="没有可提取"):
        parse_knowledge_file("空表.docx", stream.getvalue())
    document.add_paragraph("真实正文")
    stream=BytesIO()
    document.save(stream)
    parsed=parse_knowledge_file("空表.docx",stream.getvalue())
    assert parsed.sections[0].locator == "表1" and "空表格" in parsed.sections[0].text
    assert parsed.parameters == ()


def test_xlsx_merged_cells_empty_sheet_hidden_data_types_and_units():
    workbook=Workbook()
    sheet=workbook.active
    sheet.title="O'Brien 参数"
    sheet.append(["参数","值","单位"])
    sheet.append(["功率",1000,"W"])
    sheet.append([None,1200,"W"])
    sheet.merge_cells("A2:A3")
    sheet["E1"]="中文与English型号M-03"
    sheet["E2"]="00123"
    sheet["E3"]=0
    sheet["E4"]=False
    sheet["E5"]=date(2026,10,18)
    sheet["E6"]=0.125
    sheet["E6"].number_format="0.0%"
    sheet["E7"]="#DIV/0!"
    sheet.row_dimensions[7].hidden=True
    workbook.create_sheet("空表")
    hidden=workbook.create_sheet("隐藏页")
    hidden.sheet_state="hidden"
    hidden["A1"]="隐藏原文"
    stream=BytesIO()
    workbook.save(stream)
    workbook.close()
    parsed=parse_knowledge_file("参数.xlsx",stream.getvalue())
    cells={s.locator:s for s in parsed.sections}
    assert "'O''Brien 参数'!A2:A3" in cells and "'O''Brien 参数'!A3" not in cells
    assert cells["'O''Brien 参数'!E2"].text == "00123"
    assert cells["'O''Brien 参数'!E3"].text == "0"
    assert cells["'O''Brien 参数'!E4"].text == "FALSE"
    assert cells["'O''Brien 参数'!E5"].text.startswith("2026-10-18")
    assert "0.125" in cells["'O''Brien 参数'!E6"].text and "0.0%" in cells["'O''Brien 参数'!E6"].text
    assert "错误值" in cells["'O''Brien 参数'!E7"].text and "隐藏" in cells["'O''Brien 参数'!E7"].text
    assert "空工作表" in cells["'空表'"].text
    assert "hidden" in cells["'隐藏页'!A1"].text
    assert [(p.value,p.unit,p.locator) for p in parsed.parameters] == [("1000","W","'O''Brien 参数'!B2"),("1200","W","'O''Brien 参数'!B3")]


def test_xlsx_formulas_keep_expression_cache_zero_error_and_no_calculation():
    workbook=Workbook()
    sheet=workbook.active
    sheet.title="公式"
    sheet.append(["参数","值","单位"])
    sheet.append(["计算功率","=600*2","W"])
    sheet.append(["缓存零","=0","W"])
    sheet.append(["缓存报错","=1/0","W"])
    sheet["D1"]=ArrayFormula(ref="D1:D2", text="=SUM(A10:A11)")
    sheet["F1"]=DataTableFormula(ref="F1:G2", r1="A1", r2="A2")
    stream=BytesIO()
    workbook.save(stream)
    workbook.close()
    def caches(xml):
        root=ET.fromstring(xml)
        ns={"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for cell in root.findall(".//s:c",ns):
            if cell.attrib["r"] in {"B3","B4"}:
                cell.find("s:v",ns).text="0" if cell.attrib["r"]=="B3" else "#DIV/0!"
                if cell.attrib["r"]=="B4":
                    cell.set("t","e")
        return ET.tostring(root)
    data=zipped_edit(stream.getvalue(),"xl/worksheets/sheet1.xml",caches)
    parsed=parse_knowledge_file("公式.xlsx",data)
    cells={s.locator:s.text for s in parsed.sections}
    assert "=600*2" in cells["'公式'!B2"] and "无可用缓存结果，未执行计算" in cells["'公式'!B2"]
    assert "缓存结果（未重新计算）：0" in cells["'公式'!B3"]
    assert "#DIV/0!" in cells["'公式'!B4"] and "错误值" in cells["'公式'!B4"]
    assert "数组公式，作用范围 D1:D2" in cells["'公式'!D1"]
    assert "数据表公式" in cells["'公式'!F1"] and "r1=A1" in cells["'公式'!F1"] and "r2=A2" in cells["'公式'!F1"]
    power=next(p for p in parsed.parameters if p.key=="计算功率")
    assert "=600*2" in power.value and power.value != "1200"


def test_blank_xlsx_is_not_a_success():
    workbook=Workbook()
    sheet=workbook.active
    sheet["A1"].number_format="0.00"
    stream=BytesIO()
    workbook.save(stream)
    workbook.close()
    with pytest.raises(KnowledgeFileError,match="空工作表"):
        parse_knowledge_file("空.xlsx",stream.getvalue())


@pytest.mark.parametrize("filename", ["损坏.docx", "损坏.xlsx", "旧.doc", "旧.xls", "宏.xlsm"])
def test_corrupt_or_unsupported_format_is_explicit(filename):
    with pytest.raises(KnowledgeFileError):
        parse_knowledge_file(filename,b"not an office archive")


@pytest.mark.parametrize("data, filename, path", [
    (word_file(),"损坏.docx","word/document.xml"),
    (excel_file(),"损坏.xlsx","xl/worksheets/sheet1.xml"),
])
def test_valid_zip_with_corrupt_xml_fails(data,filename,path):
    with pytest.raises(KnowledgeFileError):
        parse_knowledge_file(filename,zipped_edit(data,path,lambda _:b"<unclosed>"))


def test_xlsx_dtd_and_giant_merge_are_rejected_before_allocation():
    data=excel_file()
    dtd=b'<!DOCTYPE worksheet [<!ENTITY x "unsafe">]><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>&x;</sheetData></worksheet>'
    with pytest.raises(KnowledgeFileError):
        parse_knowledge_file("不可信.xlsx",zipped_edit(data,"xl/worksheets/sheet1.xml",lambda _:dtd))
    xml=b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/><mergeCells count="1"><mergeCell ref="A1:XFD1048576"/></mergeCells></worksheet>'
    with pytest.raises(KnowledgeFileError, match="合并区域过大"):
        parse_knowledge_file("超大合并.xlsx",zipped_edit(data,"xl/worksheets/sheet1.xml",lambda _:xml))


def test_package_expansion_and_ambiguous_duplicate_parts_are_rejected(monkeypatch):
    monkeypatch.setattr(office_package,"MAX_EXPANDED_BYTES",128)
    output=BytesIO()
    with ZipFile(output,"w",ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml",b"x"*1024)
    with pytest.raises(KnowledgeFileError,match="展开后过大"):
        parse_knowledge_file("压缩.docx",output.getvalue())
    monkeypatch.setattr(office_package,"MAX_EXPANDED_BYTES",64*1024*1024)
    with pytest.warns(UserWarning,match="Duplicate"):
        with ZipFile(output:=BytesIO(),"w",ZIP_DEFLATED) as archive:
            archive.writestr("part.xml",b"one")
            archive.writestr("part.xml",b"two")
    with pytest.raises(KnowledgeFileError,match="重复部件"):
        parse_knowledge_file("歧义.xlsx",output.getvalue())


def test_xlsx_parameter_region_ends_at_blank_row_instead_of_guessing_an_unlabelled_table():
    workbook=Workbook()
    sheet=workbook.active
    sheet.append(["参数","值","单位"])
    sheet.append(["功率",1200,"W"])
    sheet.append([None,None,None])
    sheet.append(["不属于参数表",999,"不可猜单位"])
    stream=BytesIO()
    workbook.save(stream)
    workbook.close()
    parsed=parse_knowledge_file("分区.xlsx",stream.getvalue())
    assert [parameter.key for parameter in parsed.parameters] == ["功率"]
    assert "不属于参数表" in parsed.full_text


@pytest.mark.parametrize("tag", ["sdt", "txbxContent", "altChunk", "ins", "del"])
def test_docx_unsupported_body_containers_never_silently_omit_text(tag):
    data=word_file()
    def insert(xml):
        root=ET.fromstring(xml)
        root.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body").append(
            ET.Element("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"+tag))
        return ET.tostring(root)
    with pytest.raises(KnowledgeFileError,match="尚未支持"):
        parse_knowledge_file("特殊正文.docx",zipped_edit(data,"word/document.xml",insert))


def test_package_part_and_encrypted_entry_limits_are_explicit(monkeypatch):
    import struct
    output=BytesIO()
    with ZipFile(output,"w",ZIP_DEFLATED) as archive:
        archive.writestr("a.xml",b"one")
        archive.writestr("b.xml",b"two")
    monkeypatch.setattr(office_package,"MAX_PACKAGE_PARTS",1)
    with pytest.raises(KnowledgeFileError,match="4096"):
        parse_knowledge_file("部件过多.xlsx",output.getvalue())
    monkeypatch.setattr(office_package,"MAX_PACKAGE_PARTS",4096)
    raw=bytearray(output.getvalue())
    central=raw.index(b"PK\x01\x02")
    struct.pack_into("<H",raw,6,1)
    struct.pack_into("<H",raw,central+8,1)
    with pytest.raises(KnowledgeFileError,match="已加密"):
        parse_knowledge_file("加密.docx",bytes(raw))


@pytest.mark.parametrize("factory,filename,part", [(word_file,"压缩损坏.docx","word/document.xml"), (excel_file,"压缩损坏.xlsx","xl/worksheets/sheet1.xml")])
def test_damaged_compressed_body_maps_to_a_file_error(factory,filename,part):
    import struct
    data=factory()
    raw=bytearray(data)
    with ZipFile(BytesIO(data)) as archive:
        offset=archive.getinfo(part).header_offset
    name_length,extra_length=struct.unpack_from("<HH",raw,offset+26)
    raw[offset+30+name_length+extra_length]=255
    with pytest.raises(KnowledgeFileError,match="不是可解析"):
        parse_knowledge_file(filename,bytes(raw))


def test_unsupported_office_compression_is_an_explicit_file_error():
    from zipfile import ZIP_BZIP2
    output=BytesIO()
    with ZipFile(output,"w",ZIP_BZIP2) as archive:
        archive.writestr("part.xml",b"test")
    with pytest.raises(KnowledgeFileError,match="压缩方式"):
        parse_knowledge_file("不支持压缩.xlsx",output.getvalue())
