"""首页素材标签导航：点标签看到的行，必须和词云上的数字是同一集合。

走真实 FastAPI 路由和真实 ProductionRepository（隔离的临时 SQLite 与素材目录），
不调任何模型。断言只有一件事：词云统计、点击筛选、分页总数三者说的是同一批素材。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.production import MaterialSegment, ProductionRepository

WORKSPACE = "workspace-tags"
OTHER_WORKSPACE = "workspace-other-tags"

RED_BRAISED = "红烧肉"
RED_BRAISED_CLOSEUP = "红烧肉特写"
HERO_SHOT = "HeroShot"


def make_repo(tmp_path: Path) -> tuple[ProductionRepository, Path]:
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=lambda path: 1.0,
        scene_reader=lambda path, duration: [],
    )
    return repo, library


def register(
    repo: ProductionRepository,
    library: Path,
    name: str,
    *,
    workspace_id: str = WORKSPACE,
    user_tags: list[str] | None = None,
    segments: list[dict[str, list[str]]] | None = None,
    uploader_user_id: str | None = None,
) -> str:
    """登记一个素材，并直接带上它自己的标签和每个片段的标签。

    片段标签手写而不是跑分析：这里要验的是"同一个标签名，统计和筛选算不算同一件事"，
    与标签从哪里来无关，跑一遍分析反而把标签来源搅进来。
    """
    path = library / name
    path.write_bytes(b"material-bytes")
    # 上传者只认显式传入的真实身份；目录绑定身份不等于上传者。
    asset = repo._upsert_discovered_asset(
        workspace_id, path, owner_user_id=uploader_user_id
    )
    asset.user_tags = list(user_tags or [])
    asset.segments = [
        MaterialSegment(
            f"{asset.id}-seg-{index}",
            asset.id,
            float(index),
            float(index) + 1.0,
            user_tags=list(spec.get("user_tags", [])),
            system_tags=list(spec.get("system_tags", [])),
        )
        for index, spec in enumerate(segments or [])
    ]
    return repo._save_asset(asset).id


@pytest.fixture
def tag_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, library = make_repo(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    # 真实成员身份：私有素材只对上传者本人可读，匿名请求看不到任何一行。
    # 这里绑定目录时给出上传者身份，读取时带上同一个真实令牌。
    service = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    uploader, uploader_token = service.join(service.create_invite_code(), "上传者")
    return SimpleNamespace(
        client=TestClient(main_module.app),
        repository=repo,
        library=library,
        uploader_user_id=uploader.member_id,
        headers={"X-Local-Member-Token": uploader_token},
    )


def tag_counts(client: TestClient, headers: dict[str, str] | None = None) -> dict[str, dict[str, int]]:
    response = client.get("/api/production/materials/insights", headers=headers or {})
    assert response.status_code == 200
    return {item["tag"]: item for item in response.json()["tags"]}


def create_material_conversation(client: TestClient, headers: dict[str, str], asset_id: str, draft_text: str | None = None) -> dict:
    # Plain TestClient use does not enter the app lifespan; initialize only the
    # already isolated test conversation database before seeding this session.
    main_module.conversation_repo.initialize()
    response = client.post(
        "/api/assistant/conversations",
        headers=headers,
        json={"scope_kind": "material", "scope_id": f"asset:{asset_id}", "title": "测试素材对话"},
    )
    assert response.status_code == 200, response.text
    if draft_text is not None:
        saved = client.patch(f"/api/assistant/conversations/{response.json()['id']}", headers=headers,
                             json={"draft_text": draft_text, "expected_draft_version": response.json()["draft_version"]})
        assert saved.status_code == 200, saved.text
    conversation = client.get(f"/api/assistant/conversations/{response.json()['id']}", headers=headers)
    assert conversation.status_code == 200, conversation.text
    return conversation.json()


def test_selected_material_assistant_binds_visible_owner_and_returns_persisted_tags(tag_client, monkeypatch) -> None:
    asset_id = register(
        tag_client.repository, tag_client.library, "对话素材.mp4",
        uploader_user_id=tag_client.uploader_user_id, user_tags=["旧标签"],
    )
    conversation = create_material_conversation(tag_client.client, tag_client.headers, asset_id, "把整条素材标签改成新标签")

    def fake_turn(_settings, *, workspace_id, material_selection: dict, **_kwargs):
        selection = material_selection
        assert workspace_id == WORKSPACE
        assert {key: selection[key] for key in ("scope", "asset_id")} == {"scope": "asset", "asset_id": asset_id}
        assert selection["expected_updated_at"]
        before = tag_client.repository.get_asset(workspace_id, asset_id)
        updated = tag_client.repository.update_asset_tags(workspace_id, asset_id, ["新标签"])
        after = tag_client.repository.get_asset(workspace_id, asset_id)
        assert before.user_tags == ["旧标签"] and updated.user_tags == after.user_tags == ["新标签"]
        return "整条素材标签已保存 token=leaked-value", {"kind": "material_result", "material_result": {
            "scope": "asset", "asset_ref": f"asset:{asset_id}", "user_tags": after.user_tags,
            "action": "replace", "changed": True, "previous_user_tags": before.user_tags,
        }}

    monkeypatch.setattr(main_module, "run_plan_turn", fake_turn)
    response = tag_client.client.post(
        "/api/assistant/material/turn", headers=tag_client.headers,
        json={"message": "把整条素材标签改成新标签", "conversation_id": conversation["id"],
              "client_request_id": str(uuid4()), "expected_draft_version": conversation["draft_version"],
              "selection": {"scope": "asset", "asset_id": asset_id}},
    )
    assert response.status_code == 200
    assert response.json()["reply"] == "整条素材标签已保存 token=[已隐藏凭据]"
    assert response.json()["material_result"]["segment_ref"] is None
    assert response.json()["material_result"]["user_tags"] == ["新标签"]
    assert tag_client.client.get(f"/api/production/materials/{asset_id}", headers=tag_client.headers).json()["user_tags"] == ["新标签"]


def test_selected_material_assistant_does_not_call_pi_for_an_invisible_asset(tag_client, monkeypatch) -> None:
    private_asset = register(tag_client.repository, tag_client.library, "他人素材.mp4", uploader_user_id="another-member")
    visible_asset = register(tag_client.repository, tag_client.library, "本人素材.mp4", uploader_user_id=tag_client.uploader_user_id)
    conversation = create_material_conversation(tag_client.client, tag_client.headers, visible_asset, "读一下标签")
    called = False

    def forbidden_turn(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Pi must not run for an invisible target")

    monkeypatch.setattr(main_module, "run_plan_turn", forbidden_turn)
    response = tag_client.client.post(
        "/api/assistant/material/turn", headers=tag_client.headers,
        json={"message": "读一下标签", "conversation_id": conversation["id"],
              "client_request_id": str(uuid4()), "expected_draft_version": conversation["draft_version"],
              "selection": {"scope": "asset", "asset_id": private_asset}},
    )
    assert response.status_code == 404
    assert called is False


def test_selected_material_assistant_rejects_credentials_in_user_message(tag_client, monkeypatch) -> None:
    asset_id = register(
        tag_client.repository, tag_client.library, "凭据检查素材.mp4",
        uploader_user_id=tag_client.uploader_user_id,
    )
    conversation = create_material_conversation(tag_client.client, tag_client.headers, asset_id)
    monkeypatch.setattr(
        main_module, "_workspace_settings_for",
        lambda _workspace_id: SimpleNamespace(effective_llm_api_key="synthetic-secret-123"),
    )
    called = False

    def forbidden_turn(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Pi must not receive credentials pasted into the message")

    monkeypatch.setattr(main_module, "run_plan_turn", forbidden_turn)
    response = tag_client.client.post(
        "/api/assistant/material/turn", headers=tag_client.headers,
        json={"message": "添加标签 token=synthetic-secret-123", "conversation_id": conversation["id"],
              "client_request_id": str(uuid4()), "expected_draft_version": conversation["draft_version"],
              "selection": {"scope": "asset", "asset_id": asset_id}},
    )
    assert response.status_code == 422
    assert "密钥或口令" in response.json()["detail"]
    assert called is False


def test_a_material_is_counted_once_even_when_the_tag_repeats(tag_client) -> None:
    """同一个素材里标签名重复，只算一个素材；片段数保留自己的含义。"""
    register(
        tag_client.repository,
        tag_client.library,
        "重复标签素材.mp4",
        uploader_user_id=tag_client.uploader_user_id,
        user_tags=[RED_BRAISED],
        segments=[
            {"user_tags": [RED_BRAISED]},
            {"system_tags": [RED_BRAISED]},
        ],
    )
    register(
        tag_client.repository,
        tag_client.library,
        "另一个素材.mp4",
        uploader_user_id=tag_client.uploader_user_id,
        segments=[{"system_tags": [RED_BRAISED]}],
    )

    counts = tag_counts(tag_client.client, tag_client.headers)
    assert counts[RED_BRAISED]["asset_count"] == 2
    # 片段数说的是片段，不是素材：第一个素材两个片段，第二个一个。
    assert counts[RED_BRAISED]["segment_count"] == 3


def test_a_similar_tag_and_a_differently_spelled_one_are_not_the_same(tag_client) -> None:
    """相似标签、以及只是大小写不同的英文标签，都不能混进同一个数。"""
    register(
        tag_client.repository,
        tag_client.library,        "特写素材.mp4",
        uploader_user_id=tag_client.uploader_user_id,
        user_tags=[RED_BRAISED_CLOSEUP],
    )
    register(tag_client.repository, tag_client.library, "大写素材.mp4", uploader_user_id=tag_client.uploader_user_id, user_tags=[HERO_SHOT])
    register(tag_client.repository, tag_client.library, "小写素材.mp4", uploader_user_id=tag_client.uploader_user_id, user_tags=[HERO_SHOT.lower()])

    counts = tag_counts(tag_client.client, tag_client.headers)
    assert counts[RED_BRAISED_CLOSEUP]["asset_count"] == 1
    assert RED_BRAISED not in counts or counts[RED_BRAISED]["asset_count"] == 0
    # 大小写是两个标签名，各自只算自己那一个素材。
    assert counts[HERO_SHOT]["asset_count"] == 1
    assert counts[HERO_SHOT.lower()]["asset_count"] == 1


def test_the_number_shown_beside_a_tag_is_the_number_of_rows_behind_it(tag_client) -> None:
    """点标签拿到的行数，必须等于词云上写着的素材数。"""
    for index in range(3):
        register(tag_client.repository, tag_client.library, f"成片-{index}.mp4", uploader_user_id=tag_client.uploader_user_id, user_tags=[RED_BRAISED])
    register(tag_client.repository, tag_client.library, "特写成片.mp4", uploader_user_id=tag_client.uploader_user_id, user_tags=[RED_BRAISED_CLOSEUP])
    register(tag_client.repository, tag_client.library, "英文成片.mp4", uploader_user_id=tag_client.uploader_user_id, user_tags=[HERO_SHOT])

    for name in (RED_BRAISED, RED_BRAISED_CLOSEUP, HERO_SHOT):
        shown = tag_counts(tag_client.client, tag_client.headers)[name]["asset_count"]
        rows = tag_client.client.get("/api/production/materials", params={"tag": name}, headers=tag_client.headers).json()
        assert rows["total_count"] == shown
        assert len(rows["assets"]) == shown


def test_the_tag_filter_narrows_the_count_before_paging(tag_client) -> None:
    """筛选发生在计数和分页之前，跨页也要一个不漏、一样不多。"""
    for index in range(5):
        register(tag_client.repository, tag_client.library, f"分页-命中-{index}.mp4", uploader_user_id=tag_client.uploader_user_id, user_tags=[RED_BRAISED])
    for index in range(3):
        register(
            tag_client.repository,
            tag_client.library,            f"分页-未命中-{index}.mp4",
            uploader_user_id=tag_client.uploader_user_id,
            user_tags=[RED_BRAISED_CLOSEUP],
        )

    pages = [
        tag_client.client.get(
            "/api/production/materials",
            params={"tag": RED_BRAISED, "limit": 2, "offset": offset},
            headers=tag_client.headers,
        ).json()
        for offset in (0, 2, 4)
    ]

    # 每页的总数都是筛选后的总数，不是当前页，也不是全库。
    assert {page["total_count"] for page in pages} == {5}
    rows = [asset for page in pages for asset in page["assets"]]
    assert len({asset["id"] for asset in rows}) == 5
    assert {asset["file_name"] for asset in rows} == {f"分页-命中-{index}.mp4" for index in range(5)}


def test_another_workspace_never_appears_in_either_the_count_or_the_rows(tag_client) -> None:
    """别人工作区的素材，既不进我的词云，也不出现在我点标签的结果里。"""
    register(tag_client.repository, tag_client.library, "我的素材.mp4", uploader_user_id=tag_client.uploader_user_id, user_tags=[RED_BRAISED])
    register(
        tag_client.repository,
        tag_client.library,
        "他人素材.mp4",
        workspace_id=OTHER_WORKSPACE,
        uploader_user_id=tag_client.uploader_user_id,
        user_tags=[RED_BRAISED],
    )

    assert tag_counts(tag_client.client, tag_client.headers)[RED_BRAISED]["asset_count"] == 1

    mine = tag_client.client.get(
        "/api/production/materials", params={"tag": RED_BRAISED},
        headers={**tag_client.headers, "x-test-workspace": WORKSPACE},
    ).json()
    theirs = tag_client.client.get(
        "/api/production/materials", params={"tag": RED_BRAISED},
        headers={**tag_client.headers, "x-test-workspace": OTHER_WORKSPACE},
    ).json()
    assert [asset["file_name"] for asset in mine["assets"]] == ["我的素材.mp4"]
    assert [asset["file_name"] for asset in theirs["assets"]] == ["他人素材.mp4"]
