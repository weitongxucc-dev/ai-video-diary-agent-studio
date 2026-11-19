from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.materials.contracts import MaterialAsset, MaterialSegment
from tests.test_material_search_candidates import _repository
from test_content_visibility_routes import material_library_no_execution


def test_material_search_filters_members_before_limit_and_keeps_shared_assets(
    tmp_path, monkeypatch,
):
    material_library_no_execution(monkeypatch)
    workspace = "material-search-members"
    production = _repository(tmp_path)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    first, first_token = members.join(members.create_invite_code(), "第一成员")
    second, second_token = members.join(members.create_invite_code(), "第二成员")
    members.set_role(first.member_id, "owner")

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(
        main_module, "current_workspace_id", lambda _request: workspace
    )
    client = TestClient(main_module.app)

    def add_asset(file_name: str, owner_id: str, visibility: str):
        # Persist ready analysis facts directly; no ingestion/scan/provider fixture.
        path = production.video_library_path / file_name
        path.write_bytes(b"metadata-only")
        asset_id = uuid4().hex
        asset = MaterialAsset(
            id=asset_id, workspace_id=workspace, file_name=file_name,
            path=str(path), size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            duration_seconds=25.0, analysis_status="ready",
            review_status="approved", usage_role="b_roll",
            owner_user_id=owner_id, visibility=visibility,
            segments=[MaterialSegment(
                id=f"{asset_id}-{index}", asset_id=asset_id,
                start_seconds=start, end_seconds=end,
                description="permissionneedle" if index == 0 else "",
                visual_status="ready", usage_role="b_roll", review_status="approved",
            ) for index, (start, end) in enumerate(((0.0, 5.0), (5.0, 17.0), (17.0, 25.0)))],
        )
        return production._save_asset(asset)

    shared = add_asset("shared.mp4", first.member_id, "shared")
    first_private = add_asset("first-private.mp4", first.member_id, "private")
    second_private = add_asset("second-private.mp4", second.member_id, "private")

    first_headers = {"X-Local-Member-Token": first_token}
    second_headers = {"X-Local-Member-Token": second_token}
    first_all = client.get(
        "/api/production/materials/search",
        params={"q": "permissionneedle", "limit": 20},
        headers=first_headers,
    )
    second_all = client.get(
        "/api/production/materials/search",
        params={"q": "permissionneedle", "limit": 20},
        headers=second_headers,
    )
    assert first_all.status_code == second_all.status_code == 200
    first_ids = {row["asset_ref"].removeprefix("asset:") for row in first_all.json()}
    second_ids = {row["asset_ref"].removeprefix("asset:") for row in second_all.json()}
    assert first_ids == {shared.id, first_private.id}
    assert second_ids == {shared.id, second_private.id}
    text_matches = [row for row in first_all.json() if row["match_tier"] == "text"]
    assert len(text_matches) == 2
    assert all("permissionneedle" in row["evidence_excerpt"] and row["needs_confirmation"] is False
               for row in text_matches)

    weak = client.get("/api/production/materials/search",
        params={"q": "unmatched", "usage_role": "b_roll"}, headers=first_headers)
    assert weak.status_code == 200
    assert {row["asset_ref"] for row in weak.json()} == {row["asset_ref"] for row in first_all.json()}
    assert all(row["match_tier"] == "structural" and row["needs_confirmation"] is True for row in weak.json())
    matched_refs = {row["segment_ref"] for row in text_matches}
    assert all("permissionneedle" in row["evidence_excerpt"] for row in weak.json()
               if row["segment_ref"] in matched_refs)

    # The second member's private asset is newest and matches equally. A limit
    # applied before permission filtering would hide the first member's result.
    first_limited = client.get(
        "/api/production/materials/search",
        params={"q": "permissionneedle", "limit": 1},
        headers=first_headers,
    )
    assert first_limited.status_code == 200
    assert first_limited.json()
    limited_asset_id = first_limited.json()[0]["asset_ref"].removeprefix("asset:")
    assert limited_asset_id != second_private.id
    assert limited_asset_id in {shared.id, first_private.id}


def test_library_search_returns_only_real_segment_matches_and_current_use_permissions(tmp_path, monkeypatch):
    from dataclasses import replace

    material_library_no_execution(monkeypatch)
    workspace = "material-library-hits"
    production = _repository(tmp_path)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    owner, owner_token = members.join(members.create_invite_code(), "素材上传者")
    other, other_token = members.join(members.create_invite_code(), "其他成员")
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _: workspace)
    monkeypatch.setattr(main_module, "settings", replace(main_module.settings, video_inbox_path=str(production.video_library_path)))

    def asset(id, uploader, visibility, *, asset_name, description):
        source = production.video_library_path / asset_name
        source.write_bytes(b"source fixture")
        return production.material_assets.save_asset(MaterialAsset(
            id=id, workspace_id=workspace, file_name=asset_name, path=str(source),
            size_bytes=source.stat().st_size, modified_at=datetime.fromtimestamp(source.stat().st_mtime),
            owner_user_id=uploader, visibility=visibility, analysis_status="ready",
            usage_role="b_roll", duration_seconds=145,
            segments=[
                MaterialSegment(f"{id}-first", id, 35.0, 40.0, description="传送带"),
                MaterialSegment(f"{id}-second", id, 103.59, 112.79, description=description),
            ],
        ))

    shared = asset("shared-factory", owner.member_id, "shared", asset_name="工序实拍.mp4", description="包装区摆放待用纸箱")
    asset("name-match", owner.member_id, "private", asset_name="包装区补拍.mp4", description="机器仪表")
    asset("hidden", other.member_id, "private", asset_name="另个成员.mp4", description="包装区")
    owner_client = TestClient(main_module.app, headers={"X-Local-Member-Token": owner_token})
    reader_client = TestClient(main_module.app, headers={"X-Local-Member-Token": other_token})
    route = "/api/production/materials"

    def search():
        response = owner_client.get(route, params={"q": "包装区"})
        assert response.status_code == 200, response.text
        assert response.json()["total_count"] == 2
        return {item["id"]: item for item in response.json()["assets"]}

    result = search()
    assert result["name-match"]["search_hits"] == []
    visible = result[shared.id]
    assert visible["source_available"] is True
    assert [(item["segment_id"], item["basis"], item["excerpt"]) for item in visible["search_hits"]] == [
        (f"{shared.id}-second", "片段描述", "包装区摆放待用纸箱"),
    ]
    assert visible["segments"][1]["start_seconds"] == 103.59

    restriction_url = f"{route}/{shared.id}/use-state"
    denied = reader_client.put(restriction_url, json={"use_state": "excluded"})
    assert denied.status_code == 403
    assert production.get_asset(workspace, shared.id).use_state == "available"
    for status, reference_allowed in [("reference_only", True), ("excluded", False)]:
        saved = owner_client.put(restriction_url, json={"use_state": status})
        assert saved.status_code == 200, saved.text
        hit, = search()[shared.id]["search_hits"]
        assert hit["reference_allowed"] is reference_allowed
        assert hit["manual_use_allowed"] is False and hit["restriction"]

    (production.video_library_path / shared.file_name).unlink()
    assert search()[shared.id]["source_available"] is False


def test_role_confirmation_rechecks_bilingual_plans_without_touching_other_material_edits(tmp_path, monkeypatch):
    """An initially unassigned raw video can be checked from the proposed group."""
    from dataclasses import replace

    from app.source_repository import SourceRepository

    workspace = "work-material-review"
    production = _repository(tmp_path)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    owner, owner_token = members.join(members.create_invite_code(), "素材上传者")
    _, reader_token = members.join(members.create_invite_code(), "共享素材阅读者")
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3"))
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace)
    client = TestClient(main_module.app)
    owner_headers = {"X-Local-Member-Token": owner_token}
    reader_headers = {"X-Local-Member-Token": reader_token}

    path = production.video_library_path / "conveyor.mp4"
    path.write_bytes(b"fixture source; metadata only")
    production.material_source_files.bind_material_ingestion(workspace, expected_directory=str(path.parent))
    production.material_source_files.confirm_material_copy_complete(
        workspace, relative_path=path.name,
        expected_revision=production.material_source_files.material_copy_revision(path.stat()),
        confirmation_source="manual", verified_owner_user_id=owner.member_id,
    )
    registered, = production.material_intake.scan_library(workspace)
    material_library_no_execution(monkeypatch)
    segment = MaterialSegment(
        id="conveyor-shot", asset_id=registered.id, start_seconds=0.0, end_seconds=5.0,
        description="  巧克力传送带 conveyor  ", transcript="  原口述  ",
        system_tags=["主体:传送带"], user_tags=["原标签"],
    )
    neighbor = MaterialSegment(
        id="packing-shot", asset_id=registered.id, start_seconds=5.0, end_seconds=8.0,
        description="包装工位", user_tags=["包装"],
    )
    production.material_assets.save_asset(replace(
        registered, analysis_status="ready", duration_seconds=8.0, segments=[segment, neighbor],
    ))
    asset = production.set_asset_visibility(workspace, registered.id, "shared")

    created = client.post("/api/creation-requests", headers=owner_headers, json={
        "goal": "巧克力工厂传送带", "request_key": str(uuid4()), "target_count": 2,
        "output_relation": "variants", "duration_target_seconds": 30, "presenter_share_target": 20,
    })
    assert created.status_code == 201, created.text
    request = created.json()
    base = f"/api/creation-requests/{request['id']}/windows"
    prepared = client.post(base, headers=owner_headers, json={
        "expected_revision": request["revision"],
        "goals": [
            {"goal": "巧克力传送带", "requirements": {"language": "zh-CN"}},
            {"goal": "Chocolate conveyor", "requirements": {"language": "en-US"}},
        ],
    })
    assert prepared.status_code == 200, prepared.text
    draft = prepared.json()
    assert [entry["plan"]["approved_clip_count"] for entry in draft["current_window"]["entries"]] == [0, 0]

    segment_path = f"/api/production/materials/{asset.id}/segments/{segment.id}"
    change = {
        "usage_role": "b_roll",
        "expected_asset_updated_at": asset.updated_at.isoformat(),
        "expected_segment_updated_at": segment.updated_at.isoformat(),
    }
    assert client.patch(segment_path + "/usage-role", headers=reader_headers, json=change).status_code == 403
    # The older material-library write cannot override another member's role either.
    assert client.put(segment_path, headers=reader_headers, json={"usage_role": "b_roll"}).status_code == 403
    assert client.patch(segment_path + "/usage-role", headers=owner_headers,
                        json={**change, "description": "unrelated edit"}).status_code == 422
    assert production.get_asset(workspace, asset.id) == asset

    saved = client.patch(segment_path + "/usage-role", headers=owner_headers, json=change)
    assert saved.status_code == 200, saved.text
    updated = production.get_asset(workspace, asset.id)
    assert updated.segments == [
        replace(segment, usage_role="b_roll", updated_at=updated.segments[0].updated_at,
                system_tags=["主体:传送带", "用途:b_roll"]),
        neighbor,
    ]
    assert updated.review_status == asset.review_status and updated.usage_role == asset.usage_role
    assert client.patch(segment_path + "/usage-role", headers=owner_headers, json=change).status_code == 409
    assert client.put(segment_path, headers=owner_headers,
                      json={**change, "description": "stale draft"}).status_code == 409
    assert production.get_asset(workspace, asset.id) == updated

    checked = client.post(f"{base}/{draft['current_window']['id']}/replan", headers=owner_headers,
                          json={"expected_revision": draft["revision"]})
    assert checked.status_code == 200, checked.text
    plans = [entry["plan"] for entry in checked.json()["current_window"]["entries"]]
    assert [(plan["language"], plan["presenter_share_target"], plan["approved_clip_count"]) for plan in plans] == [
        ("zh-CN", 20, 1), ("en-US", 20, 1),
    ]
    assert all([match["segment_ref"] for match in plan["material_matches"]] == ["segment:conveyor-shot"] for plan in plans)
    assert checked.json()["confirmed_count"] == 0

    restricted = production.update_asset_use_state(workspace, asset.id, "not_for_automation")
    assert client.patch(segment_path + "/usage-role", headers=owner_headers, json={
        **change, "usage_role": "a_roll", "expected_asset_updated_at": restricted.updated_at.isoformat(),
        "expected_segment_updated_at": restricted.segments[0].updated_at.isoformat(),
    }).status_code == 422
    assert production.get_asset(workspace, asset.id) == restricted
