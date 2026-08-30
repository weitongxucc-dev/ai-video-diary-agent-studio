import hashlib
import io
import sqlite3
import wave
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.model_runtime import ModelCatalog
from app.models import EpisodeStatus, ScriptPackage, ScriptSection
from app.narration import NarrationArtifact
from app.production import (
    Authorization,
    CreationPlan,
    CreationPlanBlocker,
    CreationTask,
    MaterialCollection,
    MediaAuthorityError,
    NarrationArtifactRecord,
    PersonProfile,
    PronunciationEntry,
    ProductionBatch,
    ProductionProfile,
    ProductionRepository,
    VersionedVoiceProfile,
    VoiceProfile,
)
from app.repository import LEGACY_WORKSPACE_ID, EpisodeRepository
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, SourceConnector, SourceItem
from app.topics import (
    CreationTaskGeneratedScript,
    CreationTaskGeneratedScriptSection,
    CreationTaskGeneratedTopic,
    CreationTaskTopicGenerationError,
)
from app.visual_semantics import VisualEmbedding
from app.voice_clone import MediaRuntimeFailure, VoiceCloneError


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
    reference_audio_path: str,
    language: str = "zh",
    authorization_status: str = "active",
) -> VersionedVoiceProfile:
    person_authorization = Authorization(
        id=f"{voice_id}-person-authorization",
        workspace_id=workspace_id,
        version="v1",
        rights_holder="权利人",
        content_types=("person_likeness",),
        usage_scopes=("avatar_lipsync",),
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
        evidence_reference="consent://person/1",
        status="active",
    )
    person = PersonProfile(
        id=f"{voice_id}-person",
        workspace_id=workspace_id,
        display_name="主播",
        version="v1",
        avatar_reference="asset://avatar/host",
        background_capabilities=("studio",),
        shot_capabilities=("medium",),
        authorization=person_authorization,
    )
    production.save_person_profile(person)
    voice = VersionedVoiceProfile(
        id=voice_id,
        workspace_id=workspace_id,
        person_profile_id=person.id,
        person_version=person.version,
        version=version,
        language=language,  # type: ignore[arg-type]
        reference_audio_path=reference_audio_path,
        reference_text="经过授权的声音参考文字。",
        model_id="test-voice-model",
        authorization=Authorization(
            id=f"{voice_id}-authorization",
            workspace_id=workspace_id,
            version="v1",
            rights_holder="权利人",
            content_types=("voice",),
            usage_scopes=("narration",),
            effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            expires_at=None,
            evidence_reference="consent://voice/1",
            status=authorization_status,  # type: ignore[arg-type]
        ),
    )
    return production.save_versioned_voice_profile(voice)


def _seed_creation_plan_facts(
    production: ProductionRepository,
    sources: SourceRepository,
    *,
    workspace_id: str,
) -> None:
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
    sources.create_knowledge_item(
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
        )
    )
    video = production.video_library_path / f"{workspace_id}.mp4"
    video.write_bytes(b"video")
    asset = production.scan_library(workspace_id)[0]
    production.analyze_asset(workspace_id, asset.id)
    production.update_asset_review(
        workspace_id,
        asset.id,
        review_status="approved",
        rights_confirmed=True,
        brand_safe=True,
        usage_role="b_roll",
    )
    voice_reference = production.video_library_path / "approved" / f"{workspace_id}.wav"
    voice_reference.parent.mkdir(exist_ok=True)
    voice_reference.write_bytes(b"voice")
    voice = _save_versioned_voice(
        production,
        workspace_id=workspace_id,
        voice_id=f"voice-{workspace_id}",
        version="v1",
        reference_audio_path=f"workspace://approved/{workspace_id}.wav",
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


def test_materials_require_analysis_rights_and_brand_review(tmp_path):
    production = _repository(tmp_path)
    video = production.video_library_path / "demo.mp4"
    video.write_bytes(b"test video placeholder")

    assets = production.scan_library("workspace_a")
    assert len(assets) == 1
    assert assets[0].review_status == "raw"

    with pytest.raises(ValueError, match="镜头分析"):
        production.update_asset_review(
            "workspace_a",
            assets[0].id,
            review_status="approved",
            rights_confirmed=True,
            brand_safe=True,
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
            rights_confirmed=True,
            brand_safe=True,
        )

    with pytest.raises(ValueError, match="版权"):
        production.update_asset_review(
            "workspace_a",
            assets[0].id,
            review_status="approved",
            rights_confirmed=False,
            brand_safe=True,
            usage_role="b_roll",
        )

    approved = production.update_asset_review(
        "workspace_a",
        assets[0].id,
        review_status="approved",
        rights_confirmed=True,
        brand_safe=True,
        usage_role="b_roll",
    )
    assert approved.review_status == "approved"
    assert all(segment.review_status == "approved" for segment in approved.segments)


def test_production_data_is_workspace_scoped_and_persistent(tmp_path):
    production = _repository(tmp_path)
    video = production.video_library_path / "shared.mov"
    video.write_bytes(b"test video placeholder")
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


def test_batch_is_blocked_until_profile_voice_and_materials_are_ready(tmp_path):
    production = _repository(tmp_path)
    blocked = production.create_batch("workspace_a", name="first", target_count=10)
    assert blocked.status == "blocked"
    assert "brand_profile_missing" in blocked.blocker_codes
    assert "approved_materials_empty" in blocked.blocker_codes

    reference = production.video_library_path / "approved" / "voice.wav"
    reference.parent.mkdir()
    reference.write_bytes(b"voice")
    production.save_profile(
        ProductionProfile(
            workspace_id="workspace_a",
            brand_name="Studio",
            brand_template_name="Douyin Base",
            voice_name="Authorized Voice",
            voice_reference_path=str(reference),
            voice_reference_text="这是经过授权的声音参考文字。",
            voice_consent_confirmed=True,
            voice_model_status="ready",
            voice_model_message="本地模型已就绪。",
            source_urls=["https://example.com/feed"],
        )
    )
    _save_versioned_voice(
        production,
        workspace_id="workspace_a",
        voice_id="voice-ready",
        version="v1",
        reference_audio_path="workspace://approved/voice.wav",
    )
    video = production.video_library_path / "approved.m4v"
    video.write_bytes(b"test video placeholder")
    asset = production.scan_library("workspace_a")[0]
    production.analyze_asset("workspace_a", asset.id)
    production.update_asset_review(
        "workspace_a",
        asset.id,
        review_status="approved",
        rights_confirmed=True,
        brand_safe=True,
        usage_role="b_roll",
    )

    readiness = production.readiness("workspace_a")
    assert readiness["ready"] is True
    created = production.create_batch("workspace_a", name="ready", target_count=10)
    assert created.status == "created"
    assert created.blocker_codes == []


def test_scene_ranges_split_long_shots_into_twelve_second_chunks(tmp_path):
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
        duration_reader=lambda _path: 31.0,
        scene_reader=lambda _path, _duration: [],
    )
    video = tmp_path / "long.mp4"
    video.write_bytes(b"test video placeholder")
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
        rights_confirmed=True,
        brand_safe=True,
        usage_role="a_roll",
    )

    assert [item.usage_role for item in reviewed.segments] == ["a_roll", "b_roll"]
    assert production.readiness("workspace_a")["approved_segment_count"] == 1


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
            "error": "legacy_batch_creation_retired",
            "message": "旧的生产批次创建入口已退役，不会创建创作任务。",
            "impact": "本次请求没有写入批次、创作方案或创作任务。",
            "repair_action": "从创作任务进入“检查可用内容”，再完成“确认并创建任务”流程。",
            "retryable": False,
        }
    assert hashlib.sha256(production.database_path.read_bytes()).hexdigest() == before_create
    assert production.list_batches(LEGACY_WORKSPACE_ID) == []

    listed = client.get("/api/production/batches")
    assert listed.status_code == 200
    assert listed.json()["total_count"] == 0


def test_creation_plan_preview_is_read_only_and_confirm_is_persistent_and_isolated(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(production, sources, workspace_id="workspace_a")
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace_a"),
    )
    client = TestClient(main_module.app)
    payload = {
        "goal": "为产品能力写一条竖屏观察视频。",
        "name": "能力观察",
        "target_count": 1,
        "language": "zh-CN",
        "aspect_ratio": "9:16",
        "duration_target_seconds": 60,
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
    saved_plan = production.get_creation_plan("workspace_a", confirmed_body["id"])
    saved_task = production.get_creation_task(
        "workspace_a", confirmed_body["creation_task"]["id"]
    )
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
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(production, sources, workspace_id="local")
    (production.video_library_path / "approved" / "local.wav").unlink()
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(main_module, "current_workspace_id", lambda request: "local")

    response = TestClient(main_module.app).post(
        "/api/creation-plans/preview",
        json={"goal": "制作一条中文口播视频"},
    )

    assert response.status_code == 200
    assert any(
        blocker["reason"] == "voice_reference_wav_unavailable"
        and blocker["stage"] == "narration"
        and blocker["blocks_confirmation"] is False
        for blocker in response.json()["blockers"]
    )


def test_creation_task_list_is_workspace_scoped_ordered_and_read_only(
    tmp_path,
    monkeypatch,
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
    client = TestClient(main_module.app)
    before_empty_list = hashlib.sha256(production.database_path.read_bytes()).hexdigest()
    empty_list = client.get("/api/creation-tasks")
    assert empty_list.status_code == 200
    assert empty_list.json() == {"items": [], "total_count": 0}
    assert hashlib.sha256(production.database_path.read_bytes()).hexdigest() == before_empty_list

    _seed_creation_plan_facts(production, sources, workspace_id="workspace_a")
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


def test_creation_task_topics_persist_confirm_and_fail_closed_by_workspace(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(production, sources, workspace_id="workspace_a")
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            llm_provider="custom",
            llm_base_url="https://model.example/v1",
            llm_model="topic-test",
            llm_api_key="test-key",
        ),
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
        "_creation_task_topic_generator_from_settings",
        lambda: FakeGenerator(),
    )
    client = TestClient(main_module.app)
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
    confirmed = client.post(
        f"/api/creation-tasks/{task_id}/topics/{first_topic_id}/confirm", json={}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["task"]["stage"] == "script"
    assert confirmed.json()["task"]["stage_status"] == "not_started"
    assert confirmed.json()["selected_topic_id"] == first_topic_id
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

    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            llm_provider="custom",
            llm_base_url="https://model.example/v1",
            llm_model="topic-test-v2",
            llm_api_key="test-key",
        ),
    )
    changed_route = client.get(f"/api/creation-tasks/{task_id}/topics")
    assert changed_route.status_code == 200
    assert changed_route.json()["route_preflight"]["availability"] == "unverified"


def test_creation_task_script_generation_confirmation_and_restart_persistence(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(production, sources, workspace_id="workspace_a")
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            llm_provider="custom",
            llm_base_url="https://model.example/v1",
            llm_model="deepseek-test",
            llm_api_key="test-key",
        ),
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
        "_creation_task_topic_generator_from_settings",
        lambda: FakeTopicGenerator(),
    )
    monkeypatch.setattr(
        main_module,
        "_creation_task_script_generator_from_settings",
        lambda: FakeScriptGenerator(),
    )
    client = TestClient(main_module.app)
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

    generated = client.post(
        f"/api/creation-tasks/{task_id}/scripts/generate", json={}
    )
    assert generated.status_code == 200
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
        json={"title": "用户确认后的文案", "sections": edited_sections},
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
    with reloaded._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM production_batches").fetchone()[0] == 0


def test_creation_task_can_bind_a_later_authorized_voice_without_redoing_script(
    tmp_path,
    monkeypatch,
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
        approved_asset_count=1,
        approved_clip_count=1,
        material_rule_summary="只使用已审核镜头",
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
    )
    production.confirm_creation_plan(plan, task)
    reference = production.video_library_path / "approved" / "voice-recovery.wav"
    reference.parent.mkdir(exist_ok=True)
    reference.write_bytes(b"valid-wav-reference")
    voice = _save_versioned_voice(
        production,
        workspace_id=workspace_id,
        voice_id="voice-recovery",
        version="v1",
        reference_audio_path="workspace://approved/voice-recovery.wav",
    )
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

    response = TestClient(main_module.app).post(
        f"/api/creation-tasks/{task.id}/narration/voice",
        json={"voice_profile_id": voice.id, "voice_version": voice.version},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["selected_voice"]["profile_id"] == voice.id
    assert body["task"]["stage"] == "narration"
    assert body["task"]["stage_status"] == "not_started"
    assert body["task"]["confirmed_script_id"] == "script-confirmed"
    assert body["task"]["stage_reason"] is None
    assert not any(
        blocker["stage"] == "narration" for blocker in body["plan"]["blockers"]
    )
    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_creation_task(workspace_id, task.id).confirmed_script_id == "script-confirmed"
    assert reloaded.get_creation_plan(workspace_id, plan.id).selected_voice.profile_id == voice.id


def test_creation_task_topic_failure_records_capability_license_and_provider_errors(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(production, sources, workspace_id="workspace_a")
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", sources)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace_a"),
    )
    client = TestClient(main_module.app)

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

    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            llm_provider="custom",
            llm_base_url="https://model.example/v1",
            llm_model="topic-test",
            llm_api_key="test-key",
        ),
    )
    source = sources.get_source_item("workspace_a", "source-workspace_a")
    sources.save_source_items(
        "workspace_a",
        [replace(source, license="unknown")],
    )
    license_task = create_task("许可未知")
    blocked = client.post(
        f"/api/creation-tasks/{license_task}/topics/generate", json={}
    )
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["reason"] == "source_license_unverified"
    blocked_state = client.get(f"/api/creation-tasks/{license_task}/topics").json()
    assert blocked_state["latest_run"]["status"] == "failed"
    assert blocked_state["latest_run"]["route_snapshot"]["availability"] == "unverified"

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

    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            llm_provider="ollama",
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_model="local-topic-test",
        ),
    )
    monkeypatch.setattr(
        main_module,
        "_creation_task_topic_generator_from_settings",
        lambda: LocalGenerator(),
    )
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
        "_creation_task_topic_generator_from_settings",
        lambda: FailingGenerator(),
    )
    failed = client.post(
        f"/api/creation-tasks/{provider_task}/topics/generate", json={}
    )
    assert failed.status_code == 502
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
        "_creation_task_topic_generator_from_settings",
        lambda: InvalidEvidenceGenerator(),
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
    client = TestClient(main_module.app)
    payload = {"goal": "一个需要真实资料的目标"}

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

    _seed_creation_plan_facts(production, sources, workspace_id="local")
    first_preview = client.post("/api/creation-plans/preview", json=payload)
    assert first_preview.status_code == 200
    sources.create_knowledge_item(
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
        )
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
        blocker["reason"] == "authorization_revoked"
        and blocker["stage"] == "avatar_lipsync"
        and blocker["blocks_confirmation"] is False
        for blocker in changed_authorization_preview.json()["blockers"]
    )
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


def test_episode_voice_selection_requires_a_versioned_voice_identity(tmp_path):
    production = _repository(tmp_path)
    voice = _save_versioned_voice(
        production,
        workspace_id="workspace_a",
        voice_id="voice_b",
        version="voice-v1",
        reference_audio_path="workspace://approved/voice-b.wav",
    )

    with pytest.raises(MediaAuthorityError, match="选择已授权"):
        production.resolve_episode_narration_inputs("workspace_a", "episode_1")

    production.select_episode_versioned_voice(
        "workspace_a", "episode_1", voice.id, voice.version
    )
    inputs = production.resolve_episode_narration_inputs("workspace_a", "episode_1")
    assert inputs.voice.id == "voice_b"
    assert inputs.voice.version == "voice-v1"
    assert inputs.voice.authorization.id == "voice_b-authorization"

    production.select_episode_versioned_voice("workspace_a", "episode_1", None, None)
    with pytest.raises(MediaAuthorityError, match="选择已授权"):
        production.resolve_episode_narration_inputs("workspace_a", "episode_1")


def test_voice_selection_api_uses_versioned_media_voice_and_retires_legacy_write(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    episodes = EpisodeRepository(database_path=tmp_path / "episodes.sqlite3")
    episode = episodes.create(workspace_id=LEGACY_WORKSPACE_ID)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "repo", episodes)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    client = TestClient(main_module.app)
    authorization = {
        "id": "voice-authorization",
        "version": "v1",
        "rights_holder": "权利人",
        "content_types": ["voice"],
        "usage_scopes": ["narration"],
        "effective_from": "2025-01-01T00:00:00+00:00",
        "expires_at": None,
        "evidence_reference": "consent://voice/1",
        "status": "active",
    }
    person = client.post(
        "/api/media/person-profiles",
        json={
            "id": "person-a",
            "display_name": "主播 A",
            "version": "person-v1",
            "avatar_reference": "asset://avatar/person-a",
            "background_capabilities": ["studio"],
            "shot_capabilities": ["medium"],
            "authorization": {
                **authorization,
                "id": "person-authorization",
                "content_types": ["person_likeness"],
                "usage_scopes": ["avatar_lipsync"],
            },
        },
    )
    voice = client.post(
        "/api/media/voice-profiles",
        json={
            "id": "voice-b",
            "person_profile_id": "person-a",
            "person_version": "person-v1",
            "version": "voice-v1",
            "language": "zh",
            "reference_audio_path": "workspace://approved/api-b.wav",
            "reference_text": "声音 B",
            "model_id": "test-voice-model",
            "authorization": authorization,
        },
    )

    assert person.status_code == 200
    assert voice.status_code == 200
    legacy = client.post(
        "/api/production/voices",
        json={
            "name": "legacy",
            "reference_audio_path": "workspace://approved/legacy.wav",
            "reference_text": "旧声音",
            "consent_confirmed": True,
        },
    )
    assert legacy.status_code == 410
    assert legacy.json()["detail"]["error"] == "legacy_voice_profile_write_retired"
    selected = client.put(
        f"/api/production/episodes/{episode.id}/voice",
        json={"voice_profile_id": "voice-b", "voice_version": "voice-v1"},
    )
    assert selected.status_code == 200
    assert selected.json()["source"] == "episode"
    assert selected.json()["voice_profile_id"] == "voice-b"
    assert selected.json()["voice_version"] == "voice-v1"
    assert selected.json()["authorization_id"] == "voice-authorization"


def test_episode_voice_resolution_fails_closed_for_revocation_and_workspace_escape(tmp_path):
    production = _repository(tmp_path)
    voice = _save_versioned_voice(
        production,
        workspace_id="workspace_a",
        voice_id="voice-authorized",
        version="voice-v1",
        reference_audio_path="workspace://approved/voice.wav",
    )
    production.select_episode_versioned_voice(
        "workspace_a", "episode_1", voice.id, voice.version
    )
    production.save_authorization(
        replace(voice.authorization, version="v2", status="revoked")
    )

    with pytest.raises(MediaAuthorityError) as revoked:
        production.resolve_episode_narration_inputs("workspace_a", "episode_1")
    assert revoked.value.reason == "voice_authorization_authorization_revoked"
    assert revoked.value.impact
    assert revoked.value.repair_action

    with pytest.raises(KeyError):
        production.select_episode_versioned_voice(
            "workspace_b", "episode_1", voice.id, voice.version
        )
    with pytest.raises(MediaAuthorityError) as media_escape:
        _save_versioned_voice(
            production,
            workspace_id="workspace_b",
            voice_id="voice-b",
            version="voice-v1",
            reference_audio_path="workspace://approved/voice.wav",
        )
    assert media_escape.value.reason == "media_reference_owned_by_another_workspace"


def test_montage_http_rejects_client_narration_paths(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", production)
    client = TestClient(main_module.app)

    raw_create = client.post(
        "/api/production/batches/missing/montage-plans",
        json={"episode_id": "missing", "narration_audio_path": "/tmp/untrusted.wav"},
    )
    raw_render = client.post(
        "/api/production/montage-plans/missing/render",
        json={"narration_audio_path": "file:///tmp/untrusted.wav"},
    )
    logical_create = client.post(
        "/api/production/batches/missing/montage-plans",
        json={
            "episode_id": "missing",
            "narration_audio_path": "workspace://approved/narration.wav",
        },
    )

    assert raw_create.status_code == 422
    assert raw_render.status_code == 422
    assert logical_create.status_code == 422


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


def test_montage_http_persists_workspace_plan_and_usage_after_render(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    workspace_id = "workspace_a"
    episode_repository = EpisodeRepository(database_path=tmp_path / "episodes.sqlite3")
    episode = episode_repository.create(workspace_id=workspace_id)
    episode.status = EpisodeStatus.SCRIPT_APPROVED
    episode.script = ScriptPackage(
        id="script_montage",
        hook="人工智能工具正在改变团队效率。",
        sections=[ScriptSection("body", "方法", 8, "自动化处理重复工作，保留人工审核。")],
        title_options=["效率方法"],
        cover_copy="",
        publish_copy="",
        comment_prompt="",
    )
    episode_repository.save(episode)
    production.save_batch(
        ProductionBatch(
            id="batch_montage",
            workspace_id=workspace_id,
            name="测试批次",
            target_count=1,
            status="created",
        )
    )
    video = production.video_library_path / "demo.mp4"
    video.write_bytes(b"video")
    asset = production.scan_library(workspace_id)[0]
    production.analyze_asset(workspace_id, asset.id)
    production.update_asset_review(
        workspace_id,
        asset.id,
        review_status="approved",
        rights_confirmed=True,
        brand_safe=True,
        usage_role="b_roll",
    )
    for segment in production.get_asset(workspace_id, asset.id).segments:
        production.update_segment(
            workspace_id,
            asset.id,
            segment.id,
            description="人工智能工具自动化处理工作",
            transcript="",
            review_status="approved",
        )
    narration = production.video_library_path / "approved" / "narration.wav"
    narration.parent.mkdir()
    narration.write_bytes(b"audio")
    production.save_narration_artifact(
        NarrationArtifactRecord(
            id="narration_montage",
            workspace_id=workspace_id,
            episode_id=episode.id,
            script_id=episode.script.id,
            voice_profile_id="voice_montage",
            cache_key="cache_montage",
            audio_path=str(narration),
            duration_seconds=20.0,
            sample_rate=24000,
            model_id="voice-model",
        )
    )
    narration_reference = production.narration_artifact_reference("cache_montage")
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    narration_calls = []

    def narration_for_episode(workspace_id, selected_episode, profile=None):
        narration_calls.append((workspace_id, selected_episode.id))
        return NarrationArtifact(
            episode_id=selected_episode.id,
            audio_path=str(narration),
            duration_seconds=20.0,
            sample_rate=24000,
            model_id="voice-model",
            voice_profile_id="voice_montage",
            cache_key="cache_montage",
        )

    class Renderer:
        def audio_duration(self, audio_path: Path) -> float:
            assert audio_path == narration.resolve()
            return 20.0

        def render(self, plan, audio_path: Path):
            assert audio_path == narration.resolve()
            assert {clip.source_path for clip in plan.clips} == {str(video.resolve())}
            output = output_root / "output.mp4"
            subtitle = output_root / "output.srt"
            output.write_bytes(b"mp4")
            subtitle.write_text("subtitle", encoding="utf-8")
            plan.status = "rendered"
            plan.output_path = str(output)
            plan.subtitle_path = str(subtitle)
            plan.message = "rendered fixture"
            return plan

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "repo", episode_repository)
    monkeypatch.setattr(
        main_module, "montage_repo", main_module.MontageRepository(production.database_path)
    )
    monkeypatch.setattr(main_module, "montage_planner", main_module.MontagePlanner())
    monkeypatch.setattr(main_module, "montage_renderer", Renderer())
    monkeypatch.setattr(main_module, "_narration_for_episode", narration_for_episode)
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, video_output_path=str(output_root)),
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", workspace_id),
    )
    client = TestClient(main_module.app)

    created = client.post(
        "/api/production/batches/batch_montage/montage-plans",
        json={"episode_id": episode.id},
    )
    assert created.status_code == 200
    plan = created.json()
    assert narration_calls == [(workspace_id, episode.id)]
    assert plan["narration_status"] == "ready"
    assert plan["aspect_ratio"] == "9:16"
    assert not {"narration_audio_path", "output_path", "subtitle_path"} & plan.keys()
    assert plan["clips"]
    narration_response = client.post(f"/api/production/episodes/{episode.id}/narration")
    assert narration_response.status_code == 200
    assert narration_response.json()["audio_reference"] == narration_reference
    assert "audio_path" not in narration_response.json()
    confirmed = client.post(
        f"/api/production/montage-plans/{plan['id']}/confirm",
        json={"clip_ids": [clip["id"] for clip in plan["clips"]]},
    )
    assert confirmed.status_code == 200
    rendered = client.post(
        f"/api/production/montage-plans/{plan['id']}/render",
        json={},
    )

    assert rendered.status_code == 200
    assert rendered.json()["status"] == "rendered"
    assert rendered.json()["narration_status"] == "ready"
    assert rendered.json()["subtitle_status"] == "ready"
    assert rendered.json()["output_url"] == (
        f"/api/production/montage-plans/{plan['id']}/output"
    )
    assert rendered.json()["subtitle_url"] == (
        f"/api/production/montage-plans/{plan['id']}/subtitle"
    )
    assert not {"narration_audio_path", "output_path", "subtitle_path"} & rendered.json().keys()
    assert all(
        clip["source_path"] == "workspace://demo.mp4"
        for clip in rendered.json()["clips"]
    )
    assert client.get(rendered.json()["output_url"]).content == b"mp4"
    assert client.get(rendered.json()["subtitle_url"]).text == "subtitle"
    assert client.get(
        rendered.json()["output_url"],
        headers={"x-test-workspace": "workspace_b"},
    ).status_code == 404
    batch_response = client.get("/api/production/batches/batch_montage")
    assert batch_response.status_code == 200
    batch_item = batch_response.json()["items"][0]
    assert batch_item["output_url"] == rendered.json()["output_url"]
    assert not {"narration_audio_path", "output_path", "subtitle_path"} & batch_item.keys()
    reloaded_plan = main_module.MontageRepository(production.database_path).get(
        workspace_id, plan["id"]
    )
    reloaded_asset = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [],
    ).get_asset(workspace_id, asset.id)
    assert reloaded_plan.status == "rendered"
    assert all(clip.source_path == "workspace://demo.mp4" for clip in reloaded_plan.clips)
    used_segment_ids = {clip["segment_id"] for clip in plan["clips"]}
    assert all(
        segment.usage_count > 0
        for segment in reloaded_asset.segments
        if segment.id in used_segment_ids
    )
    assert client.get(
        f"/api/production/montage-plans/{plan['id']}",
        headers={"x-test-workspace": "workspace_b"},
    ).status_code == 404
    reloaded_plan.output_path = str(tmp_path / "outside.mp4")
    reloaded_plan.subtitle_path = None
    montage = main_module.MontageRepository(production.database_path)
    montage.save(reloaded_plan)
    assert client.get(f"/api/production/montage-plans/{plan['id']}/output").status_code == 404
    assert client.get(f"/api/production/montage-plans/{plan['id']}/subtitle").status_code == 404


def test_narration_http_returns_structured_language_failure(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    episodes = EpisodeRepository(database_path=tmp_path / "language-episodes.sqlite3")
    episode = episodes.create(workspace_id="workspace_a")
    episode.status = EpisodeStatus.SCRIPT_APPROVED
    episode.script = ScriptPackage(
        id="script_language",
        hook="开头。",
        sections=[ScriptSection("body", "正文", 8, "正文。")],
        title_options=[],
        cover_copy="",
        publish_copy="",
        comment_prompt="",
    )
    reference = production.video_library_path / "approved" / "voice.wav"
    reference.parent.mkdir()
    reference.write_bytes(b"voice")
    voice = _save_versioned_voice(
        production,
        workspace_id="workspace_a",
        voice_id="voice-english",
        version="voice-v1",
        reference_audio_path="workspace://approved/voice.wav",
        language="en",
    )
    production.select_episode_versioned_voice(
        "workspace_a", episode.id, voice.id, voice.version
    )

    class Generator:
        def cache_key(self, **_kwargs):
            return "language-cache"

        def generate(self, **_kwargs):
            raise VoiceCloneError(
                MediaRuntimeFailure(
                    reason="language_unavailable",
                    impact="当前模型不支持英语配音。",
                    repair_action="选择支持英语的声音模型或中文声音版本。",
                    retryable=False,
                )
            )

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "repo", episodes)
    monkeypatch.setattr(main_module, "narration_generator", Generator())
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace_a")
    client = TestClient(main_module.app)

    response = client.post(f"/api/production/episodes/{episode.id}/narration")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "language_unavailable",
        "impact": "当前模型不支持英语配音。",
        "repair_action": "选择支持英语的声音模型或中文声音版本。",
        "retryable": False,
    }


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


def test_narration_generation_reuses_persisted_artifact(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    episodes = EpisodeRepository(database_path=tmp_path / "cache-episodes.sqlite3")
    episode = episodes.create(workspace_id="workspace_a")
    episode.status = EpisodeStatus.SCRIPT_APPROVED
    episode.script = ScriptPackage(
        id="script_cache",
        hook="开头。",
        sections=[ScriptSection("body", "正文", 8, "正文。")],
        title_options=[],
        cover_copy="",
        publish_copy="",
        comment_prompt="",
    )
    reference = production.video_library_path / "approved" / "cache-voice.wav"
    reference.parent.mkdir()
    reference.write_bytes(b"voice")
    voice_profile = _save_versioned_voice(
        production,
        workspace_id="workspace_a",
        voice_id="voice_cache",
        version="voice-v1",
        reference_audio_path="workspace://approved/cache-voice.wav",
    )
    production.select_episode_versioned_voice(
        "workspace_a", episode.id, voice_profile.id, voice_profile.version
    )
    profile = ProductionProfile(workspace_id="workspace_a")

    class Generator:
        calls = 0
        cache_references: list[str] = []
        resolved_references: list[Path] = []

        def cache_key(self, **kwargs):
            self.cache_references.append(kwargs["voice_identity"].reference_audio_path)
            return "stable-cache-key"

        def generate(self, **kwargs):
            self.calls += 1
            self.resolved_references.append(kwargs["resolved_reference_audio_path"])
            output = tmp_path / "cached.wav"
            output.write_bytes(b"audio")
            return NarrationArtifact(
                episode_id=episode.id,
                audio_path=str(output),
                duration_seconds=8.0,
                sample_rate=24000,
                model_id="voice-model",
                cache_key="stable-cache-key",
            )

    generator = Generator()
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "narration_generator", generator)

    first = main_module._narration_for_episode("workspace_a", episode, profile)
    second = main_module._narration_for_episode("workspace_a", episode, profile)

    assert first.audio_path == second.audio_path
    assert generator.calls == 1
    assert generator.cache_references == ["workspace://approved/cache-voice.wav"] * 2
    assert generator.resolved_references == [reference.resolve()]
    assert voice_profile.reference_audio_path == "workspace://approved/cache-voice.wav"


def test_narration_rejects_reference_outside_authorized_root_before_provider(
    tmp_path,
    monkeypatch,
):
    production = _repository(tmp_path)
    episodes = EpisodeRepository(database_path=tmp_path / "outside-episodes.sqlite3")
    episode = episodes.create(workspace_id="workspace_a")
    episode.status = EpisodeStatus.SCRIPT_APPROVED
    episode.script = ScriptPackage(
        id="script_outside",
        hook="开头。",
        sections=[ScriptSection("body", "正文", 8, "正文。")],
        title_options=[],
        cover_copy="",
        publish_copy="",
        comment_prompt="",
    )
    outside_reference = tmp_path / "outside.wav"
    outside_reference.write_bytes(b"voice")
    class Generator:
        calls = 0

        def cache_key(self, **_kwargs):
            return "outside-cache-key"

        def generate(self, **_kwargs):
            self.calls += 1
            raise AssertionError("provider must not receive an out-of-root reference")

    generator = Generator()
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "narration_generator", generator)

    with pytest.raises(MediaAuthorityError) as failure:
        _save_versioned_voice(
            production,
            workspace_id="workspace_a",
            voice_id="voice_outside",
            version="voice-v1",
            reference_audio_path=str(outside_reference),
        )

    assert failure.value.reason == "logical_media_reference_required"
    assert generator.calls == 0


def test_material_tags_collections_usage_and_gap_insights(tmp_path):
    production = _repository(tmp_path)
    video = production.video_library_path / "office_laptop_typing.mp4"
    video.write_bytes(b"video")
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


def test_material_media_and_batch_detail_are_workspace_scoped(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    video = production.video_library_path / "preview.mp4"
    video.write_bytes(b"video preview bytes")
    asset = production.scan_library(LEGACY_WORKSPACE_ID)[0]
    batch = production.create_batch(LEGACY_WORKSPACE_ID, name="Review batch", target_count=2)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, video_inbox_path=str(production.video_library_path)),
    )
    client = TestClient(main_module.app)

    detail = client.get(f"/api/production/materials/{asset.id}")
    assert detail.status_code == 200
    assert detail.json()["file_name"] == "preview.mp4"

    media = client.get(f"/api/production/materials/{asset.id}/media")
    assert media.status_code == 200
    assert media.content == b"video preview bytes"
    assert media.headers["content-type"].startswith("video/mp4")

    batch_detail = client.get(f"/api/production/batches/{batch.id}")
    assert batch_detail.status_code == 200
    assert batch_detail.json()["name"] == "Review batch"


def test_material_library_endpoint_is_paginated(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    for index in range(3):
        (production.video_library_path / f"clip-{index}.mp4").write_bytes(b"video")
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
