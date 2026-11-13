"""Public analysis/SQLite checks using explicit synthetic execution boundaries."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.materials.analysis import MaterialAnalysisUseCase
from app.materials.assets import MaterialAssetStore
from app.materials.data_governance import KnowledgeClaim, SemanticFieldDefinition
from app.materials.source_files import MaterialSourceFileAccess
from app.media_observations import MaterialSpeechAnalysis, SegmentUnderstanding, SubtitleSegment
from app.production import ProductionRepository
from app.source_repository import SourceRepository
from app.source_types import KnowledgeParameter, SourceConnector, SourceItem
from app.visual_semantics import VisualEmbedding, VisualSemanticError


class RecordingVisualProvider:
    model_id = "synthetic-visual-boundary"
    ready = True
    message = "synthetic model boundary"

    def __init__(self):
        self.segment_calls = []
        self.text_calls = []

    def embed_segments(self, path, segments):
        self.segment_calls.append((path, tuple(segments)))
        return [VisualEmbedding(segment.segment_id, [1.0, 0.0], 3) for segment in segments]

    def embed_texts(self, texts):
        self.text_calls.append(tuple(texts))
        return [[1.0, 0.0] if index < 3 else [0.0, 1.0] for index, _ in enumerate(texts)]


class RecordingSpeechAnalyzer:
    def __init__(self):
        self.calls = []

    def analyze(self, path, duration, previous, fingerprint):
        self.calls.append((path, duration, previous, fingerprint))
        return MaterialSpeechAnalysis(
            "ready", source_fingerprint=fingerprint or "", model_id="synthetic-speech-boundary",
            observations=[SubtitleSegment(0, 1000, "合成声音观察")], message="合成声音边界结果。",
        )


def case(tmp_path, *, provider=None, speech=None, understanding=None):
    library = tmp_path / "library"
    library.mkdir()
    source = library / "source.mp4"
    source.write_bytes(b"opaque synthetic source; no decoder")
    repo = ProductionRepository(
        tmp_path / "analysis.sqlite3", library,
        duration_reader=lambda _path: 8.0, scene_reader=lambda _path, _duration: [4.0],
        visual_provider=provider, speech_analyzer=speech, visual_understanding=understanding,
    )
    repo.material_source_files.bind_material_ingestion("workspace", expected_directory=str(library))
    repo.material_source_files.confirm_material_copy_complete(
        "workspace", relative_path=source.name,
        expected_revision=repo.material_source_files.material_copy_revision(source.stat()), confirmation_source="manual",
    )
    return repo, source, repo.material_intake.scan_library("workspace")[0]


def asset_row(repo, asset_id):
    with repo._connect() as conn:
        return tuple(conn.execute("SELECT * FROM material_assets WHERE workspace_id=? AND id=?", (
            "workspace", asset_id,
        )).fetchone())


def test_analysis_and_source_construction_bind_without_io_or_execution(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("construction must not open SQLite or invoke execution ports")

    root = tmp_path / "not-created"
    assets = MaterialAssetStore(forbidden)
    sources = MaterialSourceFileAccess(forbidden, root)
    visual, speech, understanding = object(), object(), object()
    use_case = MaterialAnalysisUseCase(
        assets, sources, connection_factory=forbidden,
        duration_reader=forbidden, scene_reader=forbidden,
        visual_provider=visual, speech_analyzer=speech, visual_understanding=understanding,
    )
    assert use_case.assets is assets and use_case.source_files is sources
    assert use_case.visual_provider is visual and use_case.speech_analyzer is speech
    assert use_case.visual_understanding is understanding
    assert use_case._usage_role_prompt_vectors is None
    assert not root.exists()


def test_public_analysis_keeps_model_bindings_prompt_cache_and_concurrent_manual_facts(tmp_path):
    provider, speech = RecordingVisualProvider(), RecordingSpeechAnalyzer()
    repo, source, asset = case(tmp_path, provider=provider, speech=speech)
    use_case = repo._material_analysis
    assert use_case.visual_provider is provider and use_case.speech_analyzer is speech
    assert use_case.assets is repo._material_assets and use_case.source_files is repo._material_source_files
    first = repo.analyze_asset("workspace", asset.id)
    second = repo.analyze_asset("workspace", asset.id)
    assert first.analysis_status == second.analysis_status == "ready"
    assert len(provider.segment_calls) == len(provider.text_calls) == 1
    assert "复用 2 个" in second.analysis_message
    assert repo.get_visual_feature("workspace", first.segments[0].id, provider.model_id) == [1.0, 0.0]
    repo.update_asset_review("workspace", asset.id, review_status="approved", usage_role="b_roll")
    prompt_cache = use_case._usage_role_prompt_vectors
    visual_calls = []

    def understand(path, segments, fingerprint):
        visual_calls.append((path, tuple(segments), fingerprint))
        repo.update_segment(
            "workspace", asset.id, segments[0].id, description="处理中人工纠正", transcript="人工文字",
            user_tags=["人工确认"], review_status="approved", usage_role="b_roll",
        )
        return {segment.id: SegmentUnderstanding("ready", summary="合成画面观察") for segment in segments}

    understanding = SimpleNamespace(analyze=understand)
    repo.visual_understanding = understanding
    durations = []

    def duration(path):
        durations.append(path)
        return 8.0

    repo.duration_reader = duration
    result = repo.analyze_asset("workspace", asset.id)
    assert repo._material_analysis is use_case
    assert use_case.visual_understanding is understanding and use_case.duration_reader is duration
    assert use_case._usage_role_prompt_vectors is prompt_cache
    assert len(provider.segment_calls) == len(provider.text_calls) == 1
    assert durations == [source]
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    assert [item[3] for item in speech.calls] == [None, None, fingerprint]
    assert visual_calls[0][2] == fingerprint
    segment = result.segments[0]
    assert segment.description == "处理中人工纠正" and segment.transcript == "人工文字"
    assert segment.user_tags == ["人工确认"] and segment.review_status == "approved"
    assert segment.usage_role == "b_roll" and segment.understanding.summary == "合成画面观察"
    restored = repo.get_asset("workspace", asset.id)
    assert restored.segments[0].description == segment.description
    assert restored.speech_analysis.observations[0].text == "合成声音观察"


def test_analysis_run_records_stages_input_revision_and_expiry(tmp_path):
    provider, speech = RecordingVisualProvider(), RecordingSpeechAnalyzer()
    repo, source, asset = case(tmp_path, provider=provider, speech=speech)

    repo.analyze_asset("workspace", asset.id)
    first = repo.list_material_analysis_runs("workspace", asset.id)[0]
    assert first.status == "partial"
    assert first.source_revision["size_bytes"] == str(source.stat().st_size)
    assert first.stages["media_probe"]["status"] == "succeeded"
    assert first.stages["speech"]["status"] == "succeeded"
    assert first.stages["visual_understanding"]["status"] == "skipped"
    assert first.stages["visual_embeddings"]["status"] == "succeeded"

    source.write_bytes(b"a newer synthetic source revision")
    repo.material_source_files.confirm_material_copy_complete(
        "workspace",
        relative_path=source.name,
        expected_revision=repo.material_source_files.material_copy_revision(source.stat()),
        confirmation_source="manual",
    )
    repo.analyze_asset("workspace", asset.id)
    runs = repo.list_material_analysis_runs("workspace", asset.id)
    assert len(runs) == 2
    assert {run.status for run in runs} == {"expired", "partial"}
    assert any(item.relation == "preserved" for item in repo.material_data_governance.list_segment_lineage("workspace", asset.id))


def test_analysis_persists_evidence_candidate_assertions_and_indexes(tmp_path):
    provider, speech = RecordingVisualProvider(), RecordingSpeechAnalyzer()

    class Understanding:
        def analyze(self, path, segments, fingerprint):
            return {
                segment.id: SegmentUnderstanding(
                    "ready",
                    fingerprint=fingerprint or "",
                    model_id="synthetic-understanding",
                    sample_times=[segment.start_seconds, segment.end_seconds],
                    summary="合成画面观察",
                    facts=[{"kind": "object", "text": "一辆自行车", "confidence": 0.91}],
                    ocr_status="ready",
                    ocr_model_id="synthetic-ocr",
                )
                for segment in segments
            }

    repo, source, asset = case(
        tmp_path,
        provider=provider,
        speech=speech,
        understanding=Understanding(),
    )
    repo.analyze_asset("workspace", asset.id)

    run = repo.list_material_analysis_runs("workspace", asset.id)[0]
    assert run.source_digest == hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = repo.list_material_evidence("workspace", asset.id)
    assertions = repo.list_material_assertions("workspace", asset.id)
    indexes = repo.list_material_indexes("workspace", asset.id)
    assert len(evidence) == 3
    assert all(item.source_revision["size_bytes"] == str(source.stat().st_size) for item in evidence)
    assert len(assertions) == 2
    assert {item.field_ref for item in assertions} == {"object"}
    assert {item.value for item in assertions} == {"一辆自行车"}
    assert all(item.status == "candidate" for item in assertions)
    assert all(item.origin == "model_inference" for item in assertions)
    confirmed = repo.material_data_governance.transition_assertion(
        "workspace", asset.id, assertions[0].id, status="confirmed", expected_status="candidate"
    )
    assert confirmed.status == "confirmed"
    assert set(item.index_type for item in indexes) == {"visual_features", "material_search"}
    assert all(item.status == "ready" for item in indexes)

    source.write_bytes(b"changed after the first analysis")
    repo.material_source_files.confirm_material_copy_complete(
        "workspace",
        relative_path=source.name,
        expected_revision=repo.material_source_files.material_copy_revision(source.stat()),
        confirmation_source="manual",
    )
    repo.duration_reader = lambda _path: (_ for _ in ()).throw(ValueError("synthetic failure"))
    failed = repo.analyze_asset("workspace", asset.id)
    assert failed.analysis_status == "failed"
    stale_indexes = repo.list_material_indexes("workspace", asset.id)
    assert {item.status for item in stale_indexes} == {"stale"}
    assert all(item.invalid_reason for item in stale_indexes)


def test_data_governance_field_definition_and_knowledge_claim_lifecycle(tmp_path):
    repo, _source, _asset = case(tmp_path)
    field = repo.material_data_governance.upsert_field_definition(
        SemanticFieldDefinition(
            id="field-capacity-v1",
            workspace_id="workspace",
            canonical_id="product.capacity",
            display_name="产能",
            value_type="number",
            unit_family="throughput",
            allowed_units=("件/分钟", "件/小时"),
            aliases=("速度",),
            applies_to=("product",),
        )
    )
    assert field.value_type == "number"
    assert repo.list_semantic_field_definitions("workspace")[0].canonical_id == "product.capacity"

    claim = repo.record_knowledge_claim(
        KnowledgeClaim(
            id="claim-capacity-1",
            workspace_id="workspace",
            knowledge_item_id="knowledge-1",
            knowledge_version="2",
            field_ref="product.capacity",
            value=120,
            unit="件/分钟",
            evidence_refs=({"locator": "第2页", "source_ref": "source-1"},),
            origin="business_fact",
            confidence=0.98,
            asserted_by="member-1",
        )
    )
    assert claim.status == "candidate"
    confirmed = repo.transition_knowledge_claim(
        "workspace", claim.id, status="confirmed", expected_status="candidate"
    )
    assert confirmed.status == "confirmed"
    assert repo.list_knowledge_claims("workspace", "knowledge-1", knowledge_version="2")[0].unit == "件/分钟"


def test_parsed_knowledge_parameters_become_candidate_claims(tmp_path):
    repo, _source, _asset = case(tmp_path)
    sources = SourceRepository(
        tmp_path / "analysis.sqlite3",
        governance_store=repo.material_data_governance,
    )
    sources.upsert_connector(
        SourceConnector(
            id="connector-files",
            workspace_id="workspace",
            platform="knowledge_files",
            protocol="local_file",
            account_or_rule="用户提供",
            frequency="on_demand",
            permissions=("read:source_items",),
        )
    )
    source = SourceItem(
        id="source-parameters",
        workspace_id="workspace",
        connector_id="connector-files",
        source_label="file_import",
        source_url=None,
        content_type="text/markdown",
        author=None,
        published_at=None,
        body="产能：120",
        subtitles=None,
        media_references=(),
        license="user_provided",
        title="参数资料",
        file_reference="parameters.md",
        content_hash="f" * 64,
        parameters=(KnowledgeParameter("capacity", "120", "件/分钟", "第1段"),),
    )
    sources.save_source_items("workspace", [source])
    item = sources.save_source_item_to_knowledge("workspace", source.id, verified_owner_user_id="member-1")
    claims = repo.list_knowledge_claims("workspace", item.id, knowledge_version="1")
    assert len(claims) == 1
    assert claims[0].field_ref == "knowledge.parameter.capacity"
    assert claims[0].status == "candidate"
    assert claims[0].evidence_refs[0]["locator"] == "第1段"


def test_public_analysis_preserves_copy_and_final_digest_rejection_without_asset_publish(tmp_path):
    repo, source, asset = case(tmp_path)
    calls = []

    def duration(path):
        calls.append(path)
        return 8.0

    repo.duration_reader = duration
    assert repo.material_source_files.get_material_ingestion_binding() == ("workspace", str(source.parent))
    assert repo.material_source_files.material_copy_revision is MaterialSourceFileAccess.material_copy_revision
    with pytest.raises(ValueError, match="不在配置的素材库"):
        repo._validated_library_file(str(tmp_path / "outside.mp4"))
    source.write_bytes(b"a new unconfirmed synthetic revision")
    before = asset_row(repo, asset.id)
    with pytest.raises(ValueError, match="先确认"):
        repo.analyze_asset("workspace", asset.id)
    assert calls == [] and asset_row(repo, asset.id) == before
    repo.material_source_files.confirm_material_copy_complete(
        "workspace", relative_path=source.name,
        expected_revision=repo.material_source_files.material_copy_revision(source.stat()), confirmation_source="manual",
    )
    with pytest.raises(ValueError, match="原片内容发生变化"):
        repo.analyze_asset("workspace", asset.id, expected_source_digest="0" * 64)
    assert calls == [source] and asset_row(repo, asset.id) == before


def test_public_analysis_preserves_inner_model_failure_and_outer_public_error_state(tmp_path):
    class FailedVisualProvider(RecordingVisualProvider):
        def embed_segments(self, path, segments):
            raise VisualSemanticError("synthetic model boundary failed")

    repo, _source, asset = case(tmp_path, provider=FailedVisualProvider())
    inner_failure = repo.analyze_asset("workspace", asset.id)
    assert inner_failure.analysis_status == "ready"
    assert all(segment.visual_status == "failed" and segment.visual_sample_count == 0 for segment in inner_failure.segments)
    assert "synthetic model boundary failed" in inner_failure.analysis_message

    def bad_duration(path):
        raise ValueError("synthetic-private-error-detail")

    repo.duration_reader = bad_duration
    outer_failure = repo.analyze_asset("workspace", asset.id)
    assert outer_failure.analysis_status == "failed"
    assert outer_failure.analysis_message == "镜头分析失败：媒体格式或内容无效。检查后可手动重试。"
    assert "synthetic-private-error-detail" not in json.dumps(repo.get_asset("workspace", asset.id).analysis_message)
