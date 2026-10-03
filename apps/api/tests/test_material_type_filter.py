"""素材库媒体类型筛选：平铺四档，类型与关键词/审核状态/标签叠加。

走真实 FastAPI 路由和真实 ProductionRepository（隔离的临时 SQLite 与素材目录），
不调任何模型。这里要钉住的是两件事：类型条件和计数、分页说的是同一批素材；
以及旧记录（payload 里没有 media_type）在筛选下仍然和加载后看到的一样是视频。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import MaterialSegment, ProductionRepository

WORKSPACE = "workspace-types"
OTHER_WORKSPACE = "workspace-other-types"


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
    review_status: str = "raw",
) -> str:
    """登记一个素材。类型来自文件名后缀，所以 .mp4/.mp3/.jpg 就是三种类型。"""
    path = library / name
    path.write_bytes(b"material-bytes")
    asset = repo._upsert_discovered_asset(workspace_id, path)
    asset.user_tags = list(user_tags or [])
    asset.review_status = review_status
    asset.segments = [
        MaterialSegment(
            f"{asset.id}-seg-0",
            asset.id,
            0.0,
            1.0,
        )
    ]
    return repo._save_asset(asset).id


def register_legacy_without_media_type(
    repo: ProductionRepository,
    library: Path,
    name: str,
    *,
    workspace_id: str = WORKSPACE,
) -> str:
    """造一条直链导入之前存的旧记录：payload 里没有 media_type 这个键。

    它是旧扫描白名单唯一收过的类型，加载回来时 MaterialAsset 默认 video，
    所以筛选也必须当它是视频，否则它会从「视频」里凭空消失。
    """
    asset_id = register(repo, library, name, workspace_id=workspace_id)
    with repo._connect() as conn:
        row = conn.execute(
            "SELECT payload FROM material_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        payload = json.loads(row["payload"])
        payload.pop("media_type", None)
        conn.execute(
            "UPDATE material_assets SET payload = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), asset_id),
        )
    return asset_id


@pytest.fixture
def type_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, library = make_repo(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    return SimpleNamespace(
        client=TestClient(main_module.app), repository=repo, library=library
    )


def page(client: TestClient, **params) -> dict:
    response = client.get("/api/production/materials", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(result: dict) -> list[str]:
    return [asset["file_name"] for asset in result["assets"]]


def test_each_type_returns_only_its_own_materials(type_client) -> None:
    client, repo, library = (
        type_client.client,
        type_client.repository,
        type_client.library,
    )
    register(repo, library, "镜头一.mp4")
    register(repo, library, "旁白.mp3")
    register(repo, library, "剧照.jpg")

    video = page(client, media_type="video")
    audio = page(client, media_type="audio")
    image = page(client, media_type="image")

    assert names(video) == ["镜头一.mp4"]
    assert video["total_count"] == 1
    assert names(audio) == ["旁白.mp3"]
    assert audio["total_count"] == 1
    assert names(image) == ["剧照.jpg"]
    assert image["total_count"] == 1


def test_omitting_the_type_keeps_every_material(type_client) -> None:
    client, repo, library = (
        type_client.client,
        type_client.repository,
        type_client.library,
    )
    register(repo, library, "镜头一.mp4")
    register(repo, library, "旁白.mp3")
    register(repo, library, "剧照.jpg")

    everything = page(client)

    assert everything["total_count"] == 3
    assert sorted(names(everything)) == sorted(["镜头一.mp4", "旁白.mp3", "剧照.jpg"])


def test_legacy_material_without_media_type_stays_video(type_client) -> None:
    client, repo, library = (
        type_client.client,
        type_client.repository,
        type_client.library,
    )
    legacy = register_legacy_without_media_type(repo, library, "老素材.mp4")
    register(repo, library, "旁白.mp3")

    video = page(client, media_type="video")

    assert [asset["id"] for asset in video["assets"]] == [legacy]
    assert video["total_count"] == 1
    # 缺字段的旧记录在返回的素材里也是视频，筛选和加载后的类型是同一件事。
    assert video["assets"][0]["media_type"] == "video"
    assert page(client)["total_count"] == 2


def test_count_and_paging_share_the_same_type_clause(type_client) -> None:
    client, repo, library = (
        type_client.client,
        type_client.repository,
        type_client.library,
    )
    for index in range(5):
        register(repo, library, f"镜头{index}.mp4")
    register(repo, library, "旁白.mp3")

    first = page(client, media_type="video", limit=2, offset=0)
    second = page(client, media_type="video", limit=2, offset=2)
    third = page(client, media_type="video", limit=2, offset=4)

    # 每一页的 total_count 都是该类型的全部条数，不是当页条数。
    assert first["total_count"] == second["total_count"] == third["total_count"] == 5
    assert len(first["assets"]) == 2
    assert first["has_more"] is True
    assert len(third["assets"]) == 1
    assert third["has_more"] is False

    paged = names(first) + names(second) + names(third)
    assert len(set(paged)) == 5
    assert "旁白.mp3" not in paged


def test_type_combines_with_query_review_status_and_tag(type_client) -> None:
    client, repo, library = (
        type_client.client,
        type_client.repository,
        type_client.library,
    )
    register(repo, library, "红烧肉镜头.mp4", user_tags=["红烧肉"], review_status="approved")
    register(repo, library, "红烧肉旁白.mp3", user_tags=["红烧肉"])
    register(repo, library, "别的镜头.mp4")

    assert names(page(client, media_type="video", q="红烧肉")) == ["红烧肉镜头.mp4"]
    assert names(page(client, media_type="audio", q="红烧肉")) == ["红烧肉旁白.mp3"]
    assert names(page(client, media_type="video", review_status="approved")) == ["红烧肉镜头.mp4"]
    assert names(page(client, media_type="video", tag="红烧肉")) == ["红烧肉镜头.mp4"]
    assert names(
        page(client, media_type="video", q="红烧肉", review_status="approved", tag="红烧肉")
    ) == ["红烧肉镜头.mp4"]


def test_type_never_reaches_another_workspace(type_client) -> None:
    client, repo, library = (
        type_client.client,
        type_client.repository,
        type_client.library,
    )
    register(repo, library, "本工作区.mp4")
    register(repo, library, "别的工作区.mp4", workspace_id=OTHER_WORKSPACE)

    result = page(client, media_type="video")

    assert names(result) == ["本工作区.mp4"]
    assert result["total_count"] == 1


def test_unknown_type_is_rejected(type_client) -> None:
    client = type_client.client

    response = client.get("/api/production/materials", params={"media_type": "text"})

    assert response.status_code == 422
