"""Reference-video study and bounded structural compare contracts."""

from pathlib import Path

import pytest

from app.creation.contracts import CreationTask, TimelineVersion
from app.creation.plan_contracts import CreationPlan
from app.production import ProductionRepository


WORKSPACE = "reference-video-test"


def _repository(tmp_path: Path) -> tuple[ProductionRepository, Path, str]:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "reference.mp4"
    source.write_bytes(b"synthetic-reference-source")
    repository = ProductionRepository(
        tmp_path / "production.sqlite3",
        library,
        duration_reader=lambda _path: 8.0,
        scene_reader=lambda _path, _duration: [4.0],
    )
    repository.material_source_files.bind_material_ingestion(
        WORKSPACE, expected_directory=str(library)
    )
    repository.material_source_files.confirm_material_copy_complete(
        WORKSPACE,
        relative_path=source.name,
        expected_revision=repository.material_source_files.material_copy_revision(source.stat()),
        confirmation_source="manual",
    )
    asset = repository.material_intake.scan_library(WORKSPACE)[0]
    repository.analyze_asset(WORKSPACE, asset.id)
    return repository, source, asset.id


def _save_task_with_timeline(repository: ProductionRepository) -> tuple[str, str, str]:
    plan = CreationPlan(
        id="plan-reference",
        workspace_id=WORKSPACE,
        goal="参考视频结构测试",
        name="参考视频结构测试",
        target_count=1,
        language="zh-CN",
        aspect_ratio="16:9",
        duration_target_seconds=8.0,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=0,
        approved_clip_count=0,
        material_rule_summary="测试",
        model_snapshot=[],
        confirmation_points=[],
        blockers=[],
        preflight_fingerprint="reference-test",
    )
    task = CreationTask(
        id="task-reference",
        workspace_id=WORKSPACE,
        plan_id=plan.id,
        name=plan.name,
        scenario="reference_video_test",
    )
    with repository._connect() as connection:
        repository.plan_records.insert_confirmed_creation_plan(connection, plan, task)
    version = repository.timeline_records.create_timeline_version(
        TimelineVersion(
            id="timeline-reference-1",
            workspace_id=WORKSPACE,
            task_id=task.id,
            parent_version_id=None,
            sequence=1,
            snapshot={
                "montage": {
                    "duration_seconds": 8.0,
                    "clips": [{"id": "clip-1"}, {"id": "clip-2"}],
                }
            },
            created_by_operation_id="reference-test-op",
        )
    )
    return task.id, version.id, plan.id


def test_reference_study_reuses_governed_segments_and_evidence(tmp_path):
    repository, _source, asset_id = _repository(tmp_path)

    study = repository.create_reference_video_study(WORKSPACE, asset_id)

    assert study.status == "ready"
    assert len(study.shots) == 2
    assert all(shot.asset_id == asset_id for shot in study.shots)
    assert all(shot.evidence_ref_ids for shot in study.shots)
    assert study.metrics["analysis_scope"] == "governed_asset_segments"
    assert study.metrics["visual_similarity"]["status"] == "unavailable"
    assert repository.get_reference_video_study(WORKSPACE, study.id) == study


def test_reference_study_requires_analysis_for_exact_source_revision(tmp_path):
    repository, source, asset_id = _repository(tmp_path)
    source.write_bytes(b"a changed source revision with different bytes")
    repository.material_source_files.confirm_material_copy_complete(
        WORKSPACE,
        relative_path=source.name,
        expected_revision=repository.material_source_files.material_copy_revision(source.stat()),
        confirmation_source="manual",
    )

    with pytest.raises(ValueError, match="当前源修订的素材分析"):
        repository.create_reference_video_study(WORKSPACE, asset_id)


def test_reference_study_becomes_stale_and_compare_binds_current_timeline(tmp_path):
    repository, source, asset_id = _repository(tmp_path)
    study = repository.create_reference_video_study(WORKSPACE, asset_id)
    task_id, timeline_id, _plan_id = _save_task_with_timeline(repository)

    report = repository.compare_reference_video_study(
        WORKSPACE, study.id, task_id, timeline_id
    )
    assert report.task_id == task_id
    assert report.timeline_version_id == timeline_id
    assert report.metrics["comparison_scope"] == "bounded_structural_timeline"
    assert report.acceptance == "requires_bounded_visual_review"
    assert report.visual_observation["status"] == "unavailable"
    assert report.audio_observation["status"] == "unavailable"
    assert report.reference_evidence_ref_ids

    current = repository.timeline_records.create_timeline_version(
        TimelineVersion(
            id="timeline-reference-2",
            workspace_id=WORKSPACE,
            task_id=task_id,
            parent_version_id=timeline_id,
            sequence=2,
            snapshot={"montage": {"duration_seconds": 4.0, "clips": [{"id": "clip-1"}]}},
            created_by_operation_id="reference-test-op-2",
        )
    )
    with pytest.raises(ValueError, match="当前任务时间线版本"):
        repository.compare_reference_video_study(WORKSPACE, study.id, task_id, timeline_id)
    assert current.id == repository.task_records.get_creation_task(
        WORKSPACE, task_id
    ).current_timeline_version_id

    repository.update_asset_use_state(WORKSPACE, asset_id, "excluded")
    with pytest.raises(ValueError, match="当前不可用"):
        repository.compare_reference_video_study(WORKSPACE, study.id, task_id, current.id)

    source.write_bytes(b"reference source changed after study")
    stale = repository.get_reference_video_study(WORKSPACE, study.id)
    assert stale.status == "stale"
    with pytest.raises(ValueError, match="已过期"):
        repository.compare_reference_video_study(WORKSPACE, study.id, task_id, current.id)
