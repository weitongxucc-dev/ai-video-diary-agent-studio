"""Production delegates asset aggregates without changing caller transactions."""
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app import production
from app.materials.assets import MaterialAssetStore
from app.materials import assets as store_module


@pytest.fixture
def asset_repository(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    path = library / "fixture.mp4"
    path.write_bytes(b"owned synthetic fixture; no decoding")
    when = datetime(2026, 10, 14, tzinfo=timezone.utc)
    repo = production.ProductionRepository(tmp_path / "fixture.sqlite3", library)
    asset = production.MaterialAsset("owned", "local", path.name, str(path), path.stat().st_size, when,
        owner_user_id="member", visibility="private", analysis_status="ready", review_status="approved",
        duration_seconds=1, segments=[production.MaterialSegment("segment", "owned", 0, 1)])
    repo._save_asset(asset)
    return repo, asset


def test_old_decoder_and_tag_exports_are_unique_store_callable_objects():
    for name in ("_asset_from_row", "_asset_from_payload", "_corrected_material_segments"):
        assert getattr(production.ProductionRepository, name) is getattr(MaterialAssetStore, name)
    assert production._normalized_labels is store_module._normalized_labels
    assert production._infer_system_tags is store_module._infer_system_tags


def test_production_asset_read_uses_exact_supplied_uncommitted_connection(asset_repository, monkeypatch):
    repo, asset = asset_repository
    with repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE material_assets SET payload=? WHERE workspace_id=? AND id=?", (
            production._json_payload(replace(asset, user_description="未提交文字")), "local", asset.id,
        ))
        with monkeypatch.context() as patch:
            def forbidden():
                raise AssertionError("supplied connection must not open a store connection")
            patch.setattr(repo._material_assets, "_connect", forbidden)
            read = repo.get_asset("local", asset.id, connection=conn)
            assert read.user_description == "未提交文字" and conn.in_transaction
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        conn.rollback()
    assert repo.get_asset("local", asset.id).user_description == ""


def test_actual_material_http_page_filters_before_count_and_preserves_owned_facts(asset_repository, monkeypatch):
    repo, asset = asset_repository
    other_path = repo.video_library_path / "other.mp4"
    other_path.write_bytes(b"separate synthetic private fixture")
    repo._save_asset(replace(asset, id="other", path=str(other_path), file_name=other_path.name,
        size_bytes=other_path.stat().st_size, owner_user_id="another-member",
        segments=[replace(asset.segments[0], id="other-segment", asset_id="other")]))
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "local")
    monkeypatch.setattr(main, "_content_reader", lambda *_: ("member", False))
    monkeypatch.setattr(main, "settings", replace(main.settings, video_inbox_path=str(repo.video_library_path)))
    response = TestClient(main.app).get("/api/production/materials?limit=1&offset=0")
    assert response.status_code == 200, response.text
    assert response.json()["total_count"] == 1
    assert [item["id"] for item in response.json()["assets"]] == [asset.id]
    assert response.json()["assets"][0]["segments"][0]["id"] == "segment"
