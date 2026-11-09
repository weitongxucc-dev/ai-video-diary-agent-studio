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
