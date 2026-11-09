from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.production import MaterialAsset, MaterialSegment, ProductionRepository


def test_segment_usage_role_updates_only_its_derived_system_tag(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    media = library / "fixture.mp4"
    media.write_bytes(b"controlled fixture")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    segment = MaterialSegment(
        id="segment-one",
        asset_id="asset-one",
        start_seconds=0,
        end_seconds=1,
        description="原说明",
        transcript="保留转写",
        usage_role="b_roll",
        review_status="approved",
        system_tags=["用途:b_roll", "主体:人物", "场景:办公", "用途:b_roll"],
        user_tags=["人工校正"],
    )
    asset = MaterialAsset(
        id="asset-one",
        workspace_id="workspace-one",
        file_name=media.name,
        path=str(media),
        size_bytes=media.stat().st_size,
        modified_at=datetime.fromtimestamp(media.stat().st_mtime, timezone.utc),
        duration_seconds=1,
        usage_role="b_roll",
        review_status="approved",
        analysis_status="ready",
        visibility="shared",
        segments=[segment],
    )
    repo._save_asset(asset)

    updated = repo.update_segment(
        "workspace-one", "asset-one", "segment-one",
        description="人工新说明", transcript="保留转写", review_status="approved",
        usage_role="a_roll",
    ).segments[0]
    assert updated.usage_role == "a_roll"
    assert updated.system_tags == ["主体:人物", "场景:办公", "用途:a_roll"]
    assert updated.user_tags == ["人工校正"]
    assert updated.transcript == "保留转写"
    assert [item.segment_ref for item in repo.material_search.search_material_segments(
        "workspace-one", tags=["用途:a_roll"],
    )] == ["segment:segment-one"]
    assert repo.material_search.search_material_segments("workspace-one", tags=["用途:b_roll"]) == []

    unchanged_role = repo.update_segment(
        "workspace-one", "asset-one", "segment-one",
        description="又一次普通说明调整", transcript="保留转写", review_status="approved",
    ).segments[0]
    assert unchanged_role.system_tags == updated.system_tags
    assert unchanged_role.user_tags == updated.user_tags
    assert unchanged_role.transcript == updated.transcript
