"""知识用途分类与主题/对象关系：真实持久化、筛选与数量回读。

只覆盖本卡新增的分类/主题读写、筛选数量、解析状态和冲突入口回查；
103/134/135 已过的解析、版本与裁定行为不在这里重复验证。
隔离临时库，不访问外网、不启动真实模型。
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.source_repository import SourceRepository

WORKSPACE = "workspace-overview"
OTHER_WORKSPACE = "workspace-other-overview"

# 有小节和显式参数的文档：解析状态应为 parsed。
DOC_PARSED = """# 产品参数

## 基本参数

- 净重: 600（g）
""".encode("utf-8")
# 手动保存的普通文本条目：不经文件解析，真实地没有小节与参数。
DOC_PLAIN = "只有一段普通说明文字，没有参数也没有小节。".encode("utf-8")
# 与 DOC_PARSED 同参数不同值：用来确认真实冲突入口在概览里可见（135 已实现的能力）。
DOC_CONFLICT = """# 另一份产品参数

## 基本参数

- 净重: 800（g）
""".encode("utf-8")


@pytest.fixture
def database_path(tmp_path):
    return tmp_path / "overview.sqlite3"


@pytest.fixture
def client(tmp_path, monkeypatch, database_path):
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(database_path)
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    return TestClient(main_module.app)


def _save_document(client: TestClient, filename: str, content: bytes) -> dict:
    imported = client.post(
        "/api/source-items/file",
        json={"filename": filename, "content_base64": base64.b64encode(content).decode()},
    ).json()
    saved = client.post(
        f"/api/source-items/{imported['id']}/save-to-knowledge",
        json={"ownership": "我的内容", "authorization": "内部参考", "version": "1"},
    )
    assert saved.status_code == 200
    return saved.json()


def test_classification_and_topics_persist_and_read_back(client):
    item = _save_document(client, "manual.md", DOC_PARSED)
    # 新保存的条目默认未分类、没有主题关系，不按文件格式猜用途。
    assert item["use_category"] is None
    assert item["topics"] == []

    updated = client.patch(
        f"/api/knowledge-items/{item['id']}",
        json={"use_category": "产品知识", "topics": ["曜核一体机", "夏季新品"]},
    )
    assert updated.status_code == 200
    assert updated.json()["use_category"] == "产品知识"
    assert updated.json()["topics"] == ["曜核一体机", "夏季新品"]

    detail = client.get(f"/api/knowledge-items/{item['id']}").json()
    assert detail["use_category"] == "产品知识"
    assert detail["topics"] == ["曜核一体机", "夏季新品"]
    # 详情按这一版真实解析结果给出状态和出处。
    assert detail["parse_status"] == "parsed"
    assert [parameter["locator"] for parameter in detail["parameters"]]

    # 清空也是用户明确动作，写回未分类而不是留旧值。
    cleared = client.patch(
        f"/api/knowledge-items/{item['id']}",
        json={"use_category": None, "topics": []},
    )
    assert cleared.json()["use_category"] is None
    assert cleared.json()["topics"] == []


def test_plain_text_item_is_not_reported_as_parsed(client):
    # 走手动来源入口而不是文件解析：文件解析本来就会分小节，不能用它伪造 text_only。
    manual = client.post(
        "/api/source-items/manual",
        json={"title": "随手记", "content_type": "text", "body": "只有一段普通说明文字，没有参数也没有小节。"},
    ).json()
    saved = client.post(
        f"/api/source-items/{manual['id']}/save-to-knowledge",
        json={"ownership": "我的内容", "authorization": "内部参考", "version": "1"},
    ).json()
    detail = client.get(f"/api/knowledge-items/{saved['id']}").json()
    assert detail["parse_status"] == "text_only"
    assert detail["parameters"] == []
    # 有显式参数的条目仍按真实结构记为 parsed，两种状态不互相污染。
    parsed = _save_document(client, "manual.md", DOC_PARSED)
    assert client.get(f"/api/knowledge-items/{parsed['id']}").json()["parse_status"] == "parsed"
    # 单个段落定位也是真实结构：不能降成 text_only。
    one_section = _save_document(client, "single-paragraph.md", DOC_PLAIN)
    one_detail = client.get(f"/api/knowledge-items/{one_section['id']}").json()
    assert one_detail["sections"]
    assert one_detail["parse_status"] == "parsed"


def test_rejects_invented_category(client):
    item = _save_document(client, "manual.md", DOC_PARSED)
    response = client.patch(
        f"/api/knowledge-items/{item['id']}",
        json={"use_category": "随便编的分类", "topics": []},
    )
    assert response.status_code == 422
    assert client.get(f"/api/knowledge-items/{item['id']}").json()["use_category"] is None
    # 筛选同样不接受枚举外的值，且报错而不是回退成全部。
    assert (
        client.get("/api/knowledge-items", params={"use_category": "随便编的分类"}).status_code
        == 422
    )


def test_classification_request_requires_both_fields(client):
    # 缺字段就是缺省错误，不允许靠缺省隐式清掉另一半。
    item = _save_document(client, "manual.md", DOC_PARSED)
    client.patch(
        f"/api/knowledge-items/{item['id']}",
        json={"use_category": "产品知识", "topics": []},
    )
    assert client.patch(f"/api/knowledge-items/{item['id']}", json={"topics": ["只有主题"]}).status_code == 422
    assert client.patch(f"/api/knowledge-items/{item['id']}", json={"use_category": "公司文档"}).status_code == 422
    stored = client.get(f"/api/knowledge-items/{item['id']}").json()
    assert stored["use_category"] == "产品知识"
    assert stored["topics"] == []


def test_filter_and_counts_follow_the_same_real_field(client):
    first = _save_document(client, "one.md", DOC_PARSED)
    second = _save_document(client, "two.md", DOC_PLAIN)
    client.patch(
        f"/api/knowledge-items/{first['id']}",
        json={"use_category": "销售话术", "topics": ["曜核一体机"]},
    )

    listing = client.get("/api/knowledge-items").json()
    assert listing["total_count"] == 2
    assert listing["category_counts"]["销售话术"] == 1
    assert listing["category_counts"]["未分类"] == 1
    assert listing["category_counts"]["产品知识"] == 0
    assert listing["topics"] == ["曜核一体机"]

    filtered = client.get("/api/knowledge-items", params={"use_category": "销售话术"}).json()
    assert [entry["id"] for entry in filtered["items"]] == [first["id"]]
    # 筛选后数量仍是同一真实字段的全量统计，不随筛选缩小。
    assert filtered["category_counts"]["销售话术"] == 1
    assert filtered["total_count"] == 1

    unclassified = client.get(
        "/api/knowledge-items", params={"use_category": ""}
    ).json()
    assert [entry["id"] for entry in unclassified["items"]] == [second["id"]]

    by_topic = client.get("/api/knowledge-items", params={"topic": "曜核一体机"}).json()
    assert [entry["id"] for entry in by_topic["items"]] == [first["id"]]
    assert client.get("/api/knowledge-items", params={"topic": "别的对象"}).json()[
        "total_count"
    ] == 0


def test_classification_does_not_cross_workspace(client):
    item = _save_document(client, "manual.md", DOC_PARSED)
    assert (
        client.patch(
            f"/api/knowledge-items/{item['id']}",
            headers={"x-test-workspace": OTHER_WORKSPACE},
            json={"use_category": "公司文档", "topics": []},
        ).status_code
        == 404
    )
    assert client.get("/api/knowledge-items").json()["total_count"] == 1


def test_old_schema_database_gains_the_new_columns(tmp_path):
    """升级库缺列时由初始化补齐：旧行保持未分类/未解析，不伪造。"""
    import sqlite3

    from app.source_repository import SourceRepository as Repository

    database_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE knowledge_items (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                content_type TEXT NOT NULL, title TEXT NOT NULL, body TEXT,
                file_reference TEXT, authorization TEXT NOT NULL,
                ownership TEXT NOT NULL, source_item_id TEXT, version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'private'
            )
            """
        )
        conn.execute(
            "INSERT INTO knowledge_items VALUES "
            "('legacy', ?, 'text', '旧条目', '旧正文', NULL, 'a', 'o', NULL, '1', "
            "'2026-09-01T00:00:00+00:00', 'shared')",
            (WORKSPACE,),
        )
        conn.execute(
            """
            CREATE TABLE knowledge_item_versions (
                knowledge_item_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                version TEXT NOT NULL, content_hash TEXT, title TEXT NOT NULL,
                body TEXT, file_reference TEXT, source_item_id TEXT,
                sections TEXT NOT NULL DEFAULT '[]',
                parameters TEXT NOT NULL DEFAULT '[]',
                saved_at TEXT NOT NULL,
                PRIMARY KEY (knowledge_item_id, version)
            )
            """
        )
    repository = Repository(database_path)
    item = repository.get_knowledge_item(WORKSPACE, "legacy")
    assert item.use_category is None
    assert item.topics == ()
    # 存量行的解析状态没有真实解析记录可依，保持未知。
    assert item.parse_status is None


def test_pending_conflict_stays_visible_from_the_real_item(client):
    left = _save_document(client, "left.md", DOC_PARSED)
    right = _save_document(client, "right.md", DOC_CONFLICT)
    comparison = client.post(
        "/api/knowledge-items/parameter-comparisons",
        json={
            "object_label": "曜核一体机",
            "parameter_key": "净重",
            "unit": "g",
            "condition": "标准版",
            "left_knowledge_item_id": left["id"],
            "left_version": "1",
            "left_locator": left["parameters"][0]["locator"],
            "right_knowledge_item_id": right["id"],
            "right_version": "1",
            "right_locator": right["parameters"][0]["locator"],
        },
    )
    assert comparison.status_code == 200
    assert comparison.json()["status"] == "pending"

    listed = client.get(
        "/api/knowledge-items/parameter-comparisons",
        params={"knowledge_item_id": left["id"]},
    ).json()
    assert listed["total_count"] == 1
    assert listed["items"][0]["is_conflict"] is True