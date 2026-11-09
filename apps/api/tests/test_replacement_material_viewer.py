from dataclasses import replace

from app.production import ProductionRepository


def test_replacement_search_keeps_private_candidate_for_owner_only(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    repo = ProductionRepository(
        tmp_path / "production.sqlite3", library,
        duration_reader=lambda _path: 10.0,
        scene_reader=lambda _path, _duration: [],
    )
    media = library / "replacement.mp4"
    media.write_bytes(b"fixture")
    workspace_id = "workspace-one"
    repo.material_source_files.bind_material_ingestion(workspace_id, expected_directory=str(library))
    repo.material_source_files.confirm_material_copy_complete(
        workspace_id, relative_path=media.name,
        expected_revision=repo.material_source_files.material_copy_revision(media.stat()), confirmation_source="manual",
    )
    repo.material_intake.scan_library(workspace_id)
    asset = next(item for item in repo.list_assets(workspace_id) if item.file_name == media.name)
    asset = repo.analyze_asset(workspace_id, asset.id)
    asset = repo.update_asset_review(workspace_id, asset.id, review_status="approved", usage_role="b_roll")
    segment = asset.segments[0]
    repo.update_segment(
        workspace_id, asset.id, segment.id, review_status="approved", usage_role="b_roll",
        description="户外画面，产品补充素材", transcript="",
    )
    asset = repo.get_asset(workspace_id, asset.id)
    segment = asset.segments[0]
    repo._save_asset(replace(asset, visibility="private", owner_user_id="member-owner"))

    def search(viewer: str | None):
        return repo.material_search.search_material_segments(
            workspace_id, query="户外画面", usage_roles=["a_roll", "b_roll"],
            min_duration_seconds=5, limit=24, viewer_user_id=viewer,
            local_diagnostic=False,
        )

    assert [item.segment_ref for item in search("member-owner")] == [f"segment:{segment.id}"]
    assert search("member-other") == []
    assert search(None) == []
