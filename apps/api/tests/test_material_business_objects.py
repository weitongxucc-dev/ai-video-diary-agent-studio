"""素材业务名称条目：workspace 自有、按素材显式保存。

这些条目是用户自己维护的叫法，不是系统识别出的画面事实；与出镜形象/声音档案
无关，关联也不会创建形象或声音档案。这里只验证隔离的临时库，不打开真实用户库。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import ProductionRepository

WORKSPACE = "workspace-objects"
OTHER_WORKSPACE = "workspace-other-objects"


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
    path = library / name
    path.write_bytes(b"material-bytes")
    return repo._upsert_discovered_asset(workspace_id, path).id


def test_creating_an_entry_attaches_it_to_nothing(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "红烧肉-成片.mp4")

    entry = repo.create_business_object(WORKSPACE, "dish", "红烧肉")

    assert (entry.kind, entry.name) == ("dish", "红烧肉")
    # 名称条目本身不写素材：还得由用户明确选。
    assert repo.list_asset_business_objects(WORKSPACE, asset_id) == []


def test_an_entry_reads_back_by_kind_and_name_after_reopen(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "曜核一体机-成片.mp4")
    product = repo.create_business_object(WORKSPACE, "product", "曜核一体机")
    person = repo.create_business_object(WORKSPACE, "person", "老张")
    repo.set_asset_business_objects(WORKSPACE, asset_id, [product.id, person.id])

    # 重新打开同一份库，重开后仍能回读类型和名称。
    reopened = ProductionRepository(
        repo.database_path,
        library,
        duration_reader=lambda path: 1.0,
        scene_reader=lambda path, duration: [],
    )
    reread = reopened.list_asset_business_objects(WORKSPACE, asset_id)
    assert [(entry.id, entry.kind, entry.name) for entry in reread] == [
        (product.id, "product", "曜核一体机"),
        (person.id, "person", "老张"),
    ]
    assert reopened.list_asset_business_objects(WORKSPACE, asset_id) == reread


def test_saving_one_material_leaves_the_others_alone(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    first = register(repo, library, "店面空镜.mp4")
    second = register(repo, library, "老板出镜.mp4")
    product = repo.create_business_object(WORKSPACE, "product", "曜核一体机")

    repo.set_asset_business_objects(WORKSPACE, first, [product.id])

    assert [entry.id for entry in repo.list_asset_business_objects(WORKSPACE, first)] == [product.id]
    # 同批次的另一条素材没有跟着获得这条关联。
    assert repo.list_asset_business_objects(WORKSPACE, second) == []


def test_saving_again_replaces_rather_than_merges(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "两次保存.mp4")
    product = repo.create_business_object(WORKSPACE, "product", "曜核一体机")
    dish = repo.create_business_object(WORKSPACE, "dish", "红烧肉")
    repo.set_asset_business_objects(WORKSPACE, asset_id, [product.id, dish.id])

    replaced = repo.set_asset_business_objects(WORKSPACE, asset_id, [dish.id])
    assert [entry.id for entry in replaced] == [dish.id]
    assert [entry.id for entry in repo.list_asset_business_objects(WORKSPACE, asset_id)] == [dish.id]

    cleared = repo.set_asset_business_objects(WORKSPACE, asset_id, [])
    assert cleared == []
    assert repo.list_asset_business_objects(WORKSPACE, asset_id) == []


def test_entries_never_cross_workspaces(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    mine = register(repo, library, "本工作区素材.mp4")
    theirs = register(repo, library, "他人工作区素材.mp4", workspace_id=OTHER_WORKSPACE)
    my_entry = repo.create_business_object(WORKSPACE, "product", "我的产品")
    their_entry = repo.create_business_object(OTHER_WORKSPACE, "product", "他们的产品")
    repo.set_asset_business_objects(WORKSPACE, mine, [my_entry.id])

    # 别人的条目既不列出，也读不到别人的素材。
    assert [entry.id for entry in repo.list_business_objects(WORKSPACE)] == [my_entry.id]
    with pytest.raises(KeyError):
        repo.list_asset_business_objects(WORKSPACE, theirs)
    with pytest.raises(KeyError):
        repo.set_asset_business_objects(WORKSPACE, theirs, [their_entry.id])

    # 引用别人的条目会整批被拒，不会写半套关联。
    with pytest.raises(KeyError):
        repo.set_asset_business_objects(WORKSPACE, mine, [my_entry.id, their_entry.id])
    assert [entry.id for entry in repo.list_asset_business_objects(WORKSPACE, mine)] == [my_entry.id]
    assert repo.list_asset_business_objects(OTHER_WORKSPACE, theirs) == []


def test_a_blank_or_unknown_kind_is_refused(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)

    with pytest.raises(ValueError):
        repo.create_business_object(WORKSPACE, "brand", "不认识的类型")
    with pytest.raises(ValueError):
        repo.create_business_object(WORKSPACE, "product", "   ")


@pytest.fixture
def objects_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, library = make_repo(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", WORKSPACE))
    return SimpleNamespace(client=TestClient(main_module.app), repository=repo, library=library)


def test_the_four_frozen_routes_round_trip_one_material(objects_client) -> None:
    asset_id = register(objects_client.repository, objects_client.library, "接口素材.mp4")
    client = objects_client.client

    empty = client.get("/api/production/business-objects")
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "total_count": 0}

    created = client.post("/api/production/business-objects", json={"kind": "dish", "name": "红烧肉"})
    assert created.status_code == 201
    entry = created.json()
    assert set(entry) == {"id", "kind", "name"}
    assert (entry["kind"], entry["name"]) == ("dish", "红烧肉")

    listed = client.get("/api/production/business-objects").json()
    assert listed["total_count"] == 1
    assert listed["items"] == [entry]

    # 新增之后还没写素材。
    assert client.get(f"/api/production/materials/{asset_id}/business-objects").json() == {"items": [], "total_count": 0}

    saved = client.put(
        f"/api/production/materials/{asset_id}/business-objects",
        json={"business_object_ids": [entry["id"]]},
    )
    assert saved.status_code == 200
    assert saved.json() == {"items": [entry], "total_count": 1}
    # 回读同一份关联。
    assert client.get(f"/api/production/materials/{asset_id}/business-objects").json() == {"items": [entry], "total_count": 1}


def test_the_save_route_refuses_another_workspaces_entries(objects_client) -> None:
    asset_id = register(objects_client.repository, objects_client.library, "接口本区.mp4")
    client = objects_client.client
    mine = client.post("/api/production/business-objects", json={"kind": "product", "name": "我的产品"}).json()
    theirs = client.post(
        "/api/production/business-objects",
        json={"kind": "product", "name": "他们的产品"},
        headers={"x-test-workspace": OTHER_WORKSPACE},
    ).json()
    # 别人的条目只出现在别人的列表里。
    assert theirs["name"] == "他们的产品"
    assert theirs not in client.get("/api/production/business-objects").json()["items"]

    response = client.put(
        f"/api/production/materials/{asset_id}/business-objects",
        json={"business_object_ids": [mine["id"], theirs["id"]]},
    )

    assert response.status_code == 404
    assert objects_client.repository.list_asset_business_objects(WORKSPACE, asset_id) == []


def test_the_routes_reject_a_blank_name_and_an_unknown_kind(objects_client) -> None:
    client = objects_client.client

    assert client.post("/api/production/business-objects", json={"kind": "dish", "name": "  "}).status_code == 422
    assert client.post("/api/production/business-objects", json={"kind": "brand", "name": "红烧肉"}).status_code == 422


def test_reading_a_material_from_another_workspace_is_not_found(objects_client) -> None:
    theirs = register(objects_client.repository, objects_client.library, "接口他区.mp4", workspace_id=OTHER_WORKSPACE)

    response = objects_client.client.get(f"/api/production/materials/{theirs}/business-objects")

    assert response.status_code == 404
