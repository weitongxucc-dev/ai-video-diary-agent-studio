import hashlib
import io
import json
import sqlite3
import wave
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.artifact_store import ArtifactStore
from app.config import Settings, record_llm_service_verification
from app.database_migrations import (
    PRODUCTION_SCHEMA_VERSION,
    _MIGRATIONS,
    apply_production_migrations,
)
from app.model_runtime import ModelCatalog
from app.local_members import LocalMemberService
from app.production import (
    ArtifactRecord,
    CreationPlan,
    CreationPlanPersonReference,
    CreationPlanVoiceReference,
    CreationPlanBlocker,
    CreationTask,
    DependencyEdge,
    EditIntent,
    EditOperation,
    EditProposal,
    JobRun,
    MaterialCollection,
    MediaAuthorityError,
    NarrationArtifactRecord,
    NarrationSegment,
    PersonRender,
    PronunciationEntry,
    ProductionBatch,
    ProductionProfile,
    ProductionRepository,
    ScriptGenerationRun,
    TaskEvent,
    TimelineVersion,
    TransformRun,
    VersionedVoiceProfile,
)
from app.repository import LEGACY_WORKSPACE_ID
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, SourceConnector, SourceItem
from app.topics import (
    CreationTaskGeneratedScript,
    CreationTaskGeneratedScriptSection,
    CreationTaskGeneratedTopic,
    CreationTaskTopicGenerationError,
)
from app.task_runtime import JobResult
from app.visual_semantics import VisualEmbedding
from app.video_jobs import VideoJobRunner
from _media_preview_test_fixtures import (
    register_synthetic_profiles,
    synthetic_wav_bytes,
    write_synthetic_video,
)


class _FakeVisualProvider:
    model_id = "test-chinese-clip"
    ready = True
    message = "ready"

    def __init__(self):
        self.calls = []

    def embed_segments(self, video_path, segments):
        self.calls.append((str(video_path), [item.segment_id for item in segments]))
        return [
            VisualEmbedding(
                segment_id=item.segment_id,
                vector=[0.1, 0.2, 0.3],
                sample_count=3,
            )
            for item in segments
        ]

    def embed_texts(self, texts):
        return [
            [0.1, 0.2, 0.3] if index < 3 else [-0.1, -0.2, -0.3]
            for index, _text in enumerate(texts)
        ]


def _repository(tmp_path: Path) -> ProductionRepository:
    library = tmp_path / "library"
    library.mkdir()
    return ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )



def _confirm_library_copies(production: ProductionRepository, workspace_id: str) -> None:
    """Explicit completion declarations for this test's fully written material files."""
    production.bind_material_ingestion(
        workspace_id, expected_directory=str(production.video_library_path),
    )
    for path in production.video_library_path.rglob("*"):
        if (path.is_file() and not path.is_symlink()
                and path.suffix.lower() in {".mp4", ".mov", ".m4v"}
                and "_已处理" not in path.parts):
            production.confirm_material_copy_complete(
                workspace_id, relative_path=str(path.relative_to(production.video_library_path)),
                expected_revision=production.material_copy_revision(path.stat()),
                confirmation_source="manual",
            )


def _save_creation_task(
    production: ProductionRepository,
    *,
    workspace_id: str,
    task_id: str,
) -> CreationTask:
    plan = CreationPlan(
        id=f"plan-{task_id}",
        workspace_id=workspace_id,
        goal="生成一条可编辑视频",
        name="生产底座测试",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=60,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=0,
        approved_clip_count=0,
        material_rule_summary="测试工作区内容",
        model_snapshot=[],
        confirmation_points=[],
        blockers=[],
        preflight_fingerprint=f"fingerprint-{task_id}",
    )
    task = CreationTask(
        id=task_id,
        workspace_id=workspace_id,
        plan_id=plan.id,
        name=plan.name,
        scenario="knowledge_explanation",
    )
    return production.confirm_creation_plan(plan, task)[1]


def _seed_confirmed_script(
    production: ProductionRepository,
    *,
    workspace_id: str,
    task_id: str,
) -> tuple[str, list[dict[str, object]]]:
    script_id = f"script-{task_id}"
    now = datetime.now(timezone.utc)
    sections: list[dict[str, object]] = [
        {
            "id": "section-1",
            "title": "开场",
            "content": "第一段已确认文案。",
            "visual_prompt": "人物开场",
            "evidence_reference_ids": ["knowledge:one"],
        },
        {
            "id": "section-2",
            "title": "正文",
            "content": "第二段已确认文案。",
            "visual_prompt": "补充画面",
            "evidence_reference_ids": ["knowledge:two"],
        },
    ]
    script_payload = {
        "id": script_id,
        "workspace_id": workspace_id,
        "task_id": task_id,
        "run_id": f"script-run-{task_id}",
        "topic_id": f"topic-{task_id}",
        "title": "已确认文案",
        "language": "zh-CN",
        "sections": sections,
        "selection_status": "confirmed",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "confirmed_at": now.isoformat(),
    }
    task = production.get_creation_task(workspace_id, task_id)
    task.confirmed_script_id = script_id
    task.stage = "narration"
    task.stage_status = "running"
    task.status = "running"
    task.recovery_point = "narration"
    task.updated_at = now
    with sqlite3.connect(production.database_path) as conn:
        conn.execute(
            """
            INSERT INTO creation_task_scripts
                (id, workspace_id, task_id, run_id, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                script_id,
                workspace_id,
                task_id,
                script_payload["run_id"],
                json.dumps(script_payload, ensure_ascii=False),
                now.isoformat(),
            ),
        )
        conn.execute(
            """
            UPDATE creation_tasks SET payload = ?, updated_at = ?
            WHERE workspace_id = ? AND id = ?
            """,
            (
                json.dumps(
                    task.__dict__,
                    ensure_ascii=False,
                    default=lambda value: value.isoformat(),
                ),
                now.isoformat(),
                workspace_id,
                task_id,
            ),
        )
    return script_id, sections


def _missing_model_catalog(tmp_path: Path) -> ModelCatalog:
    return ModelCatalog(
        tmp_path / "models",
        tmp_path / "runtime" / "bin" / "python",
        tmp_path / "visual-worker.py",
        tmp_path / "voice-worker.py",
    )


def _save_versioned_voice(
    production: ProductionRepository,
    *,
    workspace_id: str,
    voice_id: str,
    version: str,
    verified_owner_user_id: str,
    language: str = "zh",
    authorization_status: str = "active",
) -> VersionedVoiceProfile:
    person, voice, _avatar_authorization, _voice_path, _image_path = register_synthetic_profiles(
        production,
        workspace_id=workspace_id,
        member_id=verified_owner_user_id,
        person_id=f"{voice_id}-person",
        voice_id=voice_id,
        person_version="v1",
        voice_version=version,
        language=language,
        model_id="test-voice-model",
    )
    if authorization_status != "active":
        voice = replace(voice, authorization=replace(voice.authorization, status=authorization_status))
        voice = production.save_versioned_voice_profile(
            voice, verified_owner_user_id=verified_owner_user_id
        )
    return voice


@pytest.fixture
def plan_owner(tmp_path: Path, monkeypatch):
    """Use the existing trusted LocalMember-token test boundary, not anonymous access."""
    members = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", members)
    owner, token = members.whoami(None)
    return owner, {"X-Local-Member-Token": token}


def _seed_creation_plan_facts(
    production: ProductionRepository,
    sources: SourceRepository,
    *,
    workspace_id: str,
    verified_owner_user_id: str,
) -> Path:
    connector = SourceConnector(
        id=f"connector-{workspace_id}",
        workspace_id=workspace_id,
        platform="manual",
        protocol="manual",
        account_or_rule="operator",
        frequency="on_demand",
        permissions=("read",),
    )
    sources.upsert_connector(connector)
    source_item = SourceItem(
        id=f"source-{workspace_id}",
        workspace_id=workspace_id,
        connector_id=connector.id,
        source_label="manual",
        source_url="https://example.com/source",
        content_type="article",
        author="operator",
        published_at=None,
        body="真实信息源正文。",
        subtitles=None,
        media_references=(),
        license="authorized",
        title="信息源条目",
    )
    sources.save_source_items(workspace_id, [source_item])
    sources.create_knowledge_item_owned(
        KnowledgeItem(
            id=f"knowledge-{workspace_id}",
            workspace_id=workspace_id,
            content_type="note",
            title="用户知识",
            body="用户拥有并授权的知识内容。",
            file_reference=None,
            authorization="authorized",
            ownership="user_owned",
            source_item_id=source_item.id,
            version="v1",
        ),
        verified_user_id=verified_owner_user_id,
    )
    video = production.video_library_path / f"{workspace_id}.mp4"
    write_synthetic_video(video, seconds=25)
    _confirm_library_copies(production, workspace_id)
    asset = production.scan_library(workspace_id)[0]
    asset.owner_user_id = verified_owner_user_id
    production._save_asset(asset)
    video_reference = production.workspace_media_reference_for_asset(workspace_id, asset.id)
    with production._connect() as conn:
        production._claim_workspace_media_reference(
            conn, workspace_id=workspace_id, reference=video_reference
        )
    production.analyze_asset(workspace_id, asset.id)
    production.update_asset_review(workspace_id, asset.id, review_status="approved", usage_role="b_roll")
    _person, voice, _avatar_authorization, voice_path, _image_path = register_synthetic_profiles(
        production, workspace_id=workspace_id, member_id=verified_owner_user_id,
        person_id=f"voice-{workspace_id}-person", voice_id=f"voice-{workspace_id}",
        person_version="v1", voice_version="v1", language="zh", model_id="test-voice-model",
    )
    production.save_pronunciation_entry(
        PronunciationEntry(
            id=f"pronunciation-{workspace_id}",
            workspace_id=workspace_id,
            term="Codex",
            language="zh",
            target_pronunciation="科德克斯",
            scope="person",
            scope_reference=voice.person_profile_id,
            version="v1",
        )
    )
    return voice_path


def test_materials_require_analysis_and_usage_review_but_default_user_content_is_usable(tmp_path):
    production = _repository(tmp_path)
    video = production.video_library_path / "demo.mp4"
    video.write_bytes(b"test video placeholder")

    _confirm_library_copies(production, "workspace_a")
    assets = production.scan_library("workspace_a")
    assert len(assets) == 1
    assert assets[0].review_status == "raw"

    with pytest.raises(ValueError, match="镜头分析"):
        production.update_asset_review(
            "workspace_a",
            assets[0].id,
            review_status="approved",
            usage_role="b_roll",
        )

    analyzed = production.analyze_asset("workspace_a", assets[0].id)
    assert analyzed.analysis_status == "ready"
    assert [(item.start_seconds, item.end_seconds) for item in analyzed.segments] == [
        (0.0, 5.0),
        (5.0, 17.0),
        (17.0, 25.0),
    ]

    with pytest.raises(ValueError, match="先选择"):
        production.update_asset_review(
            "workspace_a",
            assets[0].id,
            review_status="approved",
        )

    approved = production.update_asset_review(
        "workspace_a",
        assets[0].id,
        review_status="approved",
        usage_role="b_roll",
    )
    assert approved.review_status == "approved"
    assert approved.use_state == "available"
    assert all(segment.review_status == "approved" for segment in approved.segments)


@pytest.mark.parametrize('use_state,usable',[
    ('available',True),('reference_only',False),('excluded',False),('not_for_automation',True),('not_for_publish',True),
])
def test_material_use_state_persists_and_controls_search_and_render(tmp_path,use_state,usable):
    production=_repository(tmp_path)
    members = LocalMemberService(production.database_path.with_name("members.sqlite3"))
    member, _token = members.whoami(None)
    write_synthetic_video(production.video_library_path/'state.mp4', seconds=25)
    _confirm_library_copies(production, 'workspace_a')
    asset=production.scan_library('workspace_a')[0]
    asset.owner_user_id = member.member_id
    production._save_asset(asset)
    assert asset.use_state=='available'
    asset=production.analyze_asset('workspace_a',asset.id)
    asset=production.update_asset_review('workspace_a',asset.id,review_status='approved',usage_role='b_roll')
    reference=production.workspace_media_reference_for_asset('workspace_a',asset.id)
    stale=production.get_asset('workspace_a',asset.id)
    changed=production.update_asset_use_state('workspace_a',asset.id,use_state)
    assert changed.use_state==use_state
    assert changed.segments==stale.segments
    production._save_asset(stale,analysis_only=True)
    restarted=ProductionRepository(database_path=production.database_path,video_library_path=production.video_library_path)
    assert restarted.get_asset('workspace_a',asset.id).use_state==use_state
    assert bool(restarted.search_material_segments(
        'workspace_a', usage_roles=['b_roll'], viewer_user_id=member.member_id
    ))==usable
    if usable:
        assert restarted.resolve_clip_media_reference('workspace_a',asset.id,reference).is_file()
    else:
        with pytest.raises(MediaAuthorityError):
            restarted.resolve_clip_media_reference('workspace_a',asset.id,reference)
    with pytest.raises(KeyError):
        restarted.update_asset_use_state('workspace_b',asset.id,'available')
    with pytest.raises(ValueError,match='使用状态'):
        restarted.update_asset_use_state('workspace_a',asset.id,'invalid')
    if use_state=='excluded':
        with pytest.raises(ValueError,match='不使用'):
            production.analyze_asset('workspace_a',asset.id)
    legacy=asdict(asset)
    legacy.pop('use_state')
    legacy.update(rights_confirmed=False,brand_safe=False)
    assert ProductionRepository._asset_from_payload(legacy).use_state=='available'
    for key,value in [('review_status','rejected'),('usage_role','exclude')]:
        assert ProductionRepository._asset_from_payload({**legacy,key:value}).use_state=='excluded'
    with restarted._connect() as conn:
        stored=json.loads(conn.execute('select payload from material_assets where id=?',(asset.id,)).fetchone()[0])
    assert 'rights_confirmed' not in stored and 'brand_safe' not in stored


def test_production_data_is_workspace_scoped_and_persistent(tmp_path):
    production = _repository(tmp_path)
    video = production.video_library_path / "shared.mov"
    video.write_bytes(b"test video placeholder")
    _confirm_library_copies(production, "workspace_a")
    asset = production.scan_library("workspace_a")[0]

    assert production.list_assets("workspace_b") == []
    with pytest.raises(KeyError):
        production.get_asset("workspace_b", asset.id)

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [],
    )
    assert reloaded.get_asset("workspace_a", asset.id).file_name == "shared.mov"


def test_material_search_and_plan_matches_are_explainable_scoped_and_persistent(
    tmp_path,
):
    production = _repository(tmp_path)
    members = LocalMemberService(production.database_path.with_name("members.sqlite3"))
    member, _token = members.whoami(None)
    video = production.video_library_path / "office-demo.mp4"
    write_synthetic_video(video, seconds=25)
    _confirm_library_copies(production, "workspace_a")
    asset = production.scan_library("workspace_a")[0]
    asset.owner_user_id = member.member_id
    production._save_asset(asset)
    asset = production.analyze_asset("workspace_a", asset.id)
    asset = production.update_asset_review(
        "workspace_a",
        asset.id,
        review_status="approved",
        usage_role="b_roll",
    )
    segment = asset.segments[0]
    asset = production.update_segment(
        "workspace_a",
        asset.id,
        segment.id,
        description="办公室里的电脑打字演示",
        transcript="演示如何使用电脑完成文档编辑",
        review_status="approved",
        usage_role="b_roll",
        user_tags=["项目:教程", "动作:打字"],
    )
    segment = next(item for item in asset.segments if item.id == segment.id)

    structured = production.search_material_segments(
        "workspace_a",
        usage_roles=["b_roll"],
        tags=["动作:打字"],
        min_duration_seconds=4,
        max_duration_seconds=6,
        viewer_user_id=member.member_id,
    )
    natural = production.search_material_segments(
        "workspace_a", query="我想找办公室打字镜头", viewer_user_id=member.member_id
    )
    assert structured[0].segment_ref == f"segment:{segment.id}"
    assert natural[0].segment_ref == structured[0].segment_ref
    assert structured[0].asset_ref == f"asset:{asset.id}"
    assert structured[0].reasons
    assert any("标签匹配" in reason for reason in structured[0].reasons)
    assert any("自然语言匹配" in reason for reason in natural[0].reasons)
    assert production.search_material_segments("workspace_b", query="打字") == []

    plan = CreationPlan(
        id="plan-material-match",
        workspace_id="workspace_a",
        goal="制作办公室教程视频",
        name="素材命中方案",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=60,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=1,
        approved_clip_count=1,
        material_rule_summary="用途、标签和时长联合命中",
        model_snapshot=[],
        confirmation_points=["确认素材镜头"],
        blockers=[],
        preflight_fingerprint="material-match-fingerprint",
        material_matches=[structured[0]],
    )
    task = CreationTask(
        id="task-material-match",
        workspace_id="workspace_a",
        plan_id=plan.id,
        name=plan.name,
        scenario=plan.goal,
    )
    plan.owner_user_id = member.member_id
    task.owner_user_id = member.member_id
    saved_plan, _ = production.confirm_creation_plan(
        plan, task, verified_owner_user_id=member.member_id
    )
    assert saved_plan.material_matches == [structured[0]]
    with pytest.raises(ValueError, match="实际命中"):
        production.confirm_creation_plan(
            replace(
                plan,
                id="plan-without-material-match",
                creation_task_id=None,
                material_matches=[],
            ),
            replace(
                task,
                id="task-without-material-match",
                plan_id="plan-without-material-match",
            ),
        )

    with sqlite3.connect(production.database_path) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM creation_plans WHERE id = ?", (plan.id,)
            ).fetchone()[0]
        )
        payload.pop("material_matches")
        payload["id"] = "legacy-plan-read-only"
        payload["creation_task_id"] = None
        conn.execute(
            """
            INSERT INTO creation_plans (id, workspace_id, payload, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                payload["id"],
                "workspace_a",
                json.dumps(payload, ensure_ascii=False),
                payload["updated_at"],
            ),
        )

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_creation_plan("workspace_a", plan.id).material_matches == [
        structured[0]
    ]
    assert reloaded.search_material_segments(
        "workspace_a", query="办公室打字", viewer_user_id=member.member_id
    )[0].segment_ref == structured[0].segment_ref
    assert (
        reloaded.get_creation_plan("workspace_a", "legacy-plan-read-only").material_matches
        == []
    )


def test_timeline_subtitle_and_cover_publish_or_roll_back_together(tmp_path):
    from app.creation.contracts import CoverAsset, SubtitleCue, SubtitleTrack

    repo = _repository(tmp_path)
    _save_creation_task(repo, workspace_id="workspace_a", task_id="task-a")
    base = repo.create_timeline_version(TimelineVersion(
        "base", "workspace_a", "task-a", None, 1, {}, None))
    version = TimelineVersion("delivery", "workspace_a", "task-a", base.id, 2,
        {"subtitle_track_ids": ["delivery-subtitles"], "cover_asset_id": "delivery-cover"}, None)
    track = SubtitleTrack("delivery-subtitles", "workspace_a", "task-a", version.id,
        "zh-CN", None, {}, [SubtitleCue("cue", None, "字幕", 0, 1)], "timeline", None,
        "artifacts/subtitles.srt", "subtitle-transform", "valid")
    cover = CoverAsset("delivery-cover", "workspace_a", "task-a", version.id,
        "artifacts/video.mp4", 0, "封面", {}, {}, "video_frame", "fixture",
        "artifacts/cover.png", "cover-transform", "valid")

    with pytest.raises(ValueError, match="封面缺少"):
        with repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            repo.create_timeline_version(version, connection=conn, expected_current_version_id=base.id)
            assert repo.create_subtitle_track(track, connection=conn) == track
            repo.create_cover_asset(replace(cover, title=""), connection=conn)
    assert repo.get_creation_task("workspace_a", "task-a").current_timeline_version_id == base.id
    with pytest.raises(KeyError):
        repo.get_timeline_version("workspace_a", version.id)
    with pytest.raises(KeyError):
        repo.get_subtitle_track("workspace_a", "task-a", track.id)
    with pytest.raises(KeyError):
        repo.get_cover_asset("workspace_a", "task-a", cover.id)

    with repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        repo.create_timeline_version(version, connection=conn, expected_current_version_id=base.id)
        assert repo.create_subtitle_track(track, connection=conn) == track
        assert repo.create_cover_asset(cover, connection=conn) == cover
        assert repo.get_creation_task("workspace_a", "task-a").current_timeline_version_id == base.id
        with pytest.raises(KeyError):
            repo.get_cover_asset("workspace_a", "task-a", cover.id)
    assert repo.get_creation_task("workspace_a", "task-a").current_timeline_version_id == version.id
    assert repo.get_subtitle_track("workspace_a", "task-a", track.id) == track
    assert repo.get_cover_asset("workspace_a", "task-a", cover.id) == cover
    with repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        repo.create_timeline_version(base, connection=conn, expected_current_version_id=version.id)
        assert repo.next_timeline_sequence("workspace_a", "task-a", connection=conn) == 3
        with pytest.raises(KeyError):
            repo.next_timeline_sequence("outside", "task-a", connection=conn)


def test_timeline_versions_are_append_only_workspace_scoped_and_restart_safe(
    tmp_path,
):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-a"
    )
    _save_creation_task(
        production, workspace_id="workspace_b", task_id="task-b"
    )
    version_one = production.create_timeline_version(
        TimelineVersion(
            id="timeline-v1",
            workspace_id="workspace_a",
            task_id="task-a",
            parent_version_id=None,
            sequence=1,
            snapshot={"tracks": [{"id": "track-1", "items": []}]},
            created_by_operation_id=None,
        )
    )
    version_two = production.create_timeline_version(
        TimelineVersion(
            id="timeline-v2",
            workspace_id="workspace_a",
            task_id="task-a",
            parent_version_id=version_one.id,
            sequence=2,
            snapshot={"tracks": [{"id": "track-1", "items": ["clip-1"]}]},
            created_by_operation_id="operation-1",
        )
    )

    assert [item.id for item in production.list_timeline_versions("workspace_a", "task-a")] == [
        "timeline-v1",
        "timeline-v2",
    ]
    assert (
        production.get_creation_task("workspace_a", "task-a").current_timeline_version_id
        == version_two.id
    )
    with pytest.raises(KeyError):
        production.get_timeline_version("workspace_b", version_one.id)
    with pytest.raises(ValueError, match="不能覆盖"):
        production.create_timeline_version(
            replace(version_two, snapshot={"tracks": []})
        )
    production.create_timeline_version(
        TimelineVersion(
            id="timeline-b-v1",
            workspace_id="workspace_b",
            task_id="task-b",
            parent_version_id=None,
            sequence=1,
            snapshot={},
            created_by_operation_id=None,
        )
    )
    with pytest.raises(ValueError, match="父时间线版本"):
        production.create_timeline_version(
            TimelineVersion(
                id="timeline-cross-workspace",
                workspace_id="workspace_b",
                task_id="task-b",
                parent_version_id=version_one.id,
                sequence=2,
                snapshot={},
                created_by_operation_id="operation-cross-workspace",
            )
        )

    with sqlite3.connect(production.database_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE timeline_versions SET snapshot_json = '{}' WHERE id = ?",
                (version_one.id,),
            )
        assert conn.execute(
            "SELECT version FROM production_schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, PRODUCTION_SCHEMA_VERSION + 1)]

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_timeline_version("workspace_a", version_two.id) == version_two
    assert (
        reloaded.get_creation_task("workspace_a", "task-a").current_timeline_version_id
        == version_two.id
    )


def test_job_artifact_and_event_records_are_recoverable_and_workspace_scoped(
    tmp_path,
):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-a"
    )
    created = production.create_job_run(
        JobRun(
            id="job-1",
            workspace_id="workspace_a",
            task_id="task-a",
            kind="render_preview",
            idempotency_key="render-preview:task-a:v1",
            input_digest="input-digest-v1",
        )
    )
    idempotent = production.create_job_run(
        replace(created, id="job-duplicate")
    )
    assert idempotent.id == created.id

    running = production.claim_job_run("workspace_a", created.id)
    assert (running.status, running.attempt) == ("running", 1)
    assert production.update_job_run_progress("workspace_a", created.id, 40).progress == 40
    with pytest.raises(ValueError, match="可追溯产物"):
        production.finish_job_run(
            "workspace_a",
            created.id,
            status="succeeded",
            output_refs=["artifacts/task-a/unregistered.mp4"],
        )
    artifact = production.create_artifact_record(
        ArtifactRecord(
            id="artifact-1",
            workspace_id="workspace_a",
            task_id="task-a",
            kind="preview",
            logical_ref="artifacts/task-a/preview-v1.mp4",
            sha256=hashlib.sha256(b"preview").hexdigest(),
            size_bytes=len(b"preview"),
            producer_job_id=created.id,
            input_refs=["timeline://task-a/timeline-v1"],
        )
    )
    event = production.append_task_event(
        TaskEvent(
            id="event-1",
            workspace_id="workspace_a",
            task_id="task-a",
            job_id=created.id,
            event_type="artifact_published",
            payload={"artifact_id": artifact.id},
        )
    )
    succeeded = production.finish_job_run(
        "workspace_a",
        created.id,
        status="succeeded",
        output_refs=[artifact.logical_ref],
    )
    assert (succeeded.status, succeeded.progress, succeeded.output_refs) == (
        "succeeded",
        100,
        [artifact.logical_ref],
    )

    interrupted_candidate = production.create_job_run(
        JobRun(
            id="job-2",
            workspace_id="workspace_a",
            task_id="task-a",
            kind="render_final",
            idempotency_key="render-final:task-a:v1",
            input_digest="input-digest-v2",
        )
    )
    production.claim_job_run("workspace_a", interrupted_candidate.id)

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_job_run("workspace_a", created.id) == succeeded
    assert reloaded.get_artifact_record("workspace_a", artifact.id) == artifact
    assert reloaded.get_task_event("workspace_a", event.id) == event
    assert reloaded.get_job_run("workspace_a", interrupted_candidate.id).status == "running"
    assert reloaded.interrupt_running_job_runs("workspace_a") == 1
    interrupted = reloaded.get_job_run("workspace_a", interrupted_candidate.id)
    assert interrupted.status == "interrupted"
    assert interrupted.failure_reason == "process_interrupted"

    reloaded.claim_job_run("workspace_a", interrupted.id)
    _save_creation_task(
        reloaded, workspace_id="workspace_b", task_id="task-b"
    )
    workspace_b_job = reloaded.create_job_run(
        JobRun(
            id="job-workspace-b",
            workspace_id="workspace_b",
            task_id="task-b",
            kind="render_preview",
            idempotency_key="render-preview:task-b:v1",
            input_digest="input-digest-workspace-b",
        )
    )
    reloaded.claim_job_run("workspace_b", workspace_b_job.id)
    assert reloaded.interrupt_running_job_runs() == 2
    assert reloaded.get_job_run("workspace_a", interrupted.id).status == "interrupted"
    assert reloaded.get_job_run("workspace_b", workspace_b_job.id).status == "interrupted"

    for getter, record_id in (
        (reloaded.get_job_run, created.id),
        (reloaded.get_artifact_record, artifact.id),
        (reloaded.get_task_event, event.id),
    ):
        with pytest.raises(KeyError):
            getter("workspace_b", record_id)
    with pytest.raises(KeyError):
        reloaded.create_job_run(
            JobRun(
                id="job-cross-workspace",
                workspace_id="workspace_b",
                task_id="task-a",
                kind="render_preview",
                idempotency_key="cross-workspace",
                input_digest="input-digest-cross-workspace",
            )
        )


def test_transform_and_dependency_lineage_is_validated_and_restart_safe(tmp_path):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-a"
    )
    _save_creation_task(
        production, workspace_id="workspace_b", task_id="task-b"
    )
    job = production.create_job_run(
        JobRun(
            id="job-render",
            workspace_id="workspace_a",
            task_id="task-a",
            kind="render_preview",
            idempotency_key="render-preview:task-a:v2",
            input_digest="render-input-digest",
        )
    )
    production.claim_job_run("workspace_a", job.id)
    preview_ref = "artifacts/task-a/job-render/preview.mp4"
    subtitles_ref = "artifacts/task-a/job-render/subtitles.srt"
    for artifact_id, kind, logical_ref, payload in (
        ("artifact-preview", "preview", preview_ref, b"preview"),
        ("artifact-subtitles", "subtitles", subtitles_ref, b"subtitles"),
    ):
        production.create_artifact_record(
            ArtifactRecord(
                id=artifact_id,
                workspace_id="workspace_a",
                task_id="task-a",
                kind=kind,
                logical_ref=logical_ref,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                producer_job_id=job.id,
                input_refs=["timeline://task-a/timeline-v1"],
            )
        )
    started_at = datetime.now(timezone.utc)
    transform = production.create_transform_run(
        TransformRun(
            id="transform-render",
            workspace_id="workspace_a",
            task_id="task-a",
            job_id=job.id,
            transform_type="timeline_render",
            input_digest=job.input_digest,
            input_refs=["timeline://task-a/timeline-v1"],
            output_refs=[preview_ref, subtitles_ref],
            engine="ffmpeg",
            parameters={"video_codec": "h264", "subtitle_format": "srt"},
            status="succeeded",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
    )
    edge = production.create_dependency_edge(
        DependencyEdge(
            id="dependency-preview",
            workspace_id="workspace_a",
            task_id="task-a",
            upstream_ref=transform.input_refs[0],
            downstream_ref=preview_ref,
            created_by_transform_id=transform.id,
        )
    )

    with pytest.raises(ValueError, match="JobRun"):
        production.create_transform_run(
            replace(
                transform,
                id="transform-cross-workspace",
                workspace_id="workspace_b",
                task_id="task-b",
            )
        )
    with pytest.raises(ValueError, match="已登记的产物"):
        production.create_transform_run(
            replace(
                transform,
                id="transform-missing-artifact",
                output_refs=["artifacts/task-a/job-render/missing.mp4"],
            )
        )
    with pytest.raises(ValueError, match="上游"):
        production.create_dependency_edge(
            replace(
                edge,
                id="dependency-unrelated-input",
                upstream_ref="timeline://task-a/unrelated",
            )
        )
    with pytest.raises(ValueError, match="绝对路径"):
        production.create_dependency_edge(
            replace(edge, id="dependency-absolute", upstream_ref="/tmp/input.mp4")
        )

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_transform_run("workspace_a", transform.id) == transform
    assert reloaded.get_dependency_edge("workspace_a", edge.id) == edge
    assert reloaded.list_transform_runs("workspace_a", "task-a") == [transform]
    assert reloaded.list_dependency_edges("workspace_a", "task-a") == [edge]
    with pytest.raises(KeyError):
        reloaded.get_transform_run("workspace_b", transform.id)
    with sqlite3.connect(production.database_path) as conn:
        assert conn.execute(
            "SELECT version FROM production_schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, PRODUCTION_SCHEMA_VERSION + 1)]


def test_production_fact_appends_are_strictly_idempotent_on_replay(tmp_path):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-a"
    )
    job = production.create_job_run(
        JobRun(
            id="job-replay",
            workspace_id="workspace_a",
            task_id="task-a",
            kind="render_preview",
            idempotency_key="render-preview:task-a:replay",
            input_digest="replay-input-digest",
        )
    )
    production.claim_job_run("workspace_a", job.id)
    artifact = ArtifactRecord(
        id="artifact-replay",
        workspace_id="workspace_a",
        task_id="task-a",
        kind="preview",
        logical_ref="artifacts/task-a/job-replay/preview.mp4",
        sha256=hashlib.sha256(b"replay-preview").hexdigest(),
        size_bytes=len(b"replay-preview"),
        producer_job_id=job.id,
        input_refs=["timeline://task-a/base"],
    )
    production.create_artifact_record(artifact)
    transform = TransformRun(
        id="transform-replay",
        workspace_id="workspace_a",
        task_id="task-a",
        job_id=job.id,
        transform_type="timeline_render",
        input_digest=job.input_digest,
        input_refs=["timeline://task-a/base"],
        output_refs=[artifact.logical_ref],
        engine="ffmpeg",
        parameters={"video_codec": "h264"},
        status="succeeded",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    production.create_transform_run(transform)
    edge = DependencyEdge(
        id="dependency-replay",
        workspace_id="workspace_a",
        task_id="task-a",
        upstream_ref=transform.input_refs[0],
        downstream_ref=artifact.logical_ref,
        created_by_transform_id=transform.id,
    )
    production.create_dependency_edge(edge)
    event = TaskEvent(
        id="event-replay",
        workspace_id="workspace_a",
        task_id="task-a",
        job_id=job.id,
        event_type="render_facts_registered",
        payload={"artifact_id": artifact.id, "transform_id": transform.id},
    )
    production.append_task_event(event)
    version = TimelineVersion(
        id="timeline-replay",
        workspace_id="workspace_a",
        task_id="task-a",
        parent_version_id=None,
        sequence=1,
        snapshot={"preview_output_ref": artifact.logical_ref},
        created_by_operation_id="operation-replay",
    )
    production.create_timeline_version(version)
    production.finish_job_run(
        "workspace_a",
        job.id,
        status="succeeded",
        output_refs=[artifact.logical_ref],
    )

    assert production.create_artifact_record(artifact) == artifact
    assert production.create_transform_run(transform) == transform
    assert production.create_dependency_edge(edge) == edge
    assert production.append_task_event(event) == event
    assert production.create_timeline_version(version) == version
    assert (
        production.get_creation_task("workspace_a", "task-a").current_timeline_version_id
        == version.id
    )

    with pytest.raises(ValueError, match="领域字段不一致"):
        production.create_artifact_record(replace(artifact, size_bytes=99))
    with pytest.raises(ValueError, match="领域字段不一致"):
        production.create_artifact_record(
            replace(artifact, id="artifact-same-logical-ref")
        )
    with pytest.raises(ValueError, match="领域字段不一致"):
        production.create_transform_run(replace(transform, engine="other-engine"))
    with pytest.raises(ValueError, match="领域字段不一致"):
        production.create_dependency_edge(
            replace(edge, created_at=datetime.now(timezone.utc))
        )
    with pytest.raises(ValueError, match="领域字段不一致"):
        production.append_task_event(replace(event, payload={"artifact_id": "other"}))
    with pytest.raises(ValueError, match="不能覆盖"):
        production.create_timeline_version(replace(version, snapshot={"changed": True}))


def test_edit_domain_is_scoped_stateful_atomic_and_restart_safe(tmp_path):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-a"
    )
    _save_creation_task(
        production, workspace_id="workspace_b", task_id="task-b"
    )
    base_version = production.create_timeline_version(
        TimelineVersion(
            id="timeline-edit-base",
            workspace_id="workspace_a",
            task_id="task-a",
            parent_version_id=None,
            sequence=1,
            snapshot={"clips": [{"id": "clip-1", "asset_ref": "asset://one"}]},
            created_by_operation_id=None,
        )
    )
    intent = EditIntent(
        id="intent-replace-clip",
        workspace_id="workspace_a",
        task_id="task-a",
        raw_command="把这个镜头换成另一个",
        context_stage="montage",
        selected_object_refs=["clip:clip-1"],
        playhead_ms=1200,
        intent_type="replace_visual",
        scope={"kind": "selected_clip"},
        constraints={"keep_duration": True},
        confidence=0.98,
        status="interpreted",
    )
    assert production.create_edit_intent(intent) == intent
    assert production.create_edit_intent(intent) == intent
    with pytest.raises(ValueError, match="不能覆盖"):
        production.create_edit_intent(replace(intent, raw_command="不同命令"))
    with pytest.raises(KeyError):
        production.get_edit_intent("workspace_b", intent.id)

    proposal = EditProposal(
        id="proposal-replace-clip",
        workspace_id="workspace_a",
        task_id="task-a",
        intent_id=intent.id,
        summary="将 clip-1 替换为 clip-2",
        target_refs=["clip:clip-1"],
        operation={
            "operation_type": "ReplaceClip",
            "target_ref": "clip:clip-1",
            "replacement_ref": "clip:clip-2",
        },
        invalidated_refs=["artifacts/task-a/old/preview.mp4"],
        preview={"before": "clip:clip-1", "after": "clip:clip-2"},
        external_effects=[],
    )
    assert production.create_edit_proposal(proposal) == proposal
    rejected = production.create_edit_proposal(
        replace(
            proposal,
            id="proposal-rejected",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    rejected = production.transition_edit_proposal(
        "workspace_a", "task-a", rejected.id, status="rejected"
    )
    assert rejected.status == "rejected"
    with pytest.raises(ValueError, match="proposed"):
        production.transition_edit_proposal(
            "workspace_a", "task-a", rejected.id, status="confirmed"
        )
    expired = production.create_edit_proposal(
        replace(
            proposal,
            id="proposal-expired",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    assert production.transition_edit_proposal(
        "workspace_a", "task-a", expired.id, status="expired"
    ).status == "expired"
    with pytest.raises(ValueError, match="意图不属于"):
        production.create_edit_proposal(
            replace(
                proposal,
                id="proposal-cross-workspace",
                workspace_id="workspace_b",
                task_id="task-b",
            )
        )

    montage_plan_id = "montage-task-a"
    old_montage_payload = {
        "id": montage_plan_id,
        "task_id": "task-a",
        "status": "rendered",
        "output_path": "artifacts/task-a/old/preview.mp4",
        "subtitle_path": "artifacts/task-a/old/subtitles.srt",
    }
    with sqlite3.connect(production.database_path) as conn:
        conn.execute(
            """
            INSERT INTO creation_task_montage_plans
                (id, workspace_id, task_id, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                montage_plan_id,
                "workspace_a",
                "task-a",
                json.dumps(old_montage_payload, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    operation_created_at = datetime.now(timezone.utc)
    operation = EditOperation(
        id="operation-replace-clip",
        workspace_id="workspace_a",
        task_id="task-a",
        proposal_id=proposal.id,
        operation_type="ReplaceClip",
        payload={"target_clip_id": "clip-1", "replacement_clip_id": "clip-2"},
        base_version_id=base_version.id,
        result_version_id="timeline-edit-result",
        status="applied",
        created_at=operation_created_at,
        applied_at=datetime.now(timezone.utc),
    )
    result_version = TimelineVersion(
        id="timeline-edit-result",
        workspace_id="workspace_a",
        task_id="task-a",
        parent_version_id=base_version.id,
        sequence=2,
        snapshot={"clips": [{"id": "clip-2", "asset_ref": "asset://two"}]},
        created_by_operation_id=operation.id,
    )
    ready_montage_payload = {
        "id": montage_plan_id,
        "task_id": "task-a",
        "status": "ready",
        "clips": [{"id": "clip-2", "asset_ref": "asset://two"}],
    }
    confirmed, applied, persisted_version, task = production.confirm_edit_proposal(
        "workspace_a",
        "task-a",
        proposal.id,
        operation=operation,
        result_version=result_version,
        montage_plan_id=montage_plan_id,
        montage_payload=ready_montage_payload,
    )
    assert confirmed.status == "confirmed"
    assert applied == operation
    assert persisted_version == result_version
    assert task.current_timeline_version_id == result_version.id
    assert task.output_ready is False
    saved_montage = production.get_creation_task_montage_plan(
        "workspace_a", "task-a"
    )
    assert saved_montage["status"] == "ready"
    assert "output_path" not in saved_montage
    assert "subtitle_path" not in saved_montage

    with pytest.raises(ValueError, match="proposed"):
        production.confirm_edit_proposal(
            "workspace_a",
            "task-a",
            proposal.id,
            operation=operation,
            result_version=result_version,
        )
    with pytest.raises(ValueError, match="不能覆盖"):
        production.create_edit_operation(
            replace(operation, payload={"target_clip_id": "other"})
        )
    with sqlite3.connect(production.database_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE edit_intents SET raw_command = '被覆盖' WHERE id = ?",
                (intent.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE edit_operations SET payload_json = '{}' WHERE id = ?",
                (operation.id,),
            )
        assert conn.execute(
            "SELECT version FROM production_schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, PRODUCTION_SCHEMA_VERSION + 1)]

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_edit_intent("workspace_a", intent.id) == intent
    assert reloaded.get_edit_proposal("workspace_a", proposal.id).status == "confirmed"
    assert reloaded.get_edit_operation("workspace_a", operation.id) == operation
    assert reloaded.get_timeline_version("workspace_a", result_version.id) == result_version
    assert reloaded.list_edit_intents("workspace_a", "task-a") == [intent]
    assert len(reloaded.list_edit_proposals("workspace_a", "task-a")) == 3
    assert reloaded.list_edit_operations("workspace_a", "task-a") == [operation]


def test_generic_edit_confirmation_is_atomic_for_all_frozen_clip_operations(
    tmp_path,
):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-a"
    )
    current_version = production.create_timeline_version(
        TimelineVersion(
            id="timeline-generic-0",
            workspace_id="workspace_a",
            task_id="task-a",
            parent_version_id=None,
            sequence=1,
            snapshot={"clips": [{"id": "clip-1", "locked": False}]},
            created_by_operation_id=None,
        )
    )
    montage_plan_id = "montage-generic"
    with sqlite3.connect(production.database_path) as conn:
        conn.execute(
            """
            INSERT INTO creation_task_montage_plans
                (id, workspace_id, task_id, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                montage_plan_id,
                "workspace_a",
                "task-a",
                json.dumps(
                    {
                        "id": montage_plan_id,
                        "task_id": "task-a",
                        "status": "ready",
                        "clips": [{"id": "clip-1", "locked": False}],
                    },
                    ensure_ascii=False,
                ),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    operation_types = (
        "ReplaceClip",
        "TrimClip",
        "ReorderClip",
        "SetClipLock",
        "ConfirmClip",
        "UndoTimelineVersion",
    )
    for index, operation_type in enumerate(operation_types, start=1):
        intent = production.create_edit_intent(
            EditIntent(
                id=f"intent-generic-{index}",
                workspace_id="workspace_a",
                task_id="task-a",
                raw_command=f"执行 {operation_type}",
                context_stage="montage",
                selected_object_refs=["clip:clip-1"],
                playhead_ms=None,
                intent_type="precise_clip_action",
                scope={"kind": "selected_clip"},
                constraints={},
                confidence=1.0,
                status="interpreted",
            )
        )
        proposal = production.create_edit_proposal(
            EditProposal(
                id=f"proposal-generic-{index}",
                workspace_id="workspace_a",
                task_id="task-a",
                intent_id=intent.id,
                summary=f"确定性执行 {operation_type}",
                target_refs=["clip:clip-1"],
                operation={"operation_type": operation_type},
                invalidated_refs=["artifacts/task-a/current/preview.mp4"],
                preview={"operation_type": operation_type},
                external_effects=[],
            )
        )
        operation_id = f"operation-generic-{index}"
        result_version = TimelineVersion(
            id=f"timeline-generic-{index}",
            workspace_id="workspace_a",
            task_id="task-a",
            parent_version_id=current_version.id,
            sequence=index + 1,
            snapshot={"operation_type": operation_type, "clip_id": "clip-1"},
            created_by_operation_id=operation_id,
        )
        operation = EditOperation(
            id=operation_id,
            workspace_id="workspace_a",
            task_id="task-a",
            proposal_id=proposal.id,
            operation_type=operation_type,  # type: ignore[arg-type]
            payload={"clip_id": "clip-1", "operation_type": operation_type},
            base_version_id=current_version.id,
            result_version_id=result_version.id,
            status="applied",
            created_at=datetime.now(timezone.utc),
            applied_at=datetime.now(timezone.utc),
        )
        confirmed, applied, current_version, task = production.confirm_edit_proposal(
            "workspace_a",
            "task-a",
            proposal.id,
            operation=operation,
            result_version=result_version,
            montage_plan_id=montage_plan_id,
            montage_payload={
                "id": montage_plan_id,
                "task_id": "task-a",
                "status": "ready",
                "operation_type": operation_type,
                "clips": [{"id": "clip-1"}],
            },
        )
        assert confirmed.status == "confirmed"
        assert applied.operation_type == operation_type
        assert task.current_timeline_version_id == current_version.id

    versions = production.list_timeline_versions("workspace_a", "task-a")
    assert [version.sequence for version in versions] == list(range(1, 8))
    assert [
        operation.operation_type
        for operation in production.list_edit_operations("workspace_a", "task-a")
    ] == list(operation_types)


def test_segmented_narration_typed_edits_and_undo_are_atomic_and_restart_safe(
    tmp_path,
):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-narration"
    )
    script_id, sections = _seed_confirmed_script(
        production,
        workspace_id="workspace_a",
        task_id="task-narration",
    )
    created_at = datetime.now(timezone.utc)
    initial_segments = [
        NarrationSegment(
            id=f"narration-segment-{index}",
            workspace_id="workspace_a",
            task_id="task-narration",
            script_id=script_id,
            source_section_id=str(section["id"]),
            text=str(section["content"]),
            audio_reference=f"narration://segment/{index}/v1",
            duration_seconds=2.5 + index,
            sample_rate=24_000,
            model_id="qwen3-tts",
            voice_profile_id="voice-owner",
            voice_version="voice-v1",
            language="zh-CN",
            pronunciation_version="pronunciation-v1",
            status="ready",
            created_at=created_at,
            updated_at=created_at,
        )
        for index, section in enumerate(sections, start=1)
    ]
    initial_audio = tmp_path / "outputs" / "narration-v1.wav"
    initial_audio.parent.mkdir()
    initial_audio.write_bytes(b"whole-track-v1")
    initial_artifact = NarrationArtifactRecord(
        id="narration-artifact-v1",
        workspace_id="workspace_a",
        task_id="task-narration",
        episode_id=None,
        script_id=script_id,
        voice_profile_id="voice-owner",
        cache_key="narration-cache-v1",
        audio_path=str(initial_audio),
        duration_seconds=8.0,
        sample_rate=24_000,
        model_id="qwen3-tts",
        language="zh-CN",
        pronunciation_version="pronunciation-v1",
    )

    with pytest.raises(ValueError, match="每个段落"):
        production.complete_creation_task_narration(
            initial_artifact,
            segments=initial_segments[:1],
        )
    assert production.get_narration_artifact(
        "workspace_a", initial_artifact.cache_key
    ) is None
    assert production.list_narration_segments(
        "workspace_a", "task-narration", script_id
    ) == []
    assert production.get_creation_task(
        "workspace_a", "task-narration"
    ).stage_status == "running"

    completed_task, _ = production.complete_creation_task_narration(
        initial_artifact,
        segments=initial_segments,
    )
    assert completed_task.narration_cache_key == initial_artifact.cache_key
    assert production.list_narration_segments(
        "workspace_a", "task-narration", script_id
    ) == initial_segments
    with pytest.raises(KeyError):
        production.get_narration_segment(
            "workspace_b", "task-narration", initial_segments[0].id
        )


    montage_plan_id = "montage-narration"
    initial_cues = [
        {
            "id": "cue-1",
            "narration_segment_id": initial_segments[0].id,
            "text": initial_segments[0].text,
            "start_ms": 0,
            "end_ms": 3500,
        },
        {
            "id": "cue-2",
            "narration_segment_id": initial_segments[1].id,
            "text": initial_segments[1].text,
            "start_ms": 3500,
            "end_ms": 8000,
        },
    ]
    initial_montage = {
        "id": montage_plan_id,
        "task_id": "task-narration",
        "status": "ready",
        "clips": [{"id": "clip-1"}],
        "cues": initial_cues,
    }
    production.save_creation_task_montage_plan(
        "workspace_a", "task-narration", montage_plan_id, initial_montage
    )

    def snapshot(
        segments: list[NarrationSegment],
        cues: list[dict[str, object]],
        montage: dict[str, object],
    ) -> dict[str, object]:
        return {
            "narration_segments": [
                {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in segment.__dict__.items()
                }
                for segment in segments
            ],
            "subtitle_cues": cues,
            "montage": montage,
            "dependencies": ["narration", "subtitles", "render-output"],
        }

    base_version = production.create_timeline_version(
        TimelineVersion(
            id="timeline-narration-base",
            workspace_id="workspace_a",
            task_id="task-narration",
            parent_version_id=None,
            sequence=1,
            snapshot=snapshot(initial_segments, initial_cues, initial_montage),
            created_by_operation_id=None,
        )
    )

    def prepare_edit(
        *,
        suffix: str,
        operation_type: str,
        target_ref: str,
        base: TimelineVersion,
        sequence: int,
        result_snapshot: dict[str, object],
    ) -> tuple[EditProposal, EditOperation, TimelineVersion]:
        intent = production.create_edit_intent(
            EditIntent(
                id=f"intent-{suffix}",
                workspace_id="workspace_a",
                task_id="task-narration",
                raw_command=operation_type,
                context_stage="montage",
                selected_object_refs=[target_ref],
                playhead_ms=None,
                intent_type="typed_narration_edit",
                scope={"target_ref": target_ref},
                constraints={},
                confidence=1.0,
                status="interpreted",
            )
        )
        proposal = production.create_edit_proposal(
            EditProposal(
                id=f"proposal-{suffix}",
                workspace_id="workspace_a",
                task_id="task-narration",
                intent_id=intent.id,
                summary=operation_type,
                target_refs=[target_ref],
                operation={"operation_type": operation_type, "target_ref": target_ref},
                invalidated_refs=["artifact:render-output"],
                preview={"operation_type": operation_type},
                external_effects=[],
            )
        )
        operation_id = f"operation-{suffix}"
        result = TimelineVersion(
            id=f"timeline-{suffix}",
            workspace_id="workspace_a",
            task_id="task-narration",
            parent_version_id=base.id,
            sequence=sequence,
            snapshot=result_snapshot,
            created_by_operation_id=operation_id,
        )
        operation_created_at = datetime.now(timezone.utc)
        operation = EditOperation(
            id=operation_id,
            workspace_id="workspace_a",
            task_id="task-narration",
            proposal_id=proposal.id,
            operation_type=operation_type,  # type: ignore[arg-type]
            payload={"target_ref": target_ref},
            base_version_id=base.id,
            result_version_id=result.id,
            status="applied",
            created_at=operation_created_at,
            applied_at=datetime.now(timezone.utc),
        )
        return proposal, operation, result

    pending_time = datetime.now(timezone.utc)
    pending_segments = [
        replace(
            initial_segments[0],
            audio_reference=None,
            duration_seconds=None,
            sample_rate=None,
            status="pending",
            updated_at=pending_time,
        ),
        initial_segments[1],
    ]
    pending_montage = {**initial_montage, "cues": initial_cues}
    regenerate_proposal, regenerate_operation, regenerate_version = prepare_edit(
        suffix="regenerate",
        operation_type="RegenerateNarrationSegment",
        target_ref=f"narration-segment:{initial_segments[0].id}",
        base=base_version,
        sequence=2,
        result_snapshot=snapshot(pending_segments, initial_cues, pending_montage),
    )
    _, applied_regenerate, current_version, pending_task = (
        production.confirm_edit_proposal(
            "workspace_a",
            "task-narration",
            regenerate_proposal.id,
            operation=regenerate_operation,
            result_version=regenerate_version,
            montage_plan_id=montage_plan_id,
            montage_payload=pending_montage,
            narration_segments=pending_segments,
        )
    )
    assert applied_regenerate.operation_type == "RegenerateNarrationSegment"
    assert pending_task.stage == "narration"
    assert pending_task.stage_status == "not_started"
    persisted_pending = production.list_narration_segments(
        "workspace_a", "task-narration"
    )
    assert persisted_pending[0].status == "pending"
    assert persisted_pending[1].audio_reference == initial_segments[1].audio_reference

    production.start_creation_task_narration("workspace_a", "task-narration")
    regenerated_time = datetime.now(timezone.utc)
    regenerated_segments = [
        replace(
            pending_segments[0],
            audio_reference="narration://segment/1/v2",
            duration_seconds=4.0,
            sample_rate=24_000,
            status="ready",
            updated_at=regenerated_time,
        ),
        initial_segments[1],
    ]
    regenerated_audio = tmp_path / "outputs" / "narration-v2.wav"
    regenerated_audio.write_bytes(b"whole-track-v2")
    regenerated_artifact = replace(
        initial_artifact,
        id="narration-artifact-v2",
        cache_key="narration-cache-v2",
        audio_path=str(regenerated_audio),
        duration_seconds=8.5,
        created_at=regenerated_time,
    )
    regenerated_task, _ = production.complete_creation_task_narration(
        regenerated_artifact,
        segments=regenerated_segments,
    )
    assert regenerated_task.narration_cache_key == regenerated_artifact.cache_key

    updated_cues = [
        {**initial_cues[0], "text": "第一段\n已修改字幕。"},
        initial_cues[1],
    ]
    subtitle_montage = {**initial_montage, "cues": updated_cues}
    subtitle_proposal, subtitle_operation, subtitle_version = prepare_edit(
        suffix="subtitle",
        operation_type="UpdateSubtitleCue",
        target_ref="subtitle-cue:cue-1",
        base=current_version,
        sequence=3,
        result_snapshot=snapshot(
            regenerated_segments, updated_cues, subtitle_montage
        ),
    )
    with pytest.raises(ValueError, match="每个段落"):
        production.confirm_edit_proposal(
            "workspace_a",
            "task-narration",
            subtitle_proposal.id,
            operation=subtitle_operation,
            result_version=subtitle_version,
            montage_plan_id=montage_plan_id,
            montage_payload=subtitle_montage,
            narration_segments=regenerated_segments[:1],
        )
    assert production.get_edit_proposal(
        "workspace_a", subtitle_proposal.id
    ).status == "proposed"
    with pytest.raises(KeyError):
        production.get_timeline_version("workspace_a", subtitle_version.id)
    assert production.get_creation_task(
        "workspace_a", "task-narration"
    ).current_timeline_version_id == current_version.id

    _, applied_subtitle, current_version, subtitle_task = (
        production.confirm_edit_proposal(
            "workspace_a",
            "task-narration",
            subtitle_proposal.id,
            operation=subtitle_operation,
            result_version=subtitle_version,
            montage_plan_id=montage_plan_id,
            montage_payload=subtitle_montage,
            narration_segments=regenerated_segments,
        )
    )
    assert applied_subtitle.operation_type == "UpdateSubtitleCue"
    assert subtitle_task.narration_cache_key == regenerated_artifact.cache_key
    assert production.list_narration_segments(
        "workspace_a", "task-narration"
    ) == regenerated_segments

    restored_time = datetime.now(timezone.utc)
    restored_segments = [
        replace(segment, updated_at=restored_time) for segment in initial_segments
    ]
    undo_proposal, undo_operation, undo_version = prepare_edit(
        suffix="undo-narration",
        operation_type="UndoTimelineVersion",
        target_ref=f"timeline:{base_version.id}",
        base=current_version,
        sequence=4,
        result_snapshot=snapshot(restored_segments, initial_cues, initial_montage),
    )
    _, applied_undo, restored_version, restored_task = production.confirm_edit_proposal(
        "workspace_a",
        "task-narration",
        undo_proposal.id,
        operation=undo_operation,
        result_version=undo_version,
        montage_plan_id=montage_plan_id,
        montage_payload=initial_montage,
        narration_segments=restored_segments,
        narration_artifact=initial_artifact,
    )
    assert applied_undo.operation_type == "UndoTimelineVersion"
    assert restored_task.current_timeline_version_id == restored_version.id
    assert restored_task.narration_cache_key == initial_artifact.cache_key
    assert production.list_narration_segments(
        "workspace_a", "task-narration"
    ) == restored_segments
    assert production.get_creation_task_montage_plan(
        "workspace_a", "task-narration"
    )["cues"] == initial_cues

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.list_narration_segments(
        "workspace_a", "task-narration"
    ) == restored_segments
    assert reloaded.get_timeline_version(
        "workspace_a", restored_version.id
    ).snapshot["subtitle_cues"] == initial_cues
    assert reloaded.get_creation_task(
        "workspace_a", "task-narration"
    ).narration_cache_key == initial_artifact.cache_key


def test_creation_task_job_publishes_traceable_artifact_and_recovers_after_restart(
    tmp_path,
):
    production = _repository(tmp_path)
    _save_creation_task(
        production, workspace_id="workspace_a", task_id="task-a"
    )
    version = production.create_timeline_version(
        TimelineVersion(
            id="timeline-v1",
            workspace_id="workspace_a",
            task_id="task-a",
            parent_version_id=None,
            sequence=1,
            snapshot={"tracks": [{"id": "video", "items": ["clip-1"]}]},
            created_by_operation_id="operation-1",
        )
    )
    artifact_store = ArtifactStore(tmp_path / "workspace-files")
    runner = VideoJobRunner(production)
    run = JobRun(
        id="job-render-v1",
        workspace_id="workspace_a",
        task_id="task-a",
        kind="render_preview",
        idempotency_key="render-preview:task-a:timeline-v1",
        input_digest=hashlib.sha256(version.id.encode()).hexdigest(),
    )

    def render(context):
        context.report_progress(50)
        published = artifact_store.publish_bytes(
            "artifacts/task-a/timeline-v1/preview.mp4", b"rendered preview"
        )
        record = production.create_artifact_record(
            ArtifactRecord(
                id="artifact-preview-v1",
                workspace_id=context.workspace_id,
                task_id="task-a",
                kind="preview",
                logical_ref=published.logical_ref,
                sha256=published.sha256,
                size_bytes=published.size_bytes,
                producer_job_id=context.job_id,
                input_refs=[f"timeline://task-a/{version.id}"],
            )
        )
        production.append_task_event(
            TaskEvent(
                id="event-preview-v1",
                workspace_id=context.workspace_id,
                task_id="task-a",
                job_id=context.job_id,
                event_type="artifact_published",
                payload={"artifact_id": record.id},
            )
        )
        return JobResult((record.logical_ref,))

    completed = runner.run(run, render)
    assert completed.status == "succeeded"
    assert completed.output_refs == ["artifacts/task-a/timeline-v1/preview.mp4"]

    pending_restart = production.create_job_run(
        JobRun(
            id="job-restart",
            workspace_id="workspace_a",
            task_id="task-a",
            kind="render_final",
            idempotency_key="render-final:task-a:timeline-v1",
            input_digest=hashlib.sha256(b"render-final-v1").hexdigest(),
        )
    )
    production.claim_job_run("workspace_a", pending_restart.id)

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    restarted_runner = VideoJobRunner(reloaded)
    assert restarted_runner.recover_after_restart("workspace_a") == 1
    assert reloaded.get_job_run("workspace_a", pending_restart.id).status == "interrupted"
    assert reloaded.get_creation_task(
        "workspace_a", "task-a"
    ).current_timeline_version_id == version.id
    assert reloaded.get_artifact_record(
        "workspace_a", "artifact-preview-v1"
    ).logical_ref == completed.output_refs[0]
    assert reloaded.get_task_event(
        "workspace_a", "event-preview-v1"
    ).payload == {"artifact_id": "artifact-preview-v1"}




def test_scene_ranges_split_long_shots_into_twelve_second_chunks(tmp_path):
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
        duration_reader=lambda _path: 31.0,
        scene_reader=lambda _path, _duration: [],
    )
    video = tmp_path / "long.mp4"
    video.write_bytes(b"test video placeholder")
    _confirm_library_copies(production, "workspace_a")
    asset = production.scan_library("workspace_a")[0]

    analyzed = production.analyze_asset("workspace_a", asset.id)

    assert [(item.start_seconds, item.end_seconds) for item in analyzed.segments] == [
        (0.0, 12.0),
        (12.0, 24.0),
        (24.0, 31.0),
    ]


def test_visual_semantics_are_cached_by_file_segment_and_model(tmp_path):
    provider = _FakeVisualProvider()
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
        duration_reader=lambda _path: 8.0,
        scene_reader=lambda _path, _duration: [4.0],
        visual_provider=provider,
    )
    video = tmp_path / "semantic.mp4"
    video.write_bytes(b"first version")
    _confirm_library_copies(production, "workspace_a")
    asset = production.scan_library("workspace_a")[0]

    first = production.analyze_asset("workspace_a", asset.id)
    second = production.analyze_asset("workspace_a", asset.id)

    assert len(provider.calls) == 1
    assert all(item.visual_status == "ready" for item in first.segments)
    assert all(item.visual_model_id == provider.model_id for item in first.segments)
    assert first.suggested_usage_role == "a_roll"
    assert first.usage_role == "unassigned"
    assert all(item.suggested_usage_role == "a_roll" for item in first.segments)
    assert "复用 2 个" in second.analysis_message
    assert production.get_visual_feature(
        "workspace_a",
        first.segments[0].id,
        provider.model_id,
    ) == [0.1, 0.2, 0.3]

    video.write_bytes(b"second version is different")
    _confirm_library_copies(production, "workspace_a")
    production.scan_library("workspace_a")
    production.analyze_asset("workspace_a", asset.id)

    assert len(provider.calls) == 2


def test_mixed_visual_suggestions_stay_segment_scoped_until_review(tmp_path):
    class MixedVisualProvider(_FakeVisualProvider):
        def embed_segments(self, video_path, segments):
            self.calls.append((str(video_path), [item.segment_id for item in segments]))
            return [
                VisualEmbedding(
                    segment_id=item.segment_id,
                    vector=[1.0, 0.0] if index == 0 else [0.0, 1.0],
                    sample_count=3,
                )
                for index, item in enumerate(segments)
            ]

        def embed_texts(self, texts):
            return [
                [1.0, 0.0] if index < 3 else [0.0, 1.0]
                for index, _text in enumerate(texts)
            ]

    provider = MixedVisualProvider()
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
        duration_reader=lambda _path: 8.0,
        scene_reader=lambda _path, _duration: [4.0],
        visual_provider=provider,
    )
    video = tmp_path / "mixed.mp4"
    video.write_bytes(b"mixed video")
    _confirm_library_copies(production, "workspace_a")
    asset = production.scan_library("workspace_a")[0]

    analyzed = production.analyze_asset("workspace_a", asset.id)

    assert analyzed.suggested_usage_role == "unassigned"
    assert [item.suggested_usage_role for item in analyzed.segments] == [
        "a_roll",
        "b_roll",
    ]
    production.update_segment(
        "workspace_a",
        asset.id,
        analyzed.segments[1].id,
        description="环境补充画面",
        transcript="",
        review_status="raw",
        usage_role="b_roll",
    )
    reviewed = production.update_asset_review(
        "workspace_a",
        asset.id,
        review_status="approved",
        usage_role="a_roll",
    )

    assert [item.usage_role for item in reviewed.segments] == ["a_roll", "b_roll"]
    assert production.readiness("workspace_a", local_diagnostic=True)["approved_segment_count"] == 1


def test_production_api_exposes_foundation_and_retires_batch_creation(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    client = TestClient(main_module.app)

    foundation = client.get("/api/production/foundation")
    assert foundation.status_code == 200
    assert foundation.json()["readiness"]["ready"] is False

    before_create = hashlib.sha256(production.database_path.read_bytes()).hexdigest()
    responses = [
        client.post(
            "/api/production/batches",
            json={"name": "API batch", "target_count": 10},
        ),
        client.post("/api/production/batches", json={}),
        client.post(
            "/api/production/batches",
            content=b"not-json",
            headers={"content-type": "application/json"},
        ),
    ]
    for response in responses:
        assert response.status_code == 410
        assert response.json()["detail"] == {
            "error": "legacy_creation_write_retired",
            "message": "旧 Episode、Batch 与草稿写入口已退役。",
            "impact": "本次请求没有写入历史 Episode、Batch 或剪辑草稿。",
            "repair_action": "请通过 CreationTask 接口继续当前创作任务。",
            "retryable": False,
        }
    assert hashlib.sha256(production.database_path.read_bytes()).hexdigest() == before_create
    assert production.list_batches(LEGACY_WORKSPACE_ID) == []

    listed = client.get("/api/production/batches")
    assert listed.status_code == 200
    assert listed.json()["total_count"] == 0


def test_production_foundation_reads_legacy_batches_without_recovery_writes(
    tmp_path, monkeypatch
):
    production = _repository(tmp_path)
    legacy_batch = ProductionBatch(
        id="batch_history",
        workspace_id=LEGACY_WORKSPACE_ID,
        name="历史批次",
        target_count=1,
        status="interrupted",
        message="只读历史记录",
    )
    monkeypatch.setattr(
        production,
        "list_batches",
        lambda workspace_id: [legacy_batch] if workspace_id == LEGACY_WORKSPACE_ID else [],
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))

    response = TestClient(main_module.app).get("/api/production/foundation")

    assert response.status_code == 200
    assert response.json()["recent_batches"][0]["id"] == "batch_history"
    assert response.json()["recent_batches"][0]["status"] == "interrupted"


def test_person_render_is_task_bound_versioned_and_restart_safe(tmp_path):
    production = _repository(tmp_path)
    _save_creation_task(production, workspace_id="workspace_a", task_id="task-person")
    members = LocalMemberService(production.database_path.with_name("members.sqlite3"))
    member, _token = members.whoami(None)
    person, voice, _avatar_auth, _voice_path, _image_path = register_synthetic_profiles(
        production, workspace_id="workspace_a", member_id=member.member_id,
        person_id="person-a", voice_id="voice-person", person_version="v1",
        voice_version="v1", language="zh", model_id="qwen3-tts",
    )
    plan = production.get_creation_plan("workspace_a", "plan-task-person")
    plan.selected_person = CreationPlanPersonReference(person.id, person.version)
    plan.selected_voice = CreationPlanVoiceReference(
        voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version,
        voice.authorization.id, voice.authorization.version, "zh", voice.model_id,
    )
    plan.owner_user_id = member.member_id
    task = production.get_creation_task("workspace_a", "task-person")
    task.owner_user_id = member.member_id
    with production._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(asdict(plan), default=str), "workspace_a", plan.id))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(asdict(task), default=str), "workspace_a", task.id))
    narration_path = tmp_path / "narration-source.wav"
    narration_path.write_bytes(synthetic_wav_bytes(6))
    narration_cache_key = "person-render-narration-v1"
    narration_reference = production.narration_artifact_reference(narration_cache_key)
    production.save_narration_artifact(NarrationArtifactRecord(
        id="narration-artifact-person", workspace_id="workspace_a", task_id="task-person",
        script_id="script-task-person", voice_profile_id=voice.id,
        cache_key=narration_cache_key, audio_path=str(narration_path), duration_seconds=5.5,
        sample_rate=24_000, model_id="qwen3-tts",
    ))
    script_id, sections = _seed_confirmed_script(
        production,
        workspace_id="workspace_a",
        task_id="task-person",
    )
    created_at = datetime.now(timezone.utc)
    segment = NarrationSegment(
        id="narration-segment-person",
        workspace_id="workspace_a",
        task_id="task-person",
        script_id=script_id,
        source_section_id=str(sections[0]["id"]),
        text=str(sections[0]["content"]),
        audio_reference=narration_reference,
        duration_seconds=5.5,
        sample_rate=24_000,
        model_id="qwen3-tts",
        voice_profile_id="voice-person",
        voice_version="v1",
        language="zh-CN",
        pronunciation_version="v1",
        status="ready",
        created_at=created_at,
        updated_at=created_at,
    )
    second_segment = replace(
        segment,
        id="narration-segment-person-2",
        source_section_id=str(sections[1]["id"]),
        text=str(sections[1]["content"]),
    )
    production.save_narration_segments(
        "workspace_a", "task-person", script_id, [segment, second_segment]
    )

    visual = production.video_library_path / "person.mp4"
    write_synthetic_video(visual, seconds=25)
    _confirm_library_copies(production, "workspace_a")
    asset = production.scan_library("workspace_a")[0]
    asset.owner_user_id = member.member_id
    production._save_asset(asset)
    visual_reference = production.workspace_media_reference_for_asset(
        "workspace_a", asset.id
    )
    with production._connect() as conn:
        production._claim_workspace_media_reference(
            conn, workspace_id="workspace_a", reference=visual_reference
        )
    pending = production.create_person_render(
        PersonRender(
            id="person-render-a",
            workspace_id="workspace_a",
            task_id="task-person",
            person_profile_id=person.id,
            person_version=person.version,
            voice_profile_id="voice-person",
            voice_version="v1",
            narration_segment_id=segment.id,
            visual_reference=visual_reference,
            model_id="musetalk-v1.5-mps",
            motion="natural",
            expression="neutral",
            cache_key="a" * 64,
            status="pending",
        )
    )
    producer_job = production.create_job_run(
        JobRun(
            id="job-person-render-a",
            workspace_id="workspace_a",
            task_id="task-person",
            kind="person_render",
            idempotency_key="person-render:task-person:a",
            input_digest="c" * 64,
        )
    )
    artifact = production.create_artifact_record(
        ArtifactRecord(
            id="artifact-person-render-a",
            workspace_id="workspace_a",
            task_id="task-person",
            kind="person_render",
            logical_ref="artifacts/task-person/person-render-a.mp4",
            sha256="d" * 64,
            size_bytes=13,
            producer_job_id=producer_job.id,
            input_refs=[visual_reference, segment.audio_reference],
        )
    )
    with pytest.raises(ValueError, match="ready"):
        production.bind_person_render_artifact(
            "workspace_a", "task-person", pending.id, artifact.logical_ref
        )
    assert production.claim_person_render(
        "workspace_a", "task-person", pending.id
    ).status == "running"
    output = tmp_path / "outputs" / "person-render.mp4"
    output.parent.mkdir()
    output.write_bytes(b"person-render")
    ready = production.finish_person_render(
        "workspace_a",
        "task-person",
        pending.id,
        status="ready",
        output_path=str(output),
        duration_seconds=5.5,
        alignment_evidence='{"engine":"musetalk-v1.5"}',
    )
    _save_creation_task(
        production,
        workspace_id="workspace_a",
        task_id="task-person-other",
    )
    foreign_job = production.create_job_run(
        JobRun(
            id="job-person-render-other",
            workspace_id="workspace_a",
            task_id="task-person-other",
            kind="person_render",
            idempotency_key="person-render:task-person-other:a",
            input_digest="e" * 64,
        )
    )
    foreign_artifact = production.create_artifact_record(
        ArtifactRecord(
            id="artifact-person-render-other",
            workspace_id="workspace_a",
            task_id="task-person-other",
            kind="person_render",
            logical_ref="artifacts/task-person-other/person-render.mp4",
            sha256="f" * 64,
            size_bytes=13,
            producer_job_id=foreign_job.id,
            input_refs=[visual_reference],
        )
    )
    with pytest.raises(ValueError, match="不属于当前工作区与创作任务"):
        production.bind_person_render_artifact(
            "workspace_a", "task-person", ready.id, foreign_artifact.logical_ref
        )
    wrong_kind_artifact = production.create_artifact_record(
        replace(
            artifact,
            id="artifact-person-render-wrong-kind",
            kind="preview",
            logical_ref="artifacts/task-person/preview.mp4",
        )
    )
    with pytest.raises(ValueError, match="类型必须为 person_render"):
        production.bind_person_render_artifact(
            "workspace_a", "task-person", ready.id, wrong_kind_artifact.logical_ref
        )
    ready = production.bind_person_render_artifact(
        "workspace_a", "task-person", ready.id, artifact.logical_ref
    )
    assert ready.artifact_ref == artifact.logical_ref
    assert (
        production.bind_person_render_artifact(
            "workspace_a", "task-person", ready.id, artifact.logical_ref
        )
        == ready
    )
    with pytest.raises(ValueError, match="已绑定其他"):
        production.bind_person_render_artifact(
            "workspace_a", "task-person", ready.id, foreign_artifact.logical_ref
        )
    with sqlite3.connect(production.database_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            conn.execute(
                "UPDATE person_renders SET person_profile_id = 'other' WHERE id = ?",
                (ready.id,),
            )
    with sqlite3.connect(production.database_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="artifact ref is immutable"):
            conn.execute(
                "UPDATE person_renders SET artifact_ref = 'artifacts/other.mp4' WHERE id = ?",
                (ready.id,),
            )
    restarted = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )
    assert restarted.list_person_renders("workspace_a", "task-person") == [ready]
    with pytest.raises(KeyError):
        restarted.get_person_render("workspace_b", "task-person", ready.id)
    interrupted = restarted.create_person_render(
        replace(
            pending,
            id="person-render-interrupted",
            narration_segment_id=second_segment.id,
            cache_key="b" * 64,
        )
    )
    restarted.claim_person_render("workspace_a", "task-person", interrupted.id)
    assert restarted.interrupt_running_person_renders("workspace_a") == 1
    recovered = restarted.get_person_render(
        "workspace_a", "task-person", interrupted.id
    )
    assert (recovered.status, recovered.failure_reason) == (
        "failed",
        "process_interrupted",
    )


def test_person_render_artifact_ref_migrates_existing_v5_rows(tmp_path, monkeypatch):
    database_path = tmp_path / "production-v5.sqlite3"
    migration_five = dict(_MIGRATIONS)[5]
    created_at = datetime.now(timezone.utc).isoformat()
    library_path = tmp_path / "library"
    library_path.mkdir()
    # Real Production databases create the base material tables before applying
    # numbered migrations. Preserve that order when constructing the v5 fixture.
    with monkeypatch.context() as patch:
        patch.setattr("app.production.apply_production_migrations", lambda _conn: None)
        ProductionRepository(database_path, library_path)
    with sqlite3.connect(database_path) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='material_ingestion_files'"
        ).fetchone() == ("material_ingestion_files",)
        conn.executescript(migration_five)
        conn.execute(
            """
            CREATE TABLE production_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            "INSERT INTO production_schema_migrations (version) VALUES (?)",
            [(version,) for version in range(1, 6)],
        )
        conn.execute(
            """
            INSERT INTO person_renders
                (id, workspace_id, task_id, person_profile_id, person_version,
                 voice_profile_id, voice_version, narration_segment_id,
                 visual_reference, model_id, motion, expression, cache_key,
                 status, output_path, duration_seconds, alignment_evidence,
                 failure_reason, impact, repair_action, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "person-render-v5",
                "workspace_a",
                "task-person",
                "person-a",
                "v1",
                "voice-person",
                "v1",
                "narration-person",
                "workspace://person.mp4",
                "musetalk-v1.5-mps",
                "natural",
                "neutral",
                "a" * 64,
                "ready",
                str(tmp_path / "person-render.mp4"),
                5.5,
                '{"engine":"musetalk-v1.5"}',
                None,
                None,
                None,
                created_at,
                created_at,
            ),
        )
        apply_production_migrations(conn)
        row = conn.execute(
            "SELECT id, artifact_ref, output_path FROM person_renders"
        ).fetchone()
        versions = conn.execute(
            "SELECT version FROM production_schema_migrations ORDER BY version"
        ).fetchall()

    assert row == ("person-render-v5", None, str(tmp_path / "person-render.mp4"))
    assert versions == [(version,) for version in range(1, PRODUCTION_SCHEMA_VERSION + 1)]


def test_recorded_narration_schema_migrates_v14_generated_rows_without_rewrite(tmp_path, monkeypatch):
    database_path = tmp_path / "narration-v14.sqlite3"
    created_at = datetime.now(timezone.utc).isoformat()
    library_path = tmp_path / "library"
    library_path.mkdir()
    monkeypatch.setattr("app.production.apply_production_migrations", lambda _conn: None)
    ProductionRepository(database_path, library_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute("CREATE TABLE production_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        for version, sql in _MIGRATIONS:
            if version == PRODUCTION_SCHEMA_VERSION:
                break
            if sql:
                conn.executescript(
                    "BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;"
                )
            conn.execute("INSERT INTO production_schema_migrations (version) VALUES (?)", (version,))
        conn.execute(
            """INSERT INTO narration_segments
               (id, workspace_id, task_id, script_id, source_section_id, text,
                audio_reference, duration_seconds, sample_rate, model_id,
                voice_profile_id, voice_version, language, pronunciation_version,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("narration-old", "local", "task-old", "script-old", "section-old",
             "既有文案", "narration://artifact/generated-old", 2.5, 24000,
             "qwen-existing", "voice-existing", "v3", "zh", "dict-v1",
             "ready", created_at, created_at),
        )
        apply_production_migrations(conn)
        row = conn.execute(
            "SELECT model_id, voice_profile_id, voice_version, source_kind, source_artifact_ref, source_sha256, source_script_digest FROM narration_segments WHERE id='narration-old'"
        ).fetchone()
        columns = {item[1]: item for item in conn.execute("PRAGMA table_info(narration_segments)")}
        trigger = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='narration_segments_stable_identity'"
        ).fetchone()
    assert row == ("qwen-existing", "voice-existing", "v3", "generated", None, None, None)
    assert all(columns[name][3] == 0 for name in ("model_id", "voice_profile_id", "voice_version"))
    assert trigger == (1,)


def test_creation_plan_preview_is_read_only_and_confirm_is_persistent_and_isolated(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(
        production, sources, workspace_id="workspace_a",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace_a"),
    )
    client = TestClient(main_module.app, headers=plan_owner[1])
    assert sources.get_knowledge_item("workspace_a", "knowledge-workspace_a").owner_user_id == plan_owner[0].member_id
    assert sources.get_knowledge_item_visibility("workspace_a", "knowledge-workspace_a") == "private"
    payload = {
        "goal": "为产品能力写一条竖屏观察视频。",
        "name": "能力观察",
        "target_count": 1,
        "language": "zh-CN",
        "aspect_ratio": "9:16",
        "duration_target_seconds": 60,
        "selected_voice": {"profile_id": "voice-workspace_a", "version": "v1"},
        "selected_person": {"profile_id": "voice-workspace_a-person", "version": "v1"},
    }
    before_hash = hashlib.sha256(production.database_path.read_bytes()).hexdigest()

    preview = client.post("/api/creation-plans/preview", json=payload)

    assert preview.status_code == 200
    assert hashlib.sha256(production.database_path.read_bytes()).hexdigest() == before_hash
    preview_body = preview.json()
    assert preview_body["confirmable"] is True
    assert preview_body["knowledge_references"] == [
        {
            "id": "knowledge-workspace_a",
            "version": "v1",
            "title": "用户知识",
            "authorization": "authorized",
            "ownership": "user_owned",
        }
    ]
    assert preview_body["source_references"][0]["id"] == "source-workspace_a"
    assert preview_body["material_matches"]
    assert preview_body["material_matches"][0]["asset_ref"].startswith("asset:")
    assert preview_body["material_matches"][0]["segment_ref"].startswith("segment:")
    assert preview_body["material_matches"][0]["reasons"]
    assert preview_body["selected_voice"]["profile_id"] == "voice-workspace_a"
    assert preview_body["selected_voice"]["person_profile_id"] == "voice-workspace_a-person"
    assert preview_body["selected_voice"]["person_version"] == "v1"
    assert preview_body["pronunciation_references"] == [
        {
            "id": "pronunciation-workspace_a",
            "version": "v1",
            "scope": "person",
            "scope_reference": "voice-workspace_a-person",
            "language": "zh",
        }
    ]
    assert "reference_text" not in preview_body["selected_voice"]
    assert "reference_audio_path" not in preview_body["selected_voice"]
    assert any(
        blocker["reason"] == "voice_model_unavailable"
        and blocker["blocks_confirmation"] is False
        for blocker in preview_body["blockers"]
    )
    assert (
        client.post(
            "/api/creation-plans/preview",
            json={**payload, "narration_audio_path": "/tmp/untrusted.wav"},
        ).status_code
        == 422
    )

    confirmed = client.post(
        "/api/creation-plans/confirm",
        json={**payload, "preflight_fingerprint": preview_body["preflight_fingerprint"]},
    )

    assert confirmed.status_code == 201
    confirmed_body = confirmed.json()
    assert confirmed_body["status"] == "confirmed"
    assert confirmed_body["creation_task"]["plan_id"] == confirmed_body["id"]
    assert confirmed_body["creation_task"]["stage"] == "topic"
    assert confirmed_body["creation_task"]["stage_status"] == "not_started"
    with production._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM production_batches").fetchone()[0] == 0
    reloaded = client.get(f"/api/creation-plans/{confirmed_body['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json() == confirmed_body
    structured_materials = client.get(
        "/api/production/materials/search",
        params={"usage_role": "b_roll", "min_duration_seconds": 1},
    )
    natural_materials = client.get(
        "/api/production/materials/search",
        params={"q": "找一些补充画面"},
    )
    assert structured_materials.status_code == 200
    assert natural_materials.status_code == 200
    assert natural_materials.json()[0]["segment_ref"] in {
        item["segment_ref"] for item in structured_materials.json()
    }
    saved_plan = production.get_creation_plan("workspace_a", confirmed_body["id"])
    saved_task = production.get_creation_task(
        "workspace_a", confirmed_body["creation_task"]["id"]
    )
    assert saved_plan.owner_user_id == saved_task.owner_user_id == plan_owner[0].member_id
    # A valid different member in this same test workspace still cannot use
    # the uploader's private knowledge, including through content preflight.
    _, other_token = main_module.local_members_service.join(
        main_module.local_members_service.create_invite_code(), "另一成员",
    )
    other_headers = {"X-Local-Member-Token": other_token}
    other_preview = client.post("/api/creation-plans/preview", json=payload, headers=other_headers)
    assert other_preview.status_code == 200
    assert other_preview.json()["knowledge_references"] == []
    assert client.get(
        "/api/knowledge-items/knowledge-workspace_a", headers=other_headers,
    ).status_code == 404
    rejected_plan = replace(
        saved_plan,
        id="plan_transaction_rollback",
        creation_task_id=None,
    )
    rejected_task = replace(saved_task, plan_id=rejected_plan.id)
    with pytest.raises(sqlite3.IntegrityError):
        production.confirm_creation_plan(rejected_plan, rejected_task)
    with pytest.raises(KeyError):
        production.get_creation_plan("workspace_a", rejected_plan.id)
    assert (
        client.get(
            f"/api/creation-plans/{confirmed_body['id']}",
            headers={"x-test-workspace": "workspace_b"},
        ).status_code
        == 404
    )


def test_creation_plan_preflight_reports_missing_authorized_voice_wav(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    voice_path = _seed_creation_plan_facts(
        production, sources, workspace_id="local",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    voice_path.unlink()
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(main_module, "current_workspace_id", lambda request: "local")

    response = TestClient(main_module.app, headers=plan_owner[1]).post(
        "/api/creation-plans/preview",
        json={
            "goal": "制作一条中文口播视频",
            "selected_voice": {"profile_id": "voice-local", "version": "v1"},
            "selected_person": {"profile_id": "voice-local-person", "version": "v1"},
        },
    )

    assert response.status_code == 200, response.text
    assert any(
        blocker["reason"] == "参考文件失联或为空，请重新定位原参考"
        and blocker["stage"] == "narration"
        and blocker["blocks_confirmation"] is False
        for blocker in response.json()["blockers"]
    ), response.text


def test_creation_task_list_is_workspace_scoped_ordered_and_read_only(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace_a"),
    )
    client = TestClient(main_module.app, headers=plan_owner[1])
    before_empty_list = hashlib.sha256(production.database_path.read_bytes()).hexdigest()
    empty_list = client.get("/api/creation-tasks")
    assert empty_list.status_code == 200
    assert empty_list.json() == {"items": [], "total_count": 0}
    assert hashlib.sha256(production.database_path.read_bytes()).hexdigest() == before_empty_list

    _seed_creation_plan_facts(
        production, sources, workspace_id="workspace_a",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    connector_b = SourceConnector(
        id="connector-workspace_b",
        workspace_id="workspace_b",
        platform="manual",
        protocol="manual",
        account_or_rule="operator",
        frequency="on_demand",
        permissions=("read",),
    )
    sources.upsert_connector(connector_b)
    source_b = SourceItem(
        id="source-workspace_b",
        workspace_id="workspace_b",
        connector_id=connector_b.id,
        source_label="manual",
        source_url="https://example.com/workspace-b",
        content_type="article",
        author="operator",
        published_at=None,
        body="工作区 B 的真实信息源正文。",
        subtitles=None,
        media_references=(),
        license="authorized",
        title="工作区 B 信息源条目",
    )
    sources.save_source_items("workspace_b", [source_b])

    def confirm(goal: str, workspace_id: str) -> dict[str, object]:
        payload = {"goal": goal, "name": goal}
        headers = {"x-test-workspace": workspace_id}
        preview = client.post("/api/creation-plans/preview", json=payload, headers=headers)
        assert preview.status_code == 200
        created = client.post(
            "/api/creation-plans/confirm",
            json={
                **payload,
                "preflight_fingerprint": preview.json()["preflight_fingerprint"],
            },
            headers=headers,
        )
        assert created.status_code == 201
        return created.json()

    first_a = confirm("任务 A-1", "workspace_a")
    second_a = confirm("任务 A-2", "workspace_a")
    only_b = confirm("任务 B-1", "workspace_b")
    before_list = hashlib.sha256(production.database_path.read_bytes()).hexdigest()

    list_a = client.get("/api/creation-tasks", headers={"x-test-workspace": "workspace_a"})
    list_b = client.get("/api/creation-tasks", headers={"x-test-workspace": "workspace_b"})

    assert list_a.status_code == 200
    assert [item["id"] for item in list_a.json()["items"]] == [
        second_a["creation_task"]["id"],
        first_a["creation_task"]["id"],
    ]
    assert list_a.json()["total_count"] == 2
    assert not {"batch_id", "episode_id"} & list_a.json()["items"][0].keys()
    assert list_b.status_code == 200
    assert [item["id"] for item in list_b.json()["items"]] == [
        only_b["creation_task"]["id"]
    ]
    assert list_b.json()["total_count"] == 1
    assert hashlib.sha256(production.database_path.read_bytes()).hexdigest() == before_list


def _configure_workspace_text_service(
    workspace_id: str,
    *,
    provider: str,
    base_url: str,
    model: str,
    api_key: str,
) -> None:
    """给该工作区一份真实的文字服务配置。

    文字服务配置按工作区隔离，patch 全局 Settings 不再决定这个工作区配置得好不好，
    所以这里真的写工作区快照与该工作区的钥匙串条目。
    """
    from app import workspace_settings as workspace_settings_module

    workspace_settings_module.save_workspace_config(
        workspace_id,
        {"llm_provider": provider, "llm_base_url": base_url, "llm_model": model,
         "llm_api_key_provider": provider},
    )
    if api_key:
        workspace_settings_module.save_workspace_secret(workspace_id, "llm_api_key", api_key)


def test_creation_task_topics_persist_confirm_and_fail_closed_by_workspace(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(
        production, sources, workspace_id="workspace_a",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(main_module, "settings", Settings())
    _configure_workspace_text_service(
        "workspace_a",
        provider="custom",
        base_url="https://model.example/v1",
        model="topic-test",
        api_key="test-key",
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace_a"),
    )
    calls = []

    class FakeGenerator:
        def generate(self, input):
            calls.append(input)
            return [
                CreationTaskGeneratedTopic(
                    title="第一条真实候选",
                    angle="从已授权资料提炼的角度",
                    audience="内容运营者",
                    outline=("事实", "判断", "行动"),
                    score=91,
                    evidence_reference_ids=(input.sources[0].reference_id,),
                ),
                CreationTaskGeneratedTopic(
                    title="第二条真实候选",
                    angle="不同的可执行角度",
                    audience="内容运营者",
                    outline=("问题", "取舍", "行动"),
                    score=86,
                    evidence_reference_ids=(input.sources[1].reference_id,),
                ),
            ]

    monkeypatch.setattr(
        main_module,
        "_creation_task_topic_generator_for",
        lambda _settings: FakeGenerator(),
    )
    client = TestClient(main_module.app, headers=plan_owner[1])
    payload = {"goal": "用已授权内容生成一条竖屏选题", "name": "选题任务"}
    preview = client.post("/api/creation-plans/preview", json=payload)
    assert preview.status_code == 200
    created = client.post(
        "/api/creation-plans/confirm",
        json={**payload, "preflight_fingerprint": preview.json()["preflight_fingerprint"]},
    )
    assert created.status_code == 201
    task_id = created.json()["creation_task"]["id"]

    before_generation = client.get(f"/api/creation-tasks/{task_id}/topics")
    assert before_generation.status_code == 200
    assert before_generation.json()["route_preflight"]["availability"] == "unverified"
    assert before_generation.json()["egress_preflight"]["cloud_egress"] is True
    assert before_generation.json()["egress_preflight"]["source_count"] == 2
    assert client.post(
        f"/api/creation-tasks/{task_id}/topics/generate",
        json={"model": "client-must-not-select"},
    ).status_code == 422

    generated = client.post(
        f"/api/creation-tasks/{task_id}/topics/generate", json={}
    )
    assert generated.status_code == 200
    generated_body = generated.json()
    assert len(calls) == 1
    assert generated_body["task"]["stage_status"] == "waiting_for_user"
    assert generated_body["latest_run"]["status"] == "succeeded"
    assert generated_body["latest_run"]["route_snapshot"]["availability"] == "succeeded"
    assert generated_body["route_preflight"]["availability"] == "succeeded"
    assert generated_body["latest_run"]["route_snapshot"]["endpoint_identity"] == "https://model.example"
    assert {item["title"] for item in generated_body["candidates"]} == {
        "第一条真实候选",
        "第二条真实候选",
    }
    assert generated_body["evidence_references"]
    assert "text" not in generated_body["evidence_references"][0]
    source_evidence = next(
        item
        for item in generated_body["evidence_references"]
        if item["kind"] == "source"
    )
    confirmed_plan = production.get_creation_plan("workspace_a", created.json()["id"])
    assert source_evidence["version_or_identity"] == confirmed_plan.source_references[0].identity
    assert source_evidence["version_or_identity"] != source_evidence["reference_id"]
    source_input = next(
        item
        for item in generated_body["latest_run"]["input_references"]
        if item["kind"] == "source"
    )
    assert source_input["version_or_identity"] == confirmed_plan.source_references[0].identity
    assert source_input["version_or_identity"] != source_input["object_id"]
    assert client.post(
        f"/api/creation-tasks/{task_id}/topics/generate", json={}
    ).status_code == 409

    first_topic_id = generated_body["candidates"][0]["id"]
    second_topic_id = generated_body["candidates"][1]["id"]
    for invalid_title in ("  ", "字" * 81):
        with pytest.raises(ValueError, match="1–80"):
            production.confirm_topic_candidate("workspace_a", task_id, first_topic_id, title=invalid_title)
    assert production.get_creation_task("workspace_a", task_id).selected_topic_id is None
    confirmed = client.post(
        f"/api/creation-tasks/{task_id}/topics/{first_topic_id}/confirm", json={"title": "  用户编辑的真实标题  "}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["task"]["stage"] == "script"
    assert confirmed.json()["task"]["stage_status"] == "not_started"
    assert confirmed.json()["selected_topic_id"] == first_topic_id
    assert next(item for item in confirmed.json()["candidates"] if item["id"] == first_topic_id)["title"] == "用户编辑的真实标题"
    assert client.post(f"/api/creation-tasks/{task_id}/topics/{first_topic_id}/confirm", json={"title":"用户编辑的真实标题"}).status_code == 200
    assert client.post(f"/api/creation-tasks/{task_id}/topics/{first_topic_id}/confirm", json={"title":"重复确认不得改写"}).status_code == 409
    assert client.post(
        f"/api/creation-tasks/{task_id}/topics/{first_topic_id}/confirm", json={}
    ).status_code == 200
    conflict = client.post(
        f"/api/creation-tasks/{task_id}/topics/{second_topic_id}/confirm", json={}
    )
    assert conflict.status_code == 409
    assert (
        client.get(
            f"/api/creation-tasks/{task_id}/topics",
            headers={"x-test-workspace": "workspace_b"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/creation-tasks/{task_id}/topics/{first_topic_id}/confirm",
            json={},
            headers={"x-test-workspace": "workspace_b"},
        ).status_code
        == 404
    )

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    saved_task = reloaded.get_creation_task("workspace_a", task_id)
    assert saved_task.selected_topic_id == first_topic_id
    assert saved_task.stage == "script"
    assert next(item for item in reloaded.list_topic_candidates("workspace_a", task_id) if item.id == first_topic_id).title == "用户编辑的真实标题"
    assert saved_task.stage_status == "not_started"
    assert len(reloaded.list_topic_candidates("workspace_a", task_id)) == 2
    reloaded_evidence = reloaded.list_topic_evidence_references("workspace_a", task_id)
    assert len(reloaded_evidence) == 2
    assert next(
        item for item in reloaded_evidence if item.kind == "source"
    ).version_or_identity == confirmed_plan.source_references[0].identity
    reloaded_run = reloaded.latest_topic_generation_run("workspace_a", task_id)
    assert reloaded_run is not None
    assert next(
        item for item in reloaded_run.input_references if item["kind"] == "source"
    )["version_or_identity"] == confirmed_plan.source_references[0].identity

    _configure_workspace_text_service(
        "workspace_a",
        provider="custom",
        base_url="https://model.example/v1",
        model="topic-test-v2",
        api_key="test-key",
    )
    changed_route = client.get(f"/api/creation-tasks/{task_id}/topics")
    assert changed_route.status_code == 200
    assert changed_route.json()["route_preflight"]["availability"] == "unverified"


def test_creation_task_script_generation_confirmation_and_restart_persistence(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(
        production, sources, workspace_id="workspace_a",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(main_module, "settings", Settings())
    _configure_workspace_text_service(
        "workspace_a",
        provider="custom",
        base_url="https://model.example/v1",
        model="deepseek-test",
        api_key="test-key",
    )
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace_a")

    class FakeTopicGenerator:
        def generate(self, input):
            return [
                CreationTaskGeneratedTopic(
                    title="一个真实选题",
                    angle="从授权资料提炼",
                    audience="内容运营者",
                    outline=("事实", "判断", "行动"),
                    score=91,
                    evidence_reference_ids=tuple(
                        source.reference_id for source in input.sources
                    ),
                )
            ]

    script_calls = []

    class FakeScriptGenerator:
        def generate(self, input):
            script_calls.append(input)
            return CreationTaskGeneratedScript(
                title="已生成的真实文案",
                language="zh-CN",
                sections=(
                    CreationTaskGeneratedScriptSection(
                        title="开场",
                        content="先看事实，再给判断，最后说明下一步行动。",
                        visual_prompt="产品操作界面和人物工作场景",
                        evidence_reference_ids=tuple(
                            source.reference_id for source in input.sources
                        ),
                    ),
                ),
            )

    monkeypatch.setattr(
        main_module,
        "_creation_task_topic_generator_for",
        lambda _settings: FakeTopicGenerator(),
    )
    monkeypatch.setattr(
        main_module,
        "_creation_task_script_generator_for",
        lambda _settings: FakeScriptGenerator(),
    )
    client = TestClient(main_module.app, headers=plan_owner[1])
    payload = {"goal": "基于授权内容生成中文视频", "name": "本地真成片"}
    preview = client.post("/api/creation-plans/preview", json=payload).json()
    created = client.post(
        "/api/creation-plans/confirm",
        json={**payload, "preflight_fingerprint": preview["preflight_fingerprint"]},
    ).json()
    task_id = created["creation_task"]["id"]
    topics = client.post(f"/api/creation-tasks/{task_id}/topics/generate", json={})
    assert topics.status_code == 200
    topic_id = topics.json()["candidates"][0]["id"]
    assert client.post(
        f"/api/creation-tasks/{task_id}/topics/{topic_id}/confirm", json={}
    ).status_code == 200

    successful_script_generator = main_module._creation_task_script_generator_for

    class FailingScriptGenerator:
        def generate(self, _input):
            from app.topics import CreationTaskScriptGenerationError
            raise CreationTaskScriptGenerationError(
                code="http_failed", reason="文字模型服务拒绝了文案请求。",
                impact="未生成文案。", repair_action="检查服务状态后重试。", retryable=True,
            )

    monkeypatch.setattr(main_module, "_creation_task_script_generator_for",
                        lambda _settings: FailingScriptGenerator())
    failed = client.post(f"/api/creation-tasks/{task_id}/scripts/generate", json={})
    assert failed.status_code == 502
    runtime = client.get("/api/settings/runtime").json()
    rows = {row["name"]: row["value"] for row in runtime["settings"]}
    assert rows["LLM_SERVICE_STATE"] == "失败"
    assert rows["LLM_SERVICE_CAUSE"] == "http_failed"
    assert rows["LLM_SERVICE_DETAIL"] == "文字模型服务拒绝了文案请求。"
    assert rows["LLM_SERVICE_REPAIR_ACTION"] == "检查服务状态后重试。"
    assert "test-key" not in json.dumps(runtime)
    monkeypatch.setattr(main_module, "_creation_task_script_generator_for",
                        successful_script_generator)

    generated = client.post(
        f"/api/creation-tasks/{task_id}/scripts/generate", json={}
    )
    assert generated.status_code == 200
    recovered_rows = {row["name"]: row["value"] for row in client.get("/api/settings/runtime").json()["settings"]}
    assert recovered_rows["LLM_SERVICE_STATE"] == "可用"
    assert recovered_rows["LLM_SERVICE_REPAIR_ACTION"] == ""
    body = generated.json()
    assert len(script_calls) == 1
    assert body["task"]["stage"] == "script"
    assert body["task"]["stage_status"] == "waiting_for_user"
    assert body["scripts"][0]["selection_status"] == "draft"
    script = body["scripts"][0]
    edited_sections = [
        {**section, "content": f"{section['content']} 这句话由用户确认。"}
        for section in script["sections"]
    ]
    confirmed = client.post(
        f"/api/creation-tasks/{task_id}/scripts/{script['id']}/confirm",
        json={"title": "用户确认后的文案", "sections": edited_sections,
              "expected_updated_at": script["updated_at"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["task"]["stage"] == "narration"
    assert confirmed.json()["task"]["stage_status"] == "not_started"
    assert confirmed.json()["scripts"][0]["selection_status"] == "confirmed"
    workspace = client.get(f"/api/creation-tasks/{task_id}")
    assert workspace.status_code == 200
    assert workspace.json()["scripts"][0]["title"] == "用户确认后的文案"

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    saved_task = reloaded.get_creation_task("workspace_a", task_id)
    saved_script = reloaded.get_creation_task_script(
        "workspace_a", saved_task.confirmed_script_id
    )
    assert saved_task.stage == "narration"
    assert saved_script.sections[0].content.endswith("这句话由用户确认。")

    reloaded.start_creation_task_narration("workspace_a", task_id)
    reloaded.fail_creation_task_stage(
        "workspace_a",
        task_id,
        stage="narration",
        reason="narration_generation_failed",
        impact="未生成配音。",
        repair_action="文案预计时长超过创作方案上限，请先压缩并重新确认文案",
        retryable=True,
    )
    failed_workspace = client.get(f"/api/creation-tasks/{task_id}").json()
    assert failed_workspace["task"]["can_revise_script"] is True
    failed_task = reloaded.get_creation_task("workspace_a", task_id)
    for protected in (
        replace(failed_task, narration_cache_key="completed-audio"),
        replace(failed_task, current_timeline_version_id="existing-timeline"),
        replace(failed_task, montage_plan_id="existing-montage"),
        replace(failed_task, stage_status="running"),
        replace(failed_task, stage_reason="voice_authorization_missing"),
    ):
        assert protected.can_revise_script is False
    too_long = client.post(
        f"/api/creation-tasks/{task_id}/scripts/{saved_script.id}/confirm",
        json={"title": "仍然过长", "sections": [
            {**asdict(section), "content": "字" * 2000} for section in saved_script.sections
        ]},
    )
    assert too_long.status_code == 422
    assert reloaded.get_creation_task("workspace_a", task_id).stage_status == "failed"
    assert reloaded.get_creation_task_script("workspace_a", saved_script.id).title == saved_script.title
    direct_repair = client.post(
        f"/api/creation-tasks/{task_id}/scripts/{saved_script.id}/confirm",
        json={"title": "直接修订", "sections": [
            {**asdict(section), "content": "直接压缩正文，无需再次调用文字模型。"} for section in saved_script.sections
        ], "expected_updated_at": saved_script.updated_at.isoformat()},
    )
    assert direct_repair.status_code == 200, direct_repair.text
    assert direct_repair.json()["task"]["stage_status"] == "not_started"
    assert reloaded.get_creation_task_script("workspace_a", saved_script.id).title == "直接修订"
    saved_script = reloaded.get_creation_task_script("workspace_a", saved_script.id)
    assert len(script_calls) == 1
    assert client.get(f"/api/creation-tasks/{task_id}").json()["task"]["can_revise_script"] is False
    reloaded.start_creation_task_narration("workspace_a", task_id)
    reloaded.fail_creation_task_stage(
        "workspace_a", task_id, stage="narration", reason="narration_generation_failed",
        impact="配音超时。", repair_action="请压缩并重新确认文案", retryable=True,
    )
    repair_run = ScriptGenerationRun(
        id="script-run-repair",
        workspace_id="workspace_a",
        task_id=task_id,
        plan_id=saved_task.plan_id,
        topic_id=saved_task.selected_topic_id,
        status="running",
        route_snapshot={},
        prompt_schema_version="creation-task-script/v1",
        input_references=[],
    )
    reloaded.start_script_generation(repair_run)
    reloaded.fail_script_generation(
        repair_run.id,
        reason="文字模型服务拒绝了文案请求。",
        impact="未生成压缩后的文案。",
        repair_action="检查模型服务后重试，或手动修订并重新确认文案。",
        retryable=False,
        provider_attempted=True,
    )
    repaired_task, repaired_script = reloaded.confirm_creation_task_script(
        "workspace_a",
        task_id,
        saved_script.id,
        title="用户压缩后的文案",
        sections=[
            replace(
                section,
                content="用户在文案失败状态中压缩正文，并重新确认。",
            )
            for section in saved_script.sections
        ],
        expected_updated_at=saved_script.updated_at,
    )
    assert repaired_task.stage == "narration"
    assert repaired_task.stage_status == "not_started"
    assert repaired_task.stage_reason is None
    assert repaired_script.title == "用户压缩后的文案"
    assert repaired_script.sections[0].content == "用户在文案失败状态中压缩正文，并重新确认。"
    assert [
        item.id
        for item in reloaded.list_creation_task_scripts("workspace_a", task_id)
        if item.selection_status == "confirmed"
    ] == [repaired_script.id]
    with reloaded._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM production_batches").fetchone()[0] == 0


def test_creation_task_can_bind_a_later_authorized_voice_without_redoing_script(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    workspace_id = "workspace_a"
    plan = CreationPlan(
        id="plan-recovery",
        workspace_id=workspace_id,
        goal="继续已经确认文案的中文口播任务",
        name="配音恢复任务",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=60,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=0,
        approved_clip_count=0,
        material_rule_summary="本测试不涉及镜头选择",
        model_snapshot=[],
        confirmation_points=["确认文案", "确认声音"],
        blockers=[
            CreationPlanBlocker(
                reason="voice_authorization_missing",
                impact="当前不能配音。",
                repair_action="登记授权声音。",
                stage="narration",
                blocks_confirmation=False,
            )
        ],
        preflight_fingerprint="f" * 64,
    )
    task = CreationTask(
        id="task-recovery",
        workspace_id=workspace_id,
        plan_id=plan.id,
        name=plan.name,
        scenario=plan.goal,
        stage="narration",
        stage_status="failed",
        status="failed",
        progress=40,
        recovery_point="narration",
        confirmed_script_id="script-confirmed",
        stage_reason="voice_identity_missing",
        stage_impact="未生成配音。",
        stage_repair_action="登记授权声音。",
        stage_retryable=True,
        owner_user_id=plan_owner[0].member_id,
    )
    plan.owner_user_id = plan_owner[0].member_id
    plan, task = production.confirm_creation_plan(
        plan, task, verified_owner_user_id=plan_owner[0].member_id
    )
    voice = _save_versioned_voice(
        production,
        workspace_id=workspace_id,
        voice_id="voice-recovery",
        version="v1",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    plan.selected_person = CreationPlanPersonReference(voice.person_profile_id, voice.person_version)
    with production._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE workspace_id=? AND id=?",
            (json.dumps(asdict(plan), default=str), workspace_id, plan.id))
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)
    monkeypatch.setattr(
        main_module,
        "model_catalog",
        SimpleNamespace(
            list_statuses=lambda: [
                SimpleNamespace(
                    model=SimpleNamespace(id=voice.model_id),
                    ready=True,
                )
            ]
        ),
    )

    client = TestClient(main_module.app, headers=plan_owner[1])
    command = {"voice_profile_id": voice.id, "voice_version": voice.version,
        "request_id": "00000000-0000-4000-8000-000000000001",
        "expected_plan_updated_at": plan.updated_at.isoformat(), "expected_task_updated_at": task.updated_at.isoformat()}
    response = client.post(
        f"/api/creation-tasks/{task.id}/narration/voice",
        json=command,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert client.get(f"/api/creation-tasks/{task.id}/narration/voice/requests/{command['request_id']}").json() == body
    assert client.post(f"/api/creation-tasks/{task.id}/narration/voice", json=command).json() == body
    assert client.post(f"/api/creation-tasks/{task.id}/narration/voice",
        json={"voice_profile_id": voice.id, "voice_version": voice.version}).status_code == 422
    assert body["selected_voice"]["profile_id"] == voice.id
    assert body["status"] == "selected" and body["confirmed_script_id"] == "script-confirmed"
    current_task = production.get_creation_task(workspace_id, task.id)
    assert current_task.stage == "narration" and current_task.stage_status == "not_started"
    assert current_task.stage_reason is None
    assert not any(
        blocker.stage == "narration" for blocker in production.get_creation_plan(workspace_id, plan.id).blockers
    )
    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_creation_task(workspace_id, task.id).confirmed_script_id == "script-confirmed"
    assert reloaded.get_creation_plan(workspace_id, plan.id).selected_voice.profile_id == voice.id


def test_creation_task_can_regenerate_script_after_duration_preflight_failure(
    tmp_path,
):
    production = _repository(tmp_path)
    workspace_id = "workspace_a"
    plan = CreationPlan(
        id="plan-duration-repair",
        workspace_id=workspace_id,
        goal="制作一条三十秒中文口播",
        name="文案压缩恢复",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=0,
        approved_clip_count=0,
        material_rule_summary="本测试不涉及镜头选择",
        model_snapshot=[],
        confirmation_points=["确认文案"],
        blockers=[],
        preflight_fingerprint="f" * 64,
    )
    task = CreationTask(
        id="task-duration-repair",
        workspace_id=workspace_id,
        plan_id=plan.id,
        name=plan.name,
        scenario=plan.goal,
        stage="narration",
        stage_status="failed",
        status="failed",
        progress=40,
        recovery_point="narration",
        selected_topic_id="topic-confirmed",
        confirmed_script_id="script-too-long",
        stage_reason="narration_generation_failed",
        stage_impact="未生成配音。",
        stage_repair_action="拼合后的配音超过创作方案上限，请先压缩文案",
        stage_retryable=True,
    )
    production.confirm_creation_plan(plan, task)
    run = ScriptGenerationRun(
        id="script-run-duration-repair",
        workspace_id=workspace_id,
        task_id=task.id,
        plan_id=plan.id,
        topic_id="topic-confirmed",
        status="running",
        route_snapshot={},
        prompt_schema_version="creation-task-script/v1",
        input_references=[],
    )

    resumed, _ = production.start_script_generation(run)

    assert resumed.stage == "script"
    assert resumed.stage_status == "running"
    assert resumed.status == "running"
    assert resumed.confirmed_script_id == "script-too-long"
    assert resumed.stage_reason is None


def test_creation_task_topic_failure_records_capability_license_and_provider_errors(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(
        production, sources, workspace_id="workspace_a",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace_a"),
    )
    client = TestClient(main_module.app, headers=plan_owner[1])

    def create_task(name: str) -> str:
        payload = {"goal": name, "name": name}
        preview = client.post("/api/creation-plans/preview", json=payload)
        assert preview.status_code == 200
        confirmed = client.post(
            "/api/creation-plans/confirm",
            json={**payload, "preflight_fingerprint": preview.json()["preflight_fingerprint"]},
        )
        assert confirmed.status_code == 201
        return confirmed.json()["creation_task"]["id"]

    monkeypatch.setattr(main_module, "settings", Settings())
    unavailable_task = create_task("未配置文字模型")
    unavailable = client.post(
        f"/api/creation-tasks/{unavailable_task}/topics/generate", json={}
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["error"] == "capability_unavailable"
    unavailable_state = client.get(f"/api/creation-tasks/{unavailable_task}/topics")
    assert unavailable_state.json()["task"]["stage_status"] == "failed"
    assert unavailable_state.json()["latest_run"]["status"] == "failed"
    assert unavailable_state.json()["latest_run"]["route_snapshot"]["availability"] == "unconfigured"
    assert unavailable_state.json()["candidates"] == []

    source = sources.get_source_item("workspace_a", "source-workspace_a")
    sources.save_source_items(
        "workspace_a",
        [replace(source, license="unknown")],
    )
    license_task = create_task("许可未知")
    local_calls = []

    class LocalGenerator:
        def generate(self, input):
            local_calls.append(input)
            return [
                CreationTaskGeneratedTopic(
                    title="本地许可未知也可处理",
                    angle="不经云端发送",
                    audience="内容运营者",
                    outline=("本地处理",),
                    score=75,
                    evidence_reference_ids=(input.sources[1].reference_id,),
                )
            ]

    _configure_workspace_text_service(
        "workspace_a",
        provider="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="local-topic-test",
        api_key="",
    )
    monkeypatch.setattr(
        main_module,
        "_creation_task_topic_generator_for",
        lambda _settings: LocalGenerator(),
    )
    task = production.get_creation_task("workspace_a", license_task)
    plan = production.get_creation_plan("workspace_a", task.plan_id)
    prepared, references, _ = main_module._creation_task_topic_inputs(
        "workspace_a", task, plan, cloud_egress=True,
    )
    assert any(item.reference_id == "source:source-workspace_a" for item in prepared)
    assert any(item["object_id"] == "source-workspace_a" for item in references)
    local_allowed = client.post(
        f"/api/creation-tasks/{license_task}/topics/generate", json={}
    )
    assert local_allowed.status_code == 200
    assert len(local_calls) == 1
    assert local_allowed.json()["latest_run"]["route_snapshot"]["execution_location"] == "local"

    sources.save_source_items(
        "workspace_a",
        [replace(source, license="authorized")],
    )
    stale_task = create_task("来源身份变化")
    sources.save_source_items(
        "workspace_a",
        [replace(source, body="同一条目已被覆盖的新正文。", license="authorized")],
    )
    stale = client.post(f"/api/creation-tasks/{stale_task}/topics/generate", json={})
    assert stale.status_code == 503
    assert stale.json()["detail"]["reason"] == "source_reference_stale"
    sources.save_source_items("workspace_a", [source])
    provider_task = create_task("模型失败")

    class FailingGenerator:
        def generate(self, _input):
            raise CreationTaskTopicGenerationError(
                code="transport_failed",
                reason="模型连接失败。",
                impact="未生成选题候选。",
                repair_action="检查连接后重试。",
                retryable=True,
            )

    monkeypatch.setattr(
        main_module,
        "_creation_task_topic_generator_for",
        lambda _settings: FailingGenerator(),
    )
    record_llm_service_verification(main_module.settings, ok=True, cause="verified")
    failed = client.post(
        f"/api/creation-tasks/{provider_task}/topics/generate", json={}
    )
    assert failed.status_code == 502
    rows = {row["name"]: row["value"] for row in client.get("/api/settings/runtime").json()["settings"]}
    assert rows["LLM_SERVICE_STATE"] == "失败"
    assert rows["LLM_SERVICE_CAUSE"] == "transport_failed"
    assert rows["LLM_SERVICE_DETAIL"] == "模型连接失败。"
    assert rows["LLM_SERVICE_REPAIR_ACTION"] == "检查连接后重试。"
    failed_state = client.get(f"/api/creation-tasks/{provider_task}/topics")
    assert failed_state.json()["latest_run"]["status"] == "failed"
    assert failed_state.json()["latest_run"]["reason"] == "模型连接失败。"
    assert failed_state.json()["latest_run"]["route_snapshot"]["availability"] == "failed"
    assert failed_state.json()["route_preflight"]["availability"] == "failed"

    invalid_evidence_task = create_task("非法证据引用")

    class InvalidEvidenceGenerator:
        def generate(self, _input):
            return [
                CreationTaskGeneratedTopic(
                    title="不应保存的候选",
                    angle="不应保存",
                    audience="不应保存",
                    outline=("不应保存",),
                    score=80,
                    evidence_reference_ids=("knowledge:not-supplied:v1",),
                )
            ]

    monkeypatch.setattr(
        main_module,
        "_creation_task_topic_generator_for",
        lambda _settings: InvalidEvidenceGenerator(),
    )
    invalid_evidence = client.post(
        f"/api/creation-tasks/{invalid_evidence_task}/topics/generate", json={}
    )
    assert invalid_evidence.status_code == 502
    invalid_state = client.get(f"/api/creation-tasks/{invalid_evidence_task}/topics")
    assert invalid_state.json()["latest_run"]["status"] == "failed"
    assert invalid_state.json()["candidates"] == []
    assert client.post("/api/creation-tasks/not-visible/topics/generate", json={}).status_code == 404


def test_creation_plan_confirmation_requires_current_preflight_and_content_truth(
    tmp_path,
    monkeypatch,
    plan_owner,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "local"),
    )
    client = TestClient(main_module.app, headers=plan_owner[1])
    payload = {
        "goal": "一个需要真实资料的目标",
        "selected_voice": {"profile_id": "voice-local", "version": "v1"},
        "selected_person": {"profile_id": "voice-local-person", "version": "v1"},
    }

    blocked_preview = client.post("/api/creation-plans/preview", json=payload)
    assert blocked_preview.status_code == 200
    assert blocked_preview.json()["confirmable"] is False
    blocked_confirm = client.post(
        "/api/creation-plans/confirm",
        json={
            **payload,
            "preflight_fingerprint": blocked_preview.json()["preflight_fingerprint"],
        },
    )
    assert blocked_confirm.status_code == 409
    assert blocked_confirm.json()["detail"]["error"] == "content_sources_missing"

    _seed_creation_plan_facts(
        production, sources, workspace_id="local",
        verified_owner_user_id=plan_owner[0].member_id,
    )
    first_preview = client.post("/api/creation-plans/preview", json=payload)
    assert first_preview.status_code == 200
    sources.create_knowledge_item_owned(
        KnowledgeItem(
            id="knowledge-local-second",
            workspace_id="local",
            content_type="note",
            title="后续资料",
            body="预检完成后才加入的资料。",
            file_reference=None,
            authorization="authorized",
            ownership="user_owned",
            source_item_id=None,
            version="v1",
        ),
        verified_user_id=plan_owner[0].member_id,
    )
    stale_confirm = client.post(
        "/api/creation-plans/confirm",
        json={
            **payload,
            "preflight_fingerprint": first_preview.json()["preflight_fingerprint"],
        },
    )

    assert stale_confirm.status_code == 409
    assert stale_confirm.json()["detail"]["error"] == "creation_plan_preflight_stale"

    pronunciation_preview = client.post("/api/creation-plans/preview", json=payload)
    production.save_pronunciation_entry(
        PronunciationEntry(
            id="pronunciation-local",
            workspace_id="local",
            term="Codex",
            language="zh",
            target_pronunciation="科迪克斯",
            scope="person",
            scope_reference="voice-local-person",
            version="v2",
        )
    )
    pronunciation_stale = client.post(
        "/api/creation-plans/confirm",
        json={
            **payload,
            "preflight_fingerprint": pronunciation_preview.json()["preflight_fingerprint"],
        },
    )
    assert pronunciation_stale.status_code == 409
    assert pronunciation_stale.json()["detail"]["error"] == "creation_plan_preflight_stale"

    authorization_preview = client.post("/api/creation-plans/preview", json=payload)
    person = production.get_person_profile("local", "voice-local-person", "v1")
    person_authorization = production.get_authorization(
        "local", person.authorization.id
    )
    production.save_authorization(
        replace(person_authorization, version="v2", status="revoked")
    )
    changed_authorization_preview = client.post(
        "/api/creation-plans/preview", json=payload)
    assert any(
        blocker["reason"] == "参考使用受限：authorization_revoked"
        and blocker["stage"] == "avatar_lipsync"
        and blocker["blocks_confirmation"] is False
        for blocker in changed_authorization_preview.json()["blockers"]
    ), changed_authorization_preview.text
    authorization_stale = client.post(
        "/api/creation-plans/confirm",
        json={
            **payload,
            "preflight_fingerprint": authorization_preview.json()["preflight_fingerprint"],
        },
    )
    assert authorization_stale.status_code == 409
    assert authorization_stale.json()["detail"]["error"] == "creation_plan_preflight_stale"
    with production._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_plans").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 0


def test_voice_reference_import_uses_bytes_and_returns_registered_logical_reference(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda request: "workspace_a")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 16_000)

    response = TestClient(main_module.app).post(
        "/api/media/voice-references/import",
        files={"audio": ("owner-recording.wav", buffer.getvalue(), "audio/wav")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["reference"].startswith("workspace://approved/voices/voice_")
    assert "owner-recording" not in body["reference"]
    assert body["duration_seconds"] == pytest.approx(1.0)
    resolved = production.resolve_workspace_media_reference(
        "workspace_a",
        body["reference"],
        require_registered=True,
    )
    assert resolved.is_file()
    assert resolved.read_bytes() == buffer.getvalue()

    rejected = TestClient(main_module.app).post(
        "/api/media/voice-references/import",
        files={"audio": ("not-a-wave.wav", b"not-wave", "audio/wav")},
    )
    assert rejected.status_code == 422
    assert len(list((production.video_library_path / "approved" / "voices").glob("*.wav"))) == 1


def test_profile_api_retires_legacy_voice_writes(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    client = TestClient(main_module.app)

    response = client.put(
        "/api/production/profile",
        json={
            "workspace_name": "Studio",
            "brand_name": "Brand",
            "brand_template_name": "Douyin Base",
            "brand_colors": [],
            "subtitle_style": "",
            "intro_text": "",
            "outro_text": "",
            "background_music_path": None,
            "voice_name": "Owner",
            "voice_reference_path": None,
            "voice_consent_confirmed": False,
            "voice_model_status": "ready",
            "voice_model_message": "client says ready",
            "source_urls": ["https://example.com/feed"],
            "use_obsidian": False,
            "use_hermes": False,
            "factuality_gate_enabled": True,
            "cloud_enhancement_enabled": False,
        },
    )

    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "legacy_profile_voice_write_retired"
    assert "/api/media/voice-profiles" in response.json()["detail"]["repair_action"]


def test_profile_duration_is_configurable_beyond_ninety_seconds(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    production.save_profile(
        ProductionProfile(
            workspace_id=LEGACY_WORKSPACE_ID,
            voice_name="历史声音",
            voice_reference_path="workspace://approved/legacy.wav",
            voice_reference_text="历史声音参考文字",
            voice_consent_confirmed=True,
        )
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    client = TestClient(main_module.app)

    response = client.put(
        "/api/production/profile",
        json={
            "workspace_name": "Studio",
            "brand_name": "Brand",
            "brand_template_name": "Vertical",
            "target_duration_min_seconds": 240,
            "target_duration_max_seconds": 300,
        },
    )

    assert response.status_code == 200
    assert response.json()["target_duration_min_seconds"] == 240
    assert response.json()["target_duration_max_seconds"] == 300
    assert response.json()["voice_name"] == "历史声音"










def test_persisted_narration_reference_is_workspace_scoped_and_never_an_absolute_path(
    tmp_path,
):
    production = _repository(tmp_path)
    audio = tmp_path / "outputs" / "narration.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")
    production.save_narration_artifact(
        NarrationArtifactRecord(
            id="narration_1",
            workspace_id="workspace_a",
            episode_id="episode_1",
            script_id="script_1",
            voice_profile_id="voice_1",
            cache_key="cache_1",
            audio_path=str(audio),
            duration_seconds=8.0,
            sample_rate=24000,
            model_id="voice-model",
        )
    )
    reference = production.narration_artifact_reference("cache_1")

    assert reference == "narration://artifact/cache_1"
    assert production.resolve_authorized_narration_audio_reference(
        "workspace_a", reference
    ) == audio
    with pytest.raises(MediaAuthorityError):
        production.resolve_authorized_narration_audio_reference("workspace_b", reference)
    with pytest.raises(MediaAuthorityError):
        production.resolve_authorized_narration_audio_reference("workspace_a", str(audio))






def test_narration_artifact_cache_requires_existing_audio(tmp_path):
    production = _repository(tmp_path)
    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"audio")
    record = NarrationArtifactRecord(
        id="narration_1",
        workspace_id="workspace_a",
        episode_id="episode_1",
        script_id="script_1",
        voice_profile_id="voice_a",
        cache_key="cache_1",
        audio_path=str(audio),
        duration_seconds=12.0,
        sample_rate=24000,
        model_id="voice-model",
    )
    production.save_narration_artifact(record)

    assert production.get_narration_artifact("workspace_a", "cache_1") == record
    audio.unlink()
    assert production.get_narration_artifact("workspace_a", "cache_1") is None






def test_material_tags_collections_usage_and_gap_insights(tmp_path):
    production = _repository(tmp_path)
    video = production.video_library_path / "office_laptop_typing.mp4"
    video.write_bytes(b"video")
    _confirm_library_copies(production, "workspace_a")
    asset = production.scan_library("workspace_a")[0]
    asset = production.analyze_asset("workspace_a", asset.id)
    segment = asset.segments[0]
    collection = production.save_collection(
        MaterialCollection(
            id="collection_work",
            workspace_id="workspace_a",
            name="办公素材",
        )
    )
    asset = production.update_segment(
        "workspace_a",
        asset.id,
        segment.id,
        description="办公室里使用笔记本电脑打字",
        transcript="",
        review_status="raw",
        user_tags=["创业", "  创业  "],
        collection_ids=[collection.id],
    )
    production.record_segment_usage(
        "workspace_a",
        asset.id,
        segment.id,
        plan_id="plan_1",
        clip_id="clip_1",
    )
    production.record_segment_usage(
        "workspace_a",
        asset.id,
        segment.id,
        plan_id="plan_1",
        clip_id="clip_1",
    )
    saved_segment = production.get_asset("workspace_a", asset.id).segments[0]
    insights = production.library_insights("workspace_a")

    assert saved_segment.user_tags == ["创业"]
    assert saved_segment.collection_ids == [collection.id]
    assert saved_segment.usage_count == 1
    assert any(item["tag"] == "创业" for item in insights["tags"])
    assert "场景:户外" in insights["recommended_gaps"]




def test_material_library_endpoint_is_paginated(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    for index in range(3):
        (production.video_library_path / f"clip-{index}.mp4").write_bytes(b"video")
    _confirm_library_copies(production, LEGACY_WORKSPACE_ID)
    production.scan_library(LEGACY_WORKSPACE_ID)
    monkeypatch.setattr(main_module, "production_repo", production)
    client = TestClient(main_module.app)

    first = client.get("/api/production/materials?limit=2&offset=0")
    second = client.get("/api/production/materials?limit=2&offset=2")

    assert first.status_code == 200
    assert first.json()["total_count"] == 3
    assert len(first.json()["assets"]) == 2
    assert first.json()["has_more"] is True
    assert second.status_code == 200
    assert len(second.json()["assets"]) == 1
    assert second.json()["has_more"] is False


@pytest.mark.parametrize('change',['replace_file','remove_file','remove_segment','change_interval'])
def test_visual_feature_read_rejects_stale_source_without_reanalysis(tmp_path,change):
    provider=_FakeVisualProvider()
    production=ProductionRepository(database_path=tmp_path/'studio.sqlite3',video_library_path=tmp_path,
        duration_reader=lambda _:8.0,scene_reader=lambda *_:[4.0],visual_provider=provider)
    video=tmp_path/'source.mp4'
    video.write_bytes(b'original source')
    _confirm_library_copies(production, 'workspace_a')
    asset=production.scan_library('workspace_a')[0]
    analyzed=production.analyze_asset('workspace_a',asset.id)
    segment=analyzed.segments[0]
    assert production.get_visual_feature('workspace_a',segment.id,provider.model_id)==[.1,.2,.3]
    # A human text correction does not invalidate unchanged pixel embeddings.
    segment.description='人工修正画面描述'
    production._save_asset(analyzed)
    assert production.get_visual_feature('workspace_a',segment.id,provider.model_id)==[.1,.2,.3]
    if change=='replace_file':
        video.write_bytes(b'replacement source with different length')
    elif change=='remove_file':
        video.unlink()
    elif change=='change_interval':
        segment.end_seconds-=1
        production._save_asset(analyzed)
    else:
        analyzed.segments=[]
        production._save_asset(analyzed)
    assert production.get_visual_feature('workspace_a',segment.id,provider.model_id) is None
    assert production.get_visual_feature('workspace_b',segment.id,provider.model_id) is None


@pytest.fixture
def foundation_state(tmp_path, monkeypatch):
    from app.avatar_lipsync import ProfileReference
    from app.production import MaterialAsset, MaterialSegment
    from app.sources import ObsidianVault

    production = _repository(tmp_path)
    members = LocalMemberService(tmp_path / "foundation-members.sqlite3")
    owner, owner_token = members.whoami(None)
    other, other_token = members.join(members.create_invite_code(), "合成另成员")
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "model_catalog", SimpleNamespace(
        status=lambda _spec: SimpleNamespace(ready=True, state="ready", message="受控模型已就绪")
    ))
    configured = Settings(topic_source_urls=("https://fixture.invalid/feed",),
                          product_hunt_token=None, hermes_radar_path=None,
                          obsidian_vault_path=None)
    monkeypatch.setattr(main_module, "settings", configured)
    monkeypatch.setattr(main_module, "vault", ObsidianVault(None))
    ws = LEGACY_WORKSPACE_ID
    production.save_profile(ProductionProfile(ws, source_urls=[], use_obsidian=False, use_hermes=False))
    reference, _, _ = production.import_voice_reference_wav(
        ws, synthetic_wav_bytes(), verified_owner_user_id=owner.member_id,
    )
    source = production._asset_by_path(ws, str(production.resolve_workspace_media_reference(ws, reference)))
    voice = production.save_versioned_voice_profile(VersionedVoiceProfile(
        id="foundation-voice", workspace_id=ws, version="v1", language="zh",
        display_name="合成独立声音", reference_audio_path=reference,
        reference_text="合成参考文字", model_id="fixture-voice",
        reference=ProfileReference(asset_id=source.id),
    ), verified_owner_user_id=owner.member_id)
    path = production.video_library_path / "foundation.mp4"
    path.write_bytes(b"synthetic metadata-only video")
    stat = path.stat()
    video = production._save_asset(MaterialAsset(
        id="foundation-video", workspace_id=ws, owner_user_id=owner.member_id,
        file_name=path.name, path=str(path), size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        duration_seconds=4, analysis_status="ready", review_status="raw", usage_role="b_roll",
        segments=[MaterialSegment("foundation-segment", "foundation-video", 0, 4, usage_role="b_roll")],
    ))
    return SimpleNamespace(repo=production, owner=owner, other=other, voice=voice, video=video,
        path=path, settings=configured, client=TestClient(main_module.app),
        headers={"X-Local-Member-Token": owner_token},
        other_headers={"X-Local-Member-Token": other_token})


def test_foundation_uses_current_member_for_private_material_and_voice(foundation_state):
    state = foundation_state
    own = state.client.get("/api/production/foundation", headers=state.headers)
    other = state.client.get("/api/production/foundation", headers=state.other_headers)
    assert own.status_code == other.status_code == 200
    assert own.json()["readiness"] == {
        "ready": True, "blockers": [], "asset_count": 2,
        "approved_asset_count": 1, "approved_segment_count": 1, "total_duration_seconds": 6.0,
    }
    assert other.json()["readiness"]["asset_count"] == 0
    assert other.json()["readiness"]["approved_segment_count"] == 0
    blockers = other.json()["readiness"]["blockers"]
    assert {item["code"] for item in blockers} == {
        "versioned_voice_authorization_missing", "approved_materials_empty",
    }
    assert not any("不属于" in item["message"] for item in blockers)


def test_foundation_invalid_identity_never_falls_back_to_diagnostic(foundation_state, monkeypatch):
    state = foundation_state
    calls = []
    monkeypatch.setattr(state.repo, "readiness", lambda *args, **kwargs: calls.append(kwargs))
    response = state.client.get("/api/production/foundation", headers={"X-Local-Member-Token": "invalid"})
    assert response.status_code == 401
    assert calls == []


def test_foundation_local_diagnostic_is_explicit_and_preserves_single_user_read(foundation_state):
    response = foundation_state.client.get("/api/production/foundation")
    assert response.status_code == 200
    assert response.json()["readiness"]["ready"] is True


@pytest.mark.parametrize("source", ["urls", "vault", "product_hunt", "hermes", "none", "legacy_only"])
def test_foundation_source_readiness_matches_current_generator(foundation_state, tmp_path, monkeypatch, source):
    from app.sources import ObsidianVault
    from app.topics import MissingTopicGenerator, topic_generator_from_settings

    state = foundation_state
    vault_path = tmp_path / "configured-vault"
    if source == "vault":
        vault_path.mkdir()
    settings = replace(state.settings,
        topic_source_urls=("https://fixture.invalid/feed",) if source == "urls" else (),
        product_hunt_token="synthetic-config-only" if source == "product_hunt" else None,
        hermes_radar_path=str(tmp_path / "not-fetched-radar") if source == "hermes" else None,
        obsidian_vault_path=str(vault_path) if source == "vault" else None,
    )
    vault = ObsidianVault(settings.obsidian_vault_path)
    state.repo.save_profile(ProductionProfile(LEGACY_WORKSPACE_ID,
        source_urls=["https://old.invalid/feed"] if source == "legacy_only" else [],
        use_obsidian=source == "legacy_only", use_hermes=source == "legacy_only"))
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "vault", vault)
    generator = topic_generator_from_settings(settings, vault)
    has_current_sources = not isinstance(generator, MissingTopicGenerator)
    response = state.client.get("/api/production/foundation", headers=state.headers)
    assert response.status_code == 200
    blockers = response.json()["readiness"]["blockers"]
    assert ("source_pool_empty" not in {item["code"] for item in blockers}) == has_current_sources
    assert has_current_sources == (source not in {"none", "legacy_only"})


@pytest.mark.parametrize("mutation", ["lost", "changed", "excluded", "reference_only", "bad_range", "excluded_segment", "unanalysed", "manual_only"])
def test_foundation_counts_only_current_qualified_material(foundation_state, mutation):
    state = foundation_state
    asset = state.video
    if mutation == "lost":
        state.path.unlink()
    elif mutation == "changed":
        state.path.write_bytes(b"changed source with different bytes")
    elif mutation in {"excluded", "reference_only"}:
        asset.use_state = mutation
    elif mutation == "manual_only":
        asset.use_state = "not_for_automation"
    elif mutation == "bad_range":
        asset.segments[0].end_seconds = 5
    elif mutation == "excluded_segment":
        asset.segments[0].usage_role = "exclude"
    else:
        asset.analysis_status = "pending"
    state.repo._save_asset(asset)
    response = state.client.get("/api/production/foundation", headers=state.headers)
    assert response.status_code == 200
    readiness = response.json()["readiness"]
    assert readiness["asset_count"] == 2
    assert readiness["approved_asset_count"] == (1 if mutation == "manual_only" else 0)
    assert readiness["approved_segment_count"] == (1 if mutation == "manual_only" else 0)


@pytest.mark.parametrize("issue", ["no_voice", "lost_reference", "empty_text", "restricted", "model_missing", "both_missing", "range"])
def test_foundation_separates_voice_reference_from_live_engine(foundation_state, monkeypatch, issue):
    from app.avatar_lipsync import ProfileReference

    state = foundation_state
    if issue in {"no_voice", "both_missing"}:
        monkeypatch.setattr(state.repo, "list_versioned_voice_profiles", lambda _ws: [])
    elif issue == "lost_reference":
        state.repo.resolve_workspace_media_reference(LEGACY_WORKSPACE_ID, state.voice.reference_audio_path).unlink()
    elif issue in {"empty_text", "restricted", "range"}:
        voice = replace(state.voice, version="v2",
            reference_text="" if issue == "empty_text" else state.voice.reference_text,
            use_state="excluded" if issue == "restricted" else "available",
            reference=ProfileReference(asset_id=state.voice.reference.asset_id, start_seconds=.25, end_seconds=1.25)
                if issue == "range" else state.voice.reference)
        state.repo.save_versioned_voice_profile(voice, verified_owner_user_id=state.owner.member_id)
    if issue in {"model_missing", "both_missing"}:
        # The real foundation wrapper still reads the current catalog, not old profile state.
        monkeypatch.setattr(main_module, "model_catalog", SimpleNamespace(
            status=lambda _spec: SimpleNamespace(ready=False, state="missing", message="受控模型未安装")
        ))
    response = state.client.get("/api/production/foundation", headers=state.headers)
    assert response.status_code == 200
    blockers = response.json()["readiness"]["blockers"]
    codes = {item["code"] for item in blockers}
    assert ("voice_model_not_ready" in codes) == (issue in {"model_missing", "both_missing"})
    if issue == "empty_text":
        assert "voice_reference_missing" in codes
    elif issue in {"no_voice", "both_missing"}:
        assert "versioned_voice_authorization_missing" in codes
        message = next(item["message"] for item in blockers if item["code"] == "versioned_voice_authorization_missing")
        assert "授权" not in message and "文案" in message
    elif issue == "range":
        assert codes == set()
    elif issue != "model_missing":
        assert "voice_reference_unavailable" in codes
    else:
        assert codes == {"voice_model_not_ready"}
    assert response.json()["profile"]["voice_model_status"] == ("blocked" if issue in {"model_missing", "both_missing"} else "ready")


@pytest.mark.parametrize("ranged", [False, True])
def test_foundation_video_voice_preparation_never_decodes_or_derives(foundation_state, monkeypatch, ranged):
    from app.avatar_lipsync import ProfileReference

    state = foundation_state
    # Registration uses a controlled format qualification for synthetic video bytes.
    # Preparation must subsequently use source facts, never this decoder or a model.
    monkeypatch.setattr(state.repo, "_material_file_intact", lambda _asset: True)
    voice = state.repo.save_versioned_voice_profile(VersionedVoiceProfile(
        id="video-voice", workspace_id=LEGACY_WORKSPACE_ID, version="v1", language="zh",
        display_name="合成视频声音参考", model_id="fixture-voice", reference_text="视频中的合成文字",
        reference_audio_path=state.repo.workspace_media_reference_for_asset(LEGACY_WORKSPACE_ID, state.video.id),
        reference=ProfileReference(asset_id=state.video.id,
            segment_id=state.video.segments[0].id if ranged else None,
            start_seconds=.5 if ranged else None, end_seconds=2 if ranged else None),
    ), verified_owner_user_id=state.owner.member_id)
    monkeypatch.setattr(state.repo, "list_versioned_voice_profiles", lambda _ws: [voice])

    def no_decoder(_asset):
        raise AssertionError("foundation preparation must not decode video")

    monkeypatch.setattr(state.repo, "_material_file_intact", no_decoder)
    response = state.client.get("/api/production/foundation", headers=state.headers)
    assert response.status_code == 200
    assert response.json()["readiness"]["ready"] is True
    with pytest.raises(PermissionError, match="设备执行许可"):
        state.repo.materialize_profile_reference(voice, grant_context=None,
            viewer_user_id=state.owner.member_id)
    assert not (state.repo.video_library_path / ".profile_references").exists()


def test_foundation_checks_all_readable_voices_before_reporting_missing_text(foundation_state, monkeypatch):
    state = foundation_state
    incomplete = state.repo.save_versioned_voice_profile(
        replace(state.voice, id="incomplete-voice", reference_text=""),
        verified_owner_user_id=state.owner.member_id,
    )
    monkeypatch.setattr(state.repo, "list_versioned_voice_profiles", lambda _ws: [incomplete, state.voice])
    response = state.client.get("/api/production/foundation", headers=state.headers)
    assert response.status_code == 200
    assert response.json()["readiness"]["ready"] is True
