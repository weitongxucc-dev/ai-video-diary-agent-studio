from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import MaterialAssetNote, ProductionRepository

WORKSPACE = "workspace-notes"
OTHER_WORKSPACE = "workspace-other-notes"


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


def register(repo: ProductionRepository, library: Path, name: str, *, workspace_id: str = WORKSPACE) -> str:
    """Register one file the way a rescan discovers it."""
    path = library / name
    path.write_bytes(b"material-bytes")
    return repo._upsert_discovered_asset(workspace_id, path).id


def test_notes_saved_for_one_material_do_not_reach_another(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    first = register(repo, library, "红烧肉-成片.mp4")
    second = register(repo, library, "店面空镜.mp4")

    repo.update_asset_notes(
        WORKSPACE,
        [MaterialAssetNote(asset_id=first, user_description="刚出锅的红烧肉特写", upload_context="本周探店")],
    )

    kept = repo.get_asset(WORKSPACE, first)
    other = repo.get_asset(WORKSPACE, second)
    assert kept.user_description == "刚出锅的红烧肉特写"
    assert kept.upload_context == "本周探店"
    assert other.user_description == ""
    assert other.upload_context == ""
    # The note is the user's own text, never a fact about a segment.
    assert [segment.description for segment in kept.segments] == []


def test_batch_context_reaches_exactly_the_selected_materials(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    selected = [register(repo, library, f"选中-{index}.mp4") for index in range(2)]
    untouched = register(repo, library, "未选中.mp4")

    repo.update_asset_notes(
        WORKSPACE,
        [
            MaterialAssetNote(asset_id=asset_id, user_description="同一批次背景下的说明", upload_context="秋季新品发布")
            for asset_id in selected
        ],
    )

    for asset_id in selected:
        asset = repo.get_asset(WORKSPACE, asset_id)
        assert asset.upload_context == "秋季新品发布"
        assert asset.user_description == "同一批次背景下的说明"
    assert repo.get_asset(WORKSPACE, untouched).upload_context == ""


def test_notes_survive_a_reread_and_a_rescan(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "需要保留说明.mp4")
    repo.update_asset_notes(WORKSPACE, [MaterialAssetNote(asset_id=asset_id, user_description="老板出镜讲历史", upload_context="口播历史")])

    reread = repo.get_asset(WORKSPACE, asset_id)
    assert reread.user_description == "老板出镜讲历史"
    assert reread.upload_context == "口播历史"

    # A later scan rediscovers the same file and rewrites its analysis fields.
    repo._upsert_discovered_asset(WORKSPACE, library / "需要保留说明.mp4")

    rescanned = repo.get_asset(WORKSPACE, asset_id)
    assert rescanned.id == asset_id
    assert rescanned.user_description == "老板出镜讲历史"
    assert rescanned.upload_context == "口播历史"


def test_a_batch_naming_another_workspace_writes_nothing(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    mine = register(repo, library, "本工作区素材.mp4")
    theirs = register(repo, library, "他人工作区素材.mp4", workspace_id=OTHER_WORKSPACE)
    repo.update_asset_notes(WORKSPACE, [MaterialAssetNote(asset_id=mine, user_description="先存的一条")])

    with pytest.raises(KeyError):
        repo.update_asset_notes(
            WORKSPACE,
            [
                MaterialAssetNote(asset_id=mine, user_description="这一批会被拒绝"),
                MaterialAssetNote(asset_id=theirs, user_description="越权写入"),
            ],
        )

    assert repo.get_asset(WORKSPACE, mine).user_description == "先存的一条"
    assert repo.get_asset(OTHER_WORKSPACE, theirs).user_description == ""


def test_an_empty_note_clears_an_earlier_one(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "清空说明.mp4")
    repo.update_asset_notes(WORKSPACE, [MaterialAssetNote(asset_id=asset_id, user_description="先写一段", upload_context="先写背景")])

    repo.update_asset_notes(WORKSPACE, [MaterialAssetNote(asset_id=asset_id)])

    cleared = repo.get_asset(WORKSPACE, asset_id)
    assert cleared.user_description == ""
    assert cleared.upload_context == ""


@pytest.fixture
def notes_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, library = make_repo(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", WORKSPACE))
    return SimpleNamespace(client=TestClient(main_module.app), repository=repo, library=library)


def test_notes_route_saves_the_named_materials_and_returns_them(notes_client) -> None:
    first = register(notes_client.repository, notes_client.library, "接口素材甲.mp4")
    second = register(notes_client.repository, notes_client.library, "接口素材乙.mp4")

    response = notes_client.client.put(
        "/api/production/materials/notes",
        json={
            "items": [
                {"asset_id": first, "user_description": "甲的说明", "upload_context": "同一批次"},
                {"asset_id": second, "user_description": "乙的说明", "upload_context": "同一批次"},
            ]
        },
    )

    assert response.status_code == 200
    saved = {asset["id"]: asset for asset in response.json()["assets"]}
    assert saved[first]["user_description"] == "甲的说明"
    assert saved[second]["user_description"] == "乙的说明"
    assert saved[first]["upload_context"] == "同一批次"


def test_notes_route_refuses_a_batch_that_reaches_another_workspace(notes_client) -> None:
    mine = register(notes_client.repository, notes_client.library, "接口本区.mp4")
    theirs = register(notes_client.repository, notes_client.library, "接口他区.mp4", workspace_id=OTHER_WORKSPACE)

    response = notes_client.client.put(
        "/api/production/materials/notes",
        json={
            "items": [
                {"asset_id": mine, "user_description": "不该写入", "upload_context": ""},
                {"asset_id": theirs, "user_description": "越权", "upload_context": ""},
            ]
        },
    )

    assert response.status_code == 404
    assert notes_client.repository.get_asset(WORKSPACE, mine).user_description == ""
    assert notes_client.repository.get_asset(OTHER_WORKSPACE, theirs).user_description == ""