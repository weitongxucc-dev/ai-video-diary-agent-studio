from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.local_members import LocalMemberService
from app.production import MaterialVersionConflict
from test_material_tag_navigation import make_repo, register, WORKSPACE
from test_content_visibility_routes import material_library_no_execution


def prepared(tmp_path, *, uploader_user_id=None):
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "cas.mp4", user_tags=["原标签"],
                        segments=[{"user_tags": ["原片段"]}], uploader_user_id=uploader_user_id)
    return repo, repo.get_asset(WORKSPACE,asset_id)


@pytest.mark.parametrize("scope", ["asset","segment"])
def test_same_frozen_revision_only_one_atomic_material_writer_wins(tmp_path, scope):
    repo, original = prepared(tmp_path)
    barrier=Barrier(2)
    def write(label):
        barrier.wait()
        try:
            if scope == "asset":
                saved=repo.update_asset_tags(WORKSPACE,original.id,[label],expected_asset_updated_at=original.updated_at)
            else:
                saved=repo.update_segment(WORKSPACE,original.id,original.segments[0].id,
                    description=label,transcript="保持",review_status="raw",user_tags=[label],
                    expected_asset_updated_at=original.updated_at,expected_segment_updated_at=original.segments[0].updated_at)
            return label,saved
        except MaterialVersionConflict:
            return label,None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(write,["甲","乙"]))
    winners=[label for label,saved in outcomes if saved is not None]
    assert len(winners)==1
    rebuilt=type(repo)(tmp_path / "production.sqlite",tmp_path / "曜核AI IP一体机",duration_reader=lambda _:1.0,scene_reader=lambda *_:[])
    saved=rebuilt.get_asset(WORKSPACE,original.id)
    if scope=="asset":
        assert saved.user_tags==winners and saved.segments==original.segments
    else:
        assert saved.segments[0].user_tags==winners and saved.segments[0].description==winners[0]
        assert saved.user_tags==original.user_tags


def test_segment_revision_conflict_rolls_back_all_fields_and_api_is_409(tmp_path, monkeypatch):
    material_library_no_execution(monkeypatch)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    uploader, uploader_token = members.join(members.create_invite_code(), "上传者")
    reader, reader_token = members.join(members.create_invite_code(), "共享读者")
    repo, original = prepared(tmp_path, uploader_user_id=uploader.member_id)
    segment = original.segments[0]
    repo.update_segment(WORKSPACE, original.id, segment.id, description="并发修订",
                        transcript="新记录", review_status="raw", user_tags=["其他人的新标签"])
    current = repo.set_asset_visibility(WORKSPACE, original.id, "shared")
    # Migrated rows may only carry visibility in the dedicated column.
    with repo._connect() as conn:
        conn.execute(
            "UPDATE material_assets SET payload=json_remove(payload, '$.visibility') WHERE workspace_id=? AND id=?",
            (WORKSPACE, original.id),
        )
    reader_asset_id = register(repo, repo.video_library_path, "reader.mp4", uploader_user_id=reader.member_id)
    reader_asset = repo.get_asset(WORKSPACE, reader_asset_id)
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "current_workspace_id", lambda _: WORKSPACE)
    monkeypatch.setattr(main, "local_members_service", members)
    client = TestClient(main.app, headers={"X-Local-Member-Token": uploader_token})
    read_only = TestClient(main.app, headers={"X-Local-Member-Token": reader_token})
    asset_url = f"/api/production/materials/{original.id}"
    segment_url = f"{asset_url}/segments/{segment.id}"

    assert read_only.get(asset_url).status_code == 200
    assert read_only.put(segment_url, json={"description": "读者不能改片段"}).status_code == 403
    assert read_only.put(f"{asset_url}/tags", json={"user_tags": ["读者不能改标签"]}).status_code == 403
    assert read_only.post("/api/production/materials/tags/batch", json={
        "asset_ids": [reader_asset_id, original.id], "add": ["读者不能部分写入"], "remove": [],
    }).status_code == 403
    assert repo.get_asset(WORKSPACE, reader_asset_id) == reader_asset
    assert repo.get_asset(WORKSPACE, original.id) == current

    rejected = client.put(segment_url, json={
        "description": "过期不能写", "transcript": "过期不能写", "review_status": "rejected", "user_tags": ["过期"],
        "expected_asset_updated_at": current.updated_at.isoformat(),
        "expected_segment_updated_at": segment.updated_at.isoformat(),
    })
    assert rejected.status_code == 409, rejected.text
    assert repo.get_asset(WORKSPACE, original.id) == current
    rejected = client.put(f"{asset_url}/tags", json={
        "user_tags": ["过期"], "expected_asset_updated_at": original.updated_at.isoformat(),
    })
    assert rejected.status_code == 409, rejected.text
    assert repo.get_asset(WORKSPACE, original.id) == current
    saved = client.put(f"{asset_url}/tags", json={
        "user_tags": ["上传者新标签"], "expected_asset_updated_at": current.updated_at.isoformat(),
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["visibility"] == "shared"
    assert repo.get_asset(WORKSPACE, original.id).user_tags == ["上传者新标签"]
