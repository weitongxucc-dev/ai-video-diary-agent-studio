from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.production import _json_payload
from tests.test_material_search_candidates import _approved_asset, _repository


def test_material_search_filters_members_before_limit_and_keeps_shared_assets(
    tmp_path, monkeypatch,
):
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
        asset = _approved_asset(
            production,
            workspace,
            file_name,
            segments=[{"_start": 0.0, "description": "permissionneedle"}],
        )
        owned = replace(asset, owner_user_id=owner_id, visibility=visibility)
        with production._connect() as conn:
            conn.execute(
                "UPDATE material_assets SET payload=?, visibility=? WHERE workspace_id=? AND id=?",
                (_json_payload(owned), visibility, workspace, asset.id),
            )
        return asset

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
    first_ids = {row["asset_id"] for row in first_all.json()}
    second_ids = {row["asset_id"] for row in second_all.json()}
    assert first_ids == {shared.id, first_private.id}
    assert second_ids == {shared.id, second_private.id}

    # The second member's private asset is newest and matches equally. A limit
    # applied before permission filtering would hide the first member's result.
    first_limited = client.get(
        "/api/production/materials/search",
        params={"q": "permissionneedle", "limit": 1},
        headers=first_headers,
    )
    assert first_limited.status_code == 200
    assert first_limited.json()
    assert first_limited.json()[0]["asset_id"] != second_private.id
    assert first_limited.json()[0]["asset_id"] in {shared.id, first_private.id}
