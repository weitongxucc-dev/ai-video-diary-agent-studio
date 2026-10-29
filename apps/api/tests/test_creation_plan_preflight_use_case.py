"""Metadata boundary check; material/model facts below are declared, not executed."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException, Request
import pytest

from app import main
from app.creation.video_template_routes import VideoTemplateHTTPAdapter
from app.creation.plan_preflight_use_case import CreationPlanPreflightUseCase
from app.source_types import source_item_identity
from app.materials.contracts import MaterialSegmentMatch
from app.production import ProductionRepository, VideoTemplate
from app.schemas import CreationPlanConfirmRequest, CreationPlanPreviewRequest
from app.source_repository import SourceRepository
from app.source_types import KnowledgeItem, SourceConnector, SourceItem


def test_metadata_preflight_template_pins_scope_and_lazy_provider(tmp_path, monkeypatch):
    workspace, reader = "metadata-workspace", "metadata-reader"
    database = tmp_path / "metadata.sqlite3"
    production = ProductionRepository(database, tmp_path / "unused-library")
    sources = SourceRepository(database)
    fixed_time = datetime(2026, 10, 10, tzinfo=timezone.utc)
    source = SourceItem(id="source-fixed", workspace_id=workspace, connector_id="connector-fixed",
        source_label="metadata", source_url=None, content_type="text", author="作者",
        published_at=None, body="原始事实", subtitles=None, media_references=(),
        license="user_owned", title="来源事实", created_at=fixed_time)
    for connector_id in ("connector-fixed", "connector-outside"):
        sources.upsert_connector(SourceConnector(id=connector_id, workspace_id=workspace,
            platform="manual", protocol="metadata", account_or_rule="declared",
            frequency="manual", permissions=("read",), created_at=fixed_time))
    sources.save_source_items(workspace, [source, SourceItem(
        **{**asdict(source), "id": "source-outside", "connector_id": "connector-outside"})])
    for item_id in ("knowledge-fixed", "knowledge-outside"):
        sources.create_knowledge_item_owned(KnowledgeItem(id=item_id, workspace_id=workspace,
            content_type="text", title="固定资料", body="固定版本正文", file_reference=None,
            authorization="authorized", ownership="user_owned", source_item_id=source.id,
            version="1", created_at=fixed_time), verified_user_id=reader)
    current = sources.edit_knowledge_item(workspace, "knowledge-fixed", expected_version="1",
        title="当前新版本", body="新版本正文", verified_user_id=reader)
    assert current.version != "1"
    template = production.save_video_template(VideoTemplate(id="template-fixed", workspace_id=workspace,
        version=1, name="metadata template", origin="user", settings={"language": "zh-CN",
            "aspect_ratio": "16:9", "duration_target_seconds": 30, "presenter_share_target": 0}),
        verified_owner_user_id=reader)
    scope = {"knowledge_ids": ["knowledge-fixed"], "connector_ids": ["connector-fixed"],
        "material_asset_ids": ["asset-allowed"]}
    trace, inputs, invocations = [], [], []

    # These ports declare qualified metadata. No file/media eligibility is claimed.
    def material_facts(ws, **kwargs):
        trace.append("materials")
        assert ws == workspace and kwargs == {"viewer_user_id": reader, "local_diagnostic": False}
        return [(SimpleNamespace(id=asset, usage_role="b_roll"),
                 SimpleNamespace(id=segment, usage_role="unassigned")) for asset, segment in
                [("asset-allowed", "segment-allowed"), ("asset-outside", "segment-outside")]]

    match = MaterialSegmentMatch("asset:asset-allowed", "segment:segment-allowed", 1.0, ["declared metadata"])

    def search_facts(ws, **kwargs):
        trace.append("search")
        assert ws == workspace
        assert kwargs == {"query": "核对事实", "asset_ids": ["asset-allowed"],
            "eligible_segment_refs": {("asset-allowed", "segment-allowed")},
            "usage_roles": ["a_roll", "b_roll"], "limit": 8,
            "viewer_user_id": reader, "local_diagnostic": False}
        return [match, MaterialSegmentMatch("asset:asset-allowed", "segment:outside", 1.0, []),
            MaterialSegmentMatch("asset:asset-outside", "segment:segment-outside", 1.0, [])]

    class CatalogFacts:
        def list_statuses(self):
            trace.append("catalog")
            return [SimpleNamespace(model=SimpleNamespace(id="declared-model", capability="topic"),
                state="unavailable", ready=False, message="metadata only")]

    class ProviderFacts:
        model_id, message = "declared-lipsync", "metadata unavailable"

        @property
        def ready(self):
            trace.append("provider.ready")
            return False

    def lazy_provider(quality):
        trace.append("provider:" + quality)
        return ProviderFacts()

    def trusted_reader(request, ws):
        trace.append("reader")
        assert ws == workspace
        return reader, False

    resolver = VideoTemplateHTTPAdapter.read_template

    def observe_resolver(adapter, *args):
        trace.append("template")
        return resolver(adapter, *args)

    prepare = CreationPlanPreflightUseCase.prepare

    def observe_prepare(case, ws, payload, **kwargs):
        trace.append("prepare")
        assert isinstance(payload.provided_fields, frozenset)
        inputs.append(payload)
        assert case.production is production and case.sources is sources
        return prepare(case, ws, payload, **kwargs)

    def observe_read(obj, name, label):
        original = getattr(obj, name)

        def read(*args, **kwargs):
            trace.append(label)
            return original(*args, **kwargs)

        monkeypatch.setattr(obj, name, read)

    monkeypatch.setattr(main, "production_repo", production)
    monkeypatch.setattr(main, "source_repository", sources)
    monkeypatch.setattr(main, "model_catalog", CatalogFacts())
    monkeypatch.setattr(main, "_lipsync_provider", lazy_provider)
    monkeypatch.setattr(main, "_content_reader", trusted_reader)
    monkeypatch.setattr(VideoTemplateHTTPAdapter, "read_template", observe_resolver)
    monkeypatch.setattr(CreationPlanPreflightUseCase, "prepare", observe_prepare)
    monkeypatch.setattr(production, "automatic_material_segments", material_facts)
    monkeypatch.setattr(production.material_search, "search_material_segments", search_facts)
    for obj, name, label in [(sources, "list_knowledge_items_for_use", "knowledge.list"),
        (sources, "get_knowledge_reference_readable_by", "knowledge.pin"),
        (sources, "list_source_items_for_use", "sources"),
        (production, "get_versioned_voice_profile", "voice.pin"),
        (production, "creation_plan_person_reference", "person.pin"),
        (production, "get_default_versioned_voice", "voice.default")]:
        observe_read(obj, name, label)
    request = Request({"type": "http", "method": "POST", "path": "/metadata", "headers": []})
    with pytest.raises(HTTPException) as rejected:
        main._creation_plan_preflight(request, workspace,
            CreationPlanPreviewRequest(goal="核对事实", template_id=template.id))
    assert rejected.value.status_code == 422 and rejected.value.detail == "请选择明确的模板版本。"
    assert trace == [] and inputs == []
    invocations.append({"kind": "incomplete_template", "status": 422, "trace": list(trace)})
    payload = {"goal": " 核对事实 ", "name": " 固定名称 ", "target_count": 2,
        "template_id": template.id, "template_version": 1,
        "selected_knowledge": {"knowledge_item_id": "knowledge-fixed", "version": "1"},
        "selected_voice": {"profile_id": "absent-voice", "version": "v7"},
        "selected_person": {"profile_id": "absent-person", "version": "v8"}}
    omitted = CreationPlanPreviewRequest(**payload)
    zero = main._creation_plan_preflight(request, workspace, omitted, automation_scope=scope)
    zero_trace = ["template", "reader", "reader", "prepare", "knowledge.list", "knowledge.pin",
        "sources", "materials", "search", "voice.pin", "person.pin", "catalog"]
    assert trace == zero_trace
    assert (zero.language, zero.aspect_ratio, zero.duration_target_seconds,
            zero.presenter_share_target, zero.lipsync_quality) == ("zh-CN", "16:9", 30, 0, "fast")
    assert inputs[0].provided_fields == omitted.model_fields_set | {
        "language", "aspect_ratio", "duration_target_seconds", "presenter_share_target", "lipsync_quality"}
    assert (inputs[0].selected_knowledge, inputs[0].selected_voice, inputs[0].selected_person) == (
        ("knowledge-fixed", "1"), ("absent-voice", "v7"), ("absent-person", "v8"))
    assert [(x.id, x.version, x.title) for x in zero.knowledge_references] == [("knowledge-fixed", "1", "固定资料")]
    assert [(x.id, x.connector_id) for x in zero.source_references] == [("source-fixed", "connector-fixed")]
    assert zero.source_references[0].identity == source_item_identity(source) == "06e2443a3a33afc383efaf1d80dece9b0ea5b0407d9a801a89900d5d6a519bba"
    assert zero.material_matches == [match] and (zero.approved_asset_count, zero.approved_clip_count) == (1, 1)
    assert [x.reason for x in zero.blockers] == ["voice_reference_missing"]
    assert zero.preflight_fingerprint == "f0d89c8c53d963447aac4c7de07e46f5ff7d81c6cb93160bd0104881595b0509"
    invocations.append({"kind": "preview_omitted_zero", "trace": list(trace),
        "fingerprint": zero.preflight_fingerprint, "provided_fields": sorted(inputs[0].provided_fields)})
    trace.clear()
    explicit = CreationPlanConfirmRequest(**payload, presenter_share_target=20, lipsync_quality="high",
        preflight_fingerprint=zero.preflight_fingerprint)
    positive = main._creation_plan_preflight(request, workspace, explicit, automation_scope=scope)
    assert trace == zero_trace + ["provider:high", "provider.ready", "provider.ready", "provider.ready"]
    assert positive.presenter_share_target == 20 and positive.lipsync_quality == "high"
    assert inputs[1].provided_fields == explicit.model_fields_set | {"language", "aspect_ratio", "duration_target_seconds"}
    assert inputs[1].selected_knowledge == inputs[0].selected_knowledge
    assert [x.reason for x in positive.blockers] == ["voice_reference_missing", "'absent-person'", "person_render_unavailable"]
    assert positive.preflight_fingerprint == "f736664a4a19d8edfd9843b321b629400d7910195432a03aad4b1651c9a5707d"
    assert all(not x.blocks_confirmation for x in positive.blockers)
    invocations.append({"kind": "confirm_explicit_positive", "trace": list(trace),
        "fingerprint": positive.preflight_fingerprint, "provided_fields": sorted(inputs[1].provided_fields)})
    import sqlite3
    with sqlite3.connect(database) as conn:
        counts = {table: conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("creation_plans", "creation_tasks", "job_runs")}
    assert counts == {"creation_plans": 0, "creation_tasks": 0, "job_runs": 0}
    evidence = os.environ.get("PLAN_PREFLIGHT_EVIDENCE_DIR")
    if evidence:
        (Path(evidence) / "NORMAL_FACTS.json").write_text(json.dumps({"invocations": invocations,
            "database": str(database), "counts": counts, "real_source_metadata": True,
            "qualified_material_catalog_provider": "declared facts only",
            "production_identity_file_media_model_acceptance": False}, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture
def selected_dependencies():
    from app.creation.plan_preflight_use_case import CreationPlanPreflightInput
    state = SimpleNamespace(extra=False, knowledge_readable=True, voice_allowed=True, material_present=True,
        voice_ready=True, diagnostic="first diagnostic", searched=[])
    knowledge = SimpleNamespace(id="knowledge", version="1", title="实际资料", authorization="authorized",
        ownership="user_owned", use_state="available")
    asset = SimpleNamespace(id="selected-asset", usage_role="b_roll")
    segment = SimpleNamespace(id="selected-segment", usage_role="b_roll")
    def knowledge_pin(workspace, identifier, version, **reader):
        assert (workspace, identifier, version, reader) == ("ws", "knowledge", "1",
            {"user_id": "reader", "local_diagnostic": False})
        if not state.knowledge_readable:
            raise KeyError(identifier)
        return knowledge
    def materials(*args, **kwargs):
        selected = [(asset, segment)] if state.material_present else []
        extras = [(SimpleNamespace(id=f"other-{i}", usage_role="b_roll"),
            SimpleNamespace(id=f"new-{i}", usage_role="b_roll")) for i in range(12)] if state.extra else []
        return selected + extras
    def search(workspace, **kwargs):
        pairs = kwargs["eligible_segment_refs"]
        state.searched.append(set(pairs))
        # A full-library top eight would displace the original selected pair.
        return [MaterialSegmentMatch("asset:" + a, "segment:" + s, 1.0, ["matching input"])
            for a, s in sorted(pairs)[:8]]
    def voice_reference(*args, **kwargs):
        assert kwargs == {"viewer_user_id": "reader", "local_diagnostic": False}
        if not state.voice_allowed:
            raise PermissionError("profile_excluded")
        return SimpleNamespace(profile_id="voice", version="v1", model_id="voice-model")
    production = SimpleNamespace(automatic_material_segments=materials, material_search=SimpleNamespace(search_material_segments=search),
        get_versioned_voice_profile=lambda *_:SimpleNamespace(id="voice", version="v1", language="zh-en", person_profile_id=""),
        creation_plan_voice_reference=voice_reference, list_applicable_pronunciation_entries=lambda **_:[])
    sources = SimpleNamespace(list_knowledge_items_for_use=lambda *_, **__: [knowledge] + ([SimpleNamespace(
        **{**vars(knowledge), "id":"other-knowledge"})] if state.extra else []),
        get_knowledge_reference_readable_by=knowledge_pin, list_source_items_for_use=lambda *_, **__:[])
    def statuses():
        selected = SimpleNamespace(model=SimpleNamespace(id="voice-model", capability="voice"), state="ready",
            ready=state.voice_ready, message=state.diagnostic)
        return [selected] + ([SimpleNamespace(model=SimpleNamespace(id="unused-vision", capability="visual"),
            state="unavailable", ready=False, message=state.diagnostic)] if state.extra else [])
    case = CreationPlanPreflightUseCase(production, sources, SimpleNamespace(list_statuses=statuses),
        lipsync_provider_for=lambda _:pytest.fail("zero presenter does not need a lipsync provider"))
    payload = CreationPlanPreflightInput(goal="实际目标", name=None, target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=30, presenter_share_target=0, lipsync_quality="high",
        template_id=None, template_version=None, selected_knowledge=None, selected_voice=("voice","v1"),
        selected_person=None, provided_fields=frozenset())
    def prepare(frozen=None):
        return case.prepare("ws", payload, template=None, reader_user_id="reader", local_diagnostic=False, frozen_plan=frozen)
    baseline = prepare()
    frozen = {"knowledge_references":[asdict(x) for x in baseline.knowledge_references],
        "source_references":[], "material_matches":[asdict(x) for x in baseline.material_matches]}
    return state, prepare, baseline, frozen


def test_adoption_search_is_scoped_before_top_n_and_ignores_unselected_diagnostics(selected_dependencies):
    state, prepare, baseline, frozen = selected_dependencies
    state.extra, state.diagnostic = True, "unrelated status text changed"
    current = prepare(frozen)
    assert state.searched[-1] == {("selected-asset", "selected-segment")}
    assert current.material_matches == baseline.material_matches
    assert current.preflight_fingerprint == baseline.preflight_fingerprint
    assert [x.id for x in current.knowledge_references] == ["knowledge"]


@pytest.mark.parametrize("change", ["knowledge_readable", "voice_allowed", "material_present", "voice_ready"])
def test_selected_dependencies_and_current_permission_still_fence_adoption(selected_dependencies, change):
    state, prepare, baseline, frozen = selected_dependencies
    setattr(state, change, False)
    current = prepare(frozen)
    assert current.preflight_fingerprint != baseline.preflight_fingerprint
    if change == "knowledge_readable":
        assert any(x.blocks_confirmation for x in current.blockers)
