from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.knowledge_files import KnowledgeFileError, parse_knowledge_file
from app.local_members import LocalMemberService
from app.source_repository import SourceRepository
from tests.pdf_fixture import _text_pdf

MARKDOWN = """# 产品资料

## 基本参数

| 参数 | 值 | 单位 |
| --- | --- | --- |
| 净重 | 500 | g |
| 保修期 | 24 | 月 |

## 使用说明

- 额定功率: 1200（W）
- 噪声: 45

正文段落保留原样。
""" + "\n\n".join(f"补充说明段落 {index}：产品结构与使用环境描述。" for index in range(1, 40))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3")
    )
    # 读取知识必须带已验证身份：这里用真实成员令牌，不拿空令牌当 owner。
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    owner, owner_token = service.join(service.create_invite_code(), "所有者")
    service.set_role(owner.member_id, "owner")
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-a"),
    )
    return TestClient(main_module.app, headers={"X-Local-Member-Token": owner_token})


def _import_file(client: TestClient, filename: str, data: bytes, workspace: str | None = None):
    headers = {"x-test-workspace": workspace} if workspace else {}
    return client.post(
        "/api/source-items/file",
        json={"filename": filename, "content_base64": base64.b64encode(data).decode()},
        headers=headers,
    )


def _save(client: TestClient, source_item_id: str, workspace: str | None = None):
    headers = {"x-test-workspace": workspace} if workspace else {}
    return client.post(
        f"/api/source-items/{source_item_id}/save-to-knowledge",
        json={},
        headers=headers,
    )


def test_markdown_import_keeps_full_body_headings_and_parameters(client):
    source = _import_file(client, "产品资料.md", MARKDOWN.encode())
    assert source.status_code == 200
    body = source.json()
    assert "# 产品资料" in body["body"]
    assert "## 基本参数" in body["body"]
    # 全文不被 600 字符摘要截断
    assert len(body["body"]) > 600
    locators = [section["locator"] for section in body["sections"]]
    assert locators == ["第1段", "第2段", "第3段"]
    assert body["sections"][0]["title"] == "产品资料"
    assert body["sections"][2]["title"] == "使用说明"
    table_and_list = {
        (parameter["key"], parameter["value"], parameter["unit"], parameter["locator"])
        for parameter in body["parameters"]
    }
    assert ("净重", "500", "g", "第2段") in table_and_list
    assert ("保修期", "24", "月", "第2段") in table_and_list
    assert ("额定功率", "1200", "W", "第3段") in table_and_list
    assert ("噪声", "45", None, "第3段") in table_and_list


def test_identical_copy_does_not_add_version_and_update_keeps_old_version(client):
    first = _import_file(client, "产品资料.md", MARKDOWN.encode()).json()
    first_saved = _save(client, first["id"]).json()

    # 同一成员的同内容副本复用原件回执，也不新增知识条目或版本。
    copy_source = _import_file(client, "产品资料-副本.md", MARKDOWN.encode()).json()
    assert copy_source["id"] == first["id"]
    copy_saved = _save(client, copy_source["id"]).json()
    assert copy_saved["id"] == first_saved["id"]
    assert copy_saved["version"] == first_saved["version"]

    detail = client.get(f"/api/knowledge-items/{first_saved['id']}").json()
    assert [version["version"] for version in detail["versions"]] == ["1"]

    # 原文件内容更新：同一知识条目追加版本，旧版仍可回查。
    updated = MARKDOWN.replace("净重 | 500", "净重 | 600")
    updated_source = _import_file(client, "产品资料.md", updated.encode()).json()
    updated_saved = _save(client, updated_source["id"]).json()
    assert updated_saved["id"] == first_saved["id"]
    assert updated_saved["version"] == "2"
    assert "600" in updated_saved["body"]

    detail = client.get(f"/api/knowledge-items/{first_saved['id']}").json()
    assert [version["version"] for version in detail["versions"]] == ["1", "2"]
    old_version = detail["versions"][0]
    assert "500" in old_version["body"]
    assert any(
        parameter["key"] == "净重" and parameter["value"] == "500"
        for parameter in old_version["parameters"]
    )
    assert detail["content_hash"] != old_version["content_hash"]


def test_pdf_import_uses_real_page_locators(client):
    pdf = _text_pdf(["First page body\n- Power: 700", "Second page body"])
    source = _import_file(client, "manual.pdf", pdf)
    assert source.status_code == 200
    body = source.json()
    assert [section["locator"] for section in body["sections"]] == ["第1页", "第2页"]
    assert body["parameters"] == [
        {"key": "Power", "value": "700", "unit": None, "locator": "第1页"}
    ]

    saved = _save(client, body["id"]).json()
    detail = client.get(f"/api/knowledge-items/{saved['id']}").json()
    assert [version["version"] for version in detail["versions"]] == ["1"]
    assert [section["locator"] for section in detail["versions"][0]["sections"]] == ["第1页", "第2页"]


def test_pdf_without_text_layer_fails_and_writes_nothing(client):
    scanned = _pdf_pages("scan", "scan")
    response = _import_file(client, "扫描件.pdf", scanned)
    assert response.status_code == 422
    assert "第1、2页" in response.json()["detail"]
    assert client.get("/api/sources/items").json()["total_count"] == 0


def test_mixed_pdf_reports_unread_pages_and_writes_nothing(client):
    mixed = _pdf_pages("Power: 700", "scan", "Last text page", "drawing")
    response = _import_file(client, "mixed.pdf", mixed)
    assert response.status_code == 422
    assert "第2、4页" in response.json()["detail"]
    assert "未导入整份资料" in response.json()["detail"]
    assert client.get("/api/sources/items").json()["total_count"] == 0


def _pdf_pages(*kinds):
    """Real PdfWriter pages: empty, text, embedded bitmap and painted vector."""
    import io
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    for kind in kinds:
        if kind not in {"blank", "scan", "drawing", "state_blank"}:
            writer.add_page(PdfReader(io.BytesIO(_text_pdf([kind]))).pages[0])
            continue
        page = writer.add_blank_page(width=612, height=792)
        if kind == "blank":
            continue
        stream = DecodedStreamObject()
        if kind == "scan":
            # Bitmap text "700W", with no PDF text objects or text layer.
            glyphs = ([31, 1, 2, 4, 8, 8, 8], [14, 17, 17, 17, 17, 17, 14],
                      [14, 17, 17, 17, 17, 17, 14], [17, 17, 17, 21, 21, 21, 10])
            pixels = bytes(0 if glyphs[x // 6][y] & (1 << (4 - x % 6)) else 255
                           for y in range(7) for x in range(24) if x % 6 < 5)
            image = DecodedStreamObject()
            image.set_data(pixels)
            image.update({NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"), NameObject("/Width"): NumberObject(20),
                NameObject("/Height"): NumberObject(7), NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/ColorSpace"): NameObject("/DeviceGray")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"):
                DictionaryObject({NameObject("/Scan"): writer._add_object(image)})})
            stream.set_data(b"q 400 0 0 140 72 600 cm /Scan Do Q")
        elif kind == "drawing":
            stream.set_data(b"0 0 100 100 re f")
        else:
            stream.set_data(b"q 1 0 0 1 0 0 cm Q")
        page[NameObject("/Contents")] = writer._add_object(stream)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def test_pdfwriter_blank_pages_keep_original_page_numbers(client):
    pdf = _pdf_pages("blank", "Power: 700", "state_blank", "Last page", "blank")
    response = _import_file(client, "blank-pages.pdf", pdf)
    assert response.status_code == 200
    sections = response.json()["sections"]
    assert [item["locator"] for item in sections] == [f"第{index}页" for index in range(1, 6)]
    assert [item["text"] for item in sections] == ["", "Power: 700", "", "Last page", ""]
    saved = _save(client, response.json()["id"]).json()
    detail = client.get(f"/api/knowledge-items/{saved['id']}").json()
    assert detail["versions"][0]["sections"] == sections


def test_unsupported_format_and_bad_base64_fail_explicitly(client):
    assert _import_file(client, "资料.doc", b"binary").status_code == 422
    bad = client.post(
        "/api/source-items/file",
        json={"filename": "a.md", "content_base64": "not base64!"},
    )
    assert bad.status_code == 422


def test_current_head_points_to_the_source_item_of_that_version(client):
    first_source = _import_file(client, "产品资料.md", MARKDOWN.encode()).json()
    first_saved = _save(client, first_source["id"]).json()
    assert first_saved["source_item_id"] == first_source["id"]

    updated = MARKDOWN.replace("净重 | 500", "净重 | 600")
    updated_source = _import_file(client, "产品资料.md", updated.encode()).json()
    updated_saved = _save(client, updated_source["id"]).json()
    # 当前版本头必须指向当次真实 SourceItem，不能继续指向上一次导入的条目。
    assert updated_saved["source_item_id"] == updated_source["id"]
    assert updated_saved["version"] == "2"

    detail = client.get(f"/api/knowledge-items/{first_saved['id']}").json()
    versions = {version["version"]: version for version in detail["versions"]}
    assert versions["1"]["source_item_id"] == first_source["id"]
    assert versions["2"]["source_item_id"] == updated_source["id"]


def test_reimporting_old_content_does_not_duplicate_or_roll_back(client):
    first_source = _import_file(client, "产品资料.md", MARKDOWN.encode()).json()
    saved = _save(client, first_source["id"]).json()
    assert saved["version"] == "1"
    assert saved["duplicate_of_version"] is None

    updated = MARKDOWN.replace("净重 | 500", "净重 | 600")
    updated_source = _import_file(client, "产品资料.md", updated.encode()).json()
    updated_saved = _save(client, updated_source["id"]).json()
    assert updated_saved["version"] == "2"
    head_hash = updated_saved["content_hash"]

    # 重新导入旧版内容：不能新建条目、不能改写旧快照、不能把当前版本回滚到 V1。
    old_source = _import_file(client, "产品资料-旧内容.md", MARKDOWN.encode()).json()
    old_saved = _save(client, old_source["id"]).json()
    assert old_saved["id"] == saved["id"]
    assert old_saved["version"] == "2"
    assert old_saved["content_hash"] == head_hash
    assert "600" in old_saved["body"]
    assert old_saved["duplicate_of_version"] == "1"

    detail = client.get(f"/api/knowledge-items/{saved['id']}").json()
    assert [version["version"] for version in detail["versions"]] == ["1", "2"]
    assert "500" in detail["versions"][0]["body"]
    assert "600" in detail["versions"][1]["body"]


def test_manual_source_keeps_its_own_version_semantics_and_is_not_merged_into_file_chain(client):
    manual = client.post(
        "/api/source-items/manual",
        json={
            "title": "手工资料",
            "content_type": "text",
            "body": "手工正文",
            "file_reference": "手工资料.md",
            "license": "user_provided",
        },
    ).json()
    manual_saved = client.post(
        f"/api/source-items/{manual['id']}/save-to-knowledge",
        json={},
    ).json()
    # 手动来源使用仓储分配的版本，不会被调用方伪造版本字符串或并入文件链。
    assert manual_saved["version"] == "1"
    assert manual_saved["ownership"] == ""
    assert manual_saved["authorization"] == ""
    assert manual_saved["content_hash"] is None
    assert manual_saved["duplicate_of_version"] is None

    # 解析出的文件即使同名同引用，也不并入这条手工链。
    imported = _import_file(client, "手工资料.md", MARKDOWN.encode()).json()
    file_saved = _save(client, imported["id"]).json()
    assert file_saved["id"] != manual_saved["id"]
    assert file_saved["version"] == "1"
    assert {item["id"] for item in client.get("/api/knowledge-items").json()["items"]} == {
        manual_saved["id"],
        file_saved["id"],
    }


def test_same_content_in_another_workspace_is_a_separate_item(client):
    first = _import_file(client, "产品资料.md", MARKDOWN.encode())
    first_saved = _save(client, first.json()["id"]).json()
    other = _import_file(client, "产品资料.md", MARKDOWN.encode(), workspace="workspace-b")
    other_saved = _save(client, other.json()["id"], workspace="workspace-b").json()
    assert other_saved["id"] != first_saved["id"]
    assert client.get(
        f"/api/knowledge-items/{first_saved['id']}", headers={"x-test-workspace": "workspace-b"}
    ).status_code == 404


def test_parse_knowledge_file_keeps_heading_structure():
    parsed = parse_knowledge_file("笔记.md", MARKDOWN.encode())
    assert parsed.full_text.startswith("# 产品资料")
    assert "## 使用说明" in parsed.full_text
    assert parsed.sections[1].title == "基本参数"
    assert parsed.sections[1].text.startswith("## 基本参数")


def test_markdown_without_body_fails():
    with pytest.raises(KnowledgeFileError):
        parse_knowledge_file("空.md", b"---\ntitle: x\n---\n")


@pytest.mark.parametrize("suffix", ["docx", "xlsx"])
def test_office_upload_save_search_and_version_evidence_use_existing_routes(client, suffix):
    from tests.test_knowledge_office_ingestion import word_file, excel_file

    data = word_file() if suffix == "docx" else excel_file()
    response = _import_file(client, f"产品资料.{suffix}", data)
    assert response.status_code == 200, response.text
    source = response.json()
    expected = "表1!R2C2" if suffix == "docx" else "'电气规格'!B2"
    assert any(parameter == {"key":"额定功率", "value":"1200", "unit":"W", "locator":expected}
               for parameter in source["parameters"])
    assert next(section for section in source["sections"] if section["locator"] == expected)["text"] == "1200"
    saved_response = _save(client, source["id"])
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    hits = client.get("/api/knowledge-items/search", params={"query":"YH-Z9"})
    assert hits.status_code == 200 and hits.json()["total_count"] == 1
    found = hits.json()["items"][0]
    assert found["id"] == saved["id"] and found["version"] == "1"
    detail = client.get(f"/api/knowledge-items/{found['id']}")
    assert detail.status_code == 200
    version = detail.json()["versions"][0]
    assert version["source_item_id"] == source["id"]
    assert version["sections"] == source["sections"]
    assert version["parameters"] == source["parameters"]
    assert version["content_hash"] == source["content_hash"]


@pytest.mark.parametrize("suffix", ["docx", "xlsx"])
def test_damaged_office_upload_does_not_write_a_source_or_knowledge_item(client, suffix):
    response = _import_file(client, f"损坏.{suffix}", b"damaged office bytes")
    assert response.status_code == 422 and "损坏" in response.json()["detail"]
    assert client.get("/api/sources/items").json()["total_count"] == 0
    assert client.get("/api/knowledge-items").json()["total_count"] == 0
