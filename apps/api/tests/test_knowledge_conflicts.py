"""知识参数比较与人工裁定：范围由用户确认，冲突并列，裁定留痕可回查。

只用隔离临时库，不访问外网、不启动真实模型。
"""

from __future__ import annotations

import base64
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.source_repository import SourceRepository

WORKSPACE = "workspace-conflict"
OTHER_WORKSPACE = "workspace-other-conflict"

# 两份隔离资料对同一个参数给出不同值，且各有页码出处。
DOC_LEFT = """# 左资料

## 规格

- 净重: 500（g）
""".encode("utf-8")
DOC_RIGHT = """# 右资料

## 规格

- 净重: 800（g）
""".encode("utf-8")
# 同一参数但单位不同：属于不同比较范围，不能并到一起，也不换算。
DOC_OTHER_UNIT = """# 第三份资料

## 规格

- 净重: 1（kg）
""".encode("utf-8")
# 同一文档里同名同单位的参数出现在两个小节：比较哪一条由用户选。
DOC_TWO_SECTIONS = """# 多规格资料

## 标准版

- 净重: 500（g）

## 增强版

- 净重: 700（g）
""".encode("utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "conflict.sqlite3")
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


def _compare(client: TestClient, **overrides) -> dict:
    payload = {
        "object_label": "曜核一体机",
        "parameter_key": "净重",
        "unit": "g",
        "condition": "标准版",
        "left_knowledge_item_id": "left",
        "left_version": "1",
        "left_locator": "left-locator",
        "right_knowledge_item_id": "right",
        "right_version": "1",
        "right_locator": "right-locator",
    }
    payload.update(overrides)
    for side in ("left", "right"):
        item_id = payload[f"{side}_knowledge_item_id"]
        if payload.get(f"{side}_locator") in (None, f"{side}-locator"):
            detail = client.get(f"/api/knowledge-items/{item_id}").json()
            locator = next(
                p["locator"] for p in detail["parameters"] if p["key"] == payload["parameter_key"]
            )
            payload[f"{side}_locator"] = locator
    return client.post("/api/knowledge-items/parameter-comparisons", json=payload)


def test_same_scope_different_values_stay_pending_with_both_sides(client):
    left = _save_document(client, "左资料.md", DOC_LEFT)
    right = _save_document(client, "右资料.md", DOC_RIGHT)

    response = _compare(
        client,
        left_knowledge_item_id=left["id"],
        right_knowledge_item_id=right["id"],
    )

    assert response.status_code == 200
    body = response.json()
    # 同范围不同值：待裁定，两侧证据和各自出处都在。
    assert body["status"] == "pending"
    assert body["is_conflict"] is True
    assert body["adjudication"] is None
    assert body["left"]["value"] == "500"
    assert body["right"]["value"] == "800"
    assert body["left"]["locator"] == body["right"]["locator"]
    # 未裁定不产生任何取值结论。
    assert "retained_value" not in body


def test_different_unit_is_not_merged_into_the_same_comparison(client):
    left = _save_document(client, "左资料.md", DOC_LEFT)
    other_unit = _save_document(client, "第三份资料.md", DOC_OTHER_UNIT)

    response = _compare(
        client,
        left_knowledge_item_id=left["id"],
        right_knowledge_item_id=other_unit["id"],
    )

    # 单位不同就是另一个比较范围：明确失败，不换算、不合并。
    assert response.status_code == 422
    assert "净重" in response.json()["detail"]


def test_missing_scope_is_refused_instead_of_guessing(client):
    left = _save_document(client, "左资料.md", DOC_LEFT)
    right = _save_document(client, "右资料.md", DOC_RIGHT)

    # 范围缺项：接口层就会拒绝，不会带着空范围去猜对象。
    missing = _compare(
        client,
        left_knowledge_item_id=left["id"],
        right_knowledge_item_id=right["id"],
        condition="   ",
    )
    blank = _compare(client, left_knowledge_item_id=left["id"],
                     right_knowledge_item_id=right["id"], condition="")
    assert missing.status_code == 422
    assert blank.status_code == 422
    # 没有落库：缺范围，比较根本没建立。
    assert client.get("/api/knowledge-items/parameter-comparisons").json()["total_count"] == 0


def test_adjudication_records_operator_reason_time_and_both_versions(client):
    left = _save_document(client, "左资料.md", DOC_LEFT)
    right = _save_document(client, "右资料.md", DOC_RIGHT)
    comparison = _compare(
        client,
        left_knowledge_item_id=left["id"],
        right_knowledge_item_id=right["id"],
    ).json()

    adjudicated = client.post(
        f"/api/knowledge-items/parameter-comparisons/{comparison['id']}/adjudications",
        json={"reason": "以新版说明书为准", "retained_side": "right"},
    )

    assert adjudicated.status_code == 200
    body = adjudicated.json()
    assert body["status"] == "adjudicated"
    record = body["adjudication"]
    # 裁定人来自当前已解析的身份，不是客户端自报的字符串。
    assert record["operator"] == "本地未认证"
    assert record["reason"] == "以新版说明书为准"
    assert record["decided_at"]
    assert record["left_version_ref"] == f"{left['id']}@1"
    assert record["right_version_ref"] == f"{right['id']}@1"
    assert record["retained_value"] == "800"
    # 未选的一侧不删除：两侧证据仍然都在。
    assert body["left"]["value"] == "500"
    assert body["right"]["value"] == "800"


def test_a_new_version_creates_a_new_pending_comparison_and_keeps_the_old_one(client):
    left = _save_document(client, "左资料.md", DOC_LEFT)
    right = _save_document(client, "右资料.md", DOC_RIGHT)
    comparison = _compare(
        client,
        left_knowledge_item_id=left["id"],
        right_knowledge_item_id=right["id"],
    ).json()
    client.post(
        f"/api/knowledge-items/parameter-comparisons/{comparison['id']}/adjudications",
        json={"reason": "以右为准", "retained_side": "right"},
    )

    # 右资料内容更新后产生新版本：与旧版本的裁定不自动沿用。
    updated_right = _save_document(
        client, "右资料.md", DOC_RIGHT.replace(b"800", b"820")
    )
    assert updated_right["version"] == "2"
    fresh = _compare(
        client,
        left_knowledge_item_id=left["id"],
        right_knowledge_item_id=updated_right["id"],
        right_version="2",
    ).json()

    assert fresh["id"] != comparison["id"]
    assert fresh["status"] == "pending"
    assert fresh["adjudication"] is None
    # 历史裁定仍在，旧证据仍可读。
    history = client.get(
        f"/api/knowledge-items/parameter-comparisons/{comparison['id']}"
    ).json()
    assert history["status"] == "adjudicated"
    assert history["adjudication"]["retained_value"] == "800"
    assert history["right"]["value"] == "800"


def test_another_workspace_cannot_read_or_adjudicate_the_comparison(client):
    left = _save_document(client, "左资料.md", DOC_LEFT)
    right = _save_document(client, "右资料.md", DOC_RIGHT)
    comparison = _compare(
        client,
        left_knowledge_item_id=left["id"],
        right_knowledge_item_id=right["id"],
    ).json()

    read = client.get(
        f"/api/knowledge-items/parameter-comparisons/{comparison['id']}",
        headers={"x-test-workspace": OTHER_WORKSPACE},
    )
    decided = client.post(
        f"/api/knowledge-items/parameter-comparisons/{comparison['id']}/adjudications",
        headers={"x-test-workspace": OTHER_WORKSPACE},
        json={"reason": "越权", "retained_side": "left"},
    )
    listed = client.get(
        "/api/knowledge-items/parameter-comparisons",
        headers={"x-test-workspace": OTHER_WORKSPACE},
    )

    assert read.status_code == 404
    assert decided.status_code == 404
    assert listed.json()["total_count"] == 0
def test_the_chosen_locator_selects_the_evidence_not_the_first_match(client):
    # 同一文档同名同单位参数出现在两个小节：由用户选中的出处决定用哪条证据。
    multi = _save_document(client, "多规格资料.md", DOC_TWO_SECTIONS)
    other = _save_document(client, "右资料.md", DOC_RIGHT)
    enhanced = client.get(f"/api/knowledge-items/{multi['id']}").json()
    standard_locator = next(p["locator"] for p in enhanced["parameters"] if p["value"] == "500")
    enhanced_locator = next(p["locator"] for p in enhanced["parameters"] if p["value"] == "700")
    assert standard_locator != enhanced_locator

    picked = _compare(
        client,
        left_knowledge_item_id=multi["id"],
        left_locator=enhanced_locator,
        right_knowledge_item_id=other["id"],
        right_locator=None,
    )
    body = picked.json()
    assert body["left"]["value"] == "700"
    assert body["left"]["locator"] == enhanced_locator

    # 指定一个该版本里并不存在的出处：明确失败，不退回取第一条。
    wrong = client.post(
        "/api/knowledge-items/parameter-comparisons",
        json={
            "object_label": "曜核一体机", "parameter_key": "净重", "unit": "g",
            "condition": "标准版",
            "left_knowledge_item_id": multi["id"], "left_version": "1",
            "left_locator": "第 99 段", "right_knowledge_item_id": other["id"],
            "right_version": "1", "right_locator": enhanced_locator,
        },
    )
    assert wrong.status_code == 422
    assert "第 99 段" in wrong.json()["detail"]
