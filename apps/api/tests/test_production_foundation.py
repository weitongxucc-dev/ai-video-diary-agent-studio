from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.model_runtime import ModelCatalog
from app.production import ProductionProfile, ProductionRepository
from app.repository import LEGACY_WORKSPACE_ID
from app.visual_semantics import VisualEmbedding


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

    reference = tmp_path / "voice.wav"
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


def test_production_api_exposes_foundation_and_gated_batch(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "model_catalog", _missing_model_catalog(tmp_path))
    client = TestClient(main_module.app)

    foundation = client.get("/api/production/foundation")
    assert foundation.status_code == 200
    assert foundation.json()["readiness"]["ready"] is False

    created = client.post(
        "/api/production/batches",
        json={"name": "API batch", "target_count": 10},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "blocked"
    assert "voice_model_not_ready" in created.json()["blocker_codes"]

    listed = client.get("/api/production/batches")
    assert listed.status_code == 200
    assert listed.json()["total_count"] == 1


def test_profile_api_cannot_claim_voice_model_is_ready(tmp_path, monkeypatch):
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

    assert response.status_code == 200
    assert response.json()["voice_model_status"] == "blocked"
    assert response.json()["voice_model_message"] != "client says ready"


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
