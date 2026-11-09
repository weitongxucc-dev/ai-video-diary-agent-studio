"""Material value contracts preserve real schema/JSON consumers without loading analyzers."""

from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import textwrap


def test_material_contract_import_and_construction_do_not_load_runtime_adapters():
    script = textwrap.dedent("""
        from dataclasses import asdict
        from datetime import datetime, timezone
        import importlib.abc
        from pathlib import Path
        import sys

        class RejectRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = {
                    'app.production', 'app.main', 'app.repository', 'app.asr',
                    'app.visual_understanding', 'app.material_speech', 'app.model_runtime',
                    'app.material_scene', 'app.material_probe', 'app.material_ingestion',
                    'app.avatar_lipsync', 'app.voice_clone',
                }
                if fullname in blocked:
                    raise AssertionError(f'Material contract imported runtime: {fullname}')

        def reject_side_effects(event, args):
            if event in {'sqlite3.connect', 'socket.connect', 'subprocess.Popen'}:
                raise AssertionError(f'Material contract started runtime effect: {event}')

        sys.meta_path.insert(0, RejectRuntime())
        sys.addaudithook(reject_side_effects)
        Path.rglob = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Vault scan forbidden'))
        from app.materials.contracts import (
            MaterialAsset, MaterialSegment, material_allows_footage, effective_segment_usage_role,
        )
        segment = MaterialSegment('segment', 'asset', 0, 1)
        asset = MaterialAsset('asset', 'workspace-a', 'fixture.mp4', 'fixture://media',
            1, datetime(2026, 10, 14, tzinfo=timezone.utc), segments=[segment])
        assert asset.speech_analysis is None and segment.understanding is None
        assert asdict(asset)['segments'][0]['id'] == 'segment'
        assert material_allows_footage(asset)
        assert effective_segment_usage_role(asset, segment) == 'unassigned'
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_exports_reference_one_material_contract_implementation():
    from app import production
    from app.materials import contracts

    for name in (
        "AssetReviewStatus", "ContentVisibility", "AssetUsageRole", "SuggestedUsageRole",
        "AnalysisStatus", "MaterialMediaType", "MATERIAL_MEDIA_BY_SUFFIX",
        "BUSINESS_OBJECT_KINDS", "ContentUseState", "MaterialSegment", "MaterialAsset",
        "MaterialAssetNote", "MaterialBusinessObject", "MaterialCollection", "MaterialSegmentMatch",
        "material_media_type_for", "material_media_type_label", "material_allows_footage",
        "material_allows_automation", "effective_segment_usage_role",
    ):
        assert getattr(production, name) is getattr(contracts, name)


def test_material_defaults_and_date_factories_keep_existing_value_semantics():
    from app.materials import contracts
    from app.models import utc_now

    when = datetime(2026, 10, 14, tzinfo=timezone.utc)
    first = contracts.MaterialAsset("first", "workspace-a", "fixture.mp4", "fixture://media", 1, when)
    second = contracts.MaterialAsset("second", "workspace-a", "fixture.mp4", "fixture://media", 1, when)
    assert (first.media_type, first.visibility, first.use_state, first.owner_user_id) == (
        "video", "private", "available", None,
    )
    first.user_tags.append("用户标签")
    assert second.user_tags == []
    first.analysis_status = 'ready'
    segment = contracts.MaterialSegment('segment', first.id, 0, 1)
    qualify = contracts.material_usage_eligibility
    assert qualify(first, segment, purpose='automatic').eligible  # raw is not excluded.
    first.use_state = 'not_for_automation'
    assert qualify(first, segment, purpose='manual').eligible
    assert qualify(first, segment, purpose='automatic').status == 'restricted'
    first.use_state = 'reference_only'
    assert qualify(first, segment, purpose='reference').eligible
    assert qualify(first, segment, purpose='manual').status == 'restricted'
    assert qualify(first, segment, purpose='reference', expected_asset_version=when).status == 'input_changed'
    assert qualify(None, purpose='manual', readable=False).status == 'unreadable'
    assert qualify(None, purpose='manual').status == 'not_found'
    first.use_state, first.analysis_status = 'available', 'pending'
    assert qualify(first, segment, purpose='automatic').status == 'processing'
    assert qualify(first, segment, purpose='reference').eligible
    for value_type in (contracts.MaterialAsset, contracts.MaterialSegment, contracts.MaterialCollection):
        defaults = {field.name: field.default_factory for field in fields(value_type)}
        assert defaults["created_at"] is defaults["updated_at"] is utc_now
    assert first.created_at.tzinfo is timezone.utc
    assert asdict(contracts.MaterialAssetNote("first")) == {
        "asset_id": "first", "user_description": "", "upload_context": "",
    }
    match = contracts.MaterialSegmentMatch("asset://first", "segment://first", 0.5, ["测试依据"])
    assert (match.rank, match.match_tier, match.matched_terms, match.tags) == (0, "structural", [], [])


def test_material_observations_round_trip_through_actual_decoder_json_and_response_schema():
    from app import production
    from app.asr import SubtitleSegment
    from app.material_speech import MaterialSpeechAnalysis
    from app.materials.contracts import MaterialAsset, MaterialSegment
    from app.schemas import MaterialAssetResponse, MaterialSegmentResponse
    from app.visual_understanding import SegmentUnderstanding

    when = datetime(2026, 10, 14, tzinfo=timezone.utc)
    understanding = SegmentUnderstanding(
        status="ready", model_id="fixture-only", sample_times=[0.5], summary="测试画面",
    )
    speech = MaterialSpeechAnalysis(
        status="ready", source_fingerprint="fixture", model_id="fixture-only",
        observations=[SubtitleSegment(0, 1000, "测试原文")],
    )
    segment = MaterialSegment(
        "segment", "asset", 0, 1, understanding=understanding, system_tags=["主体:人物"],
        created_at=when, updated_at=when,
    )
    asset = MaterialAsset(
        "asset", "workspace-a", "fixture.mp4", "fixture://media", 1, when,
        segments=[segment], speech_analysis=speech, owner_user_id="uploader-a",
        user_description="用户的说明", created_at=when, updated_at=when,
    )
    encoded = json.dumps(asdict(asset), ensure_ascii=False, default=production._json_default)
    decoded = production.ProductionRepository._asset_from_payload(json.loads(encoded))
    assert type(decoded) is MaterialAsset
    assert type(decoded.segments[0]) is MaterialSegment
    assert type(decoded.speech_analysis) is MaterialSpeechAnalysis
    assert type(decoded.speech_analysis.observations[0]) is SubtitleSegment
    assert type(decoded.segments[0].understanding) is SegmentUnderstanding
    assert asdict(decoded) == asdict(asset)
    assert json.dumps(asdict(decoded), ensure_ascii=False, default=production._json_default) == encoded

    response = MaterialAssetResponse.model_validate(decoded, from_attributes=True).model_dump(mode="json")
    segment_response = MaterialSegmentResponse.model_validate(decoded.segments[0], from_attributes=True)
    assert response["segments"][0] == segment_response.model_dump(mode="json")
    assert response["speech_analysis"] == {
        "status": "ready", "source_fingerprint": "fixture", "model_id": "fixture-only",
        "observations": [{"start_ms": 0, "end_ms": 1000, "text": "测试原文"}], "message": "",
    }
    assert response["segments"][0]["understanding"]["summary"] == "测试画面"
    assert response["modified_at"] == "2026-10-14T00:00:00Z"
    assert (response["visibility"], response["use_state"], response["user_description"]) == (
        "private", "available", "用户的说明",
    )
