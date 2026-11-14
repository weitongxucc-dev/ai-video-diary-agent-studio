from types import SimpleNamespace

from app.materials.candidate_bundle import build_candidate_bundle
from app.materials.library_routes import MaterialLibraryHTTPAdapter


def test_candidate_bundle_projects_material_and_knowledge_without_promoting_unknowns():
    material = SimpleNamespace(
        asset_ref="asset:a1", segment_ref="segment:s1", score=0.72,
        evidence_ref_ids=["evidence-1"], needs_confirmation=True,
        time_range="0.0 – 2.0", reasons=["文字命中"], match_tier="text",
    )
    claim = SimpleNamespace(
        id="claim-1", knowledge_item_id="knowledge-1", knowledge_version="v2",
        evidence_refs=(), status="candidate", confidence=None, field_ref="model",
        value="待确认型号", unit=None, origin="model_inference",
    )
    bundle = build_candidate_bundle([material], [claim], knowledge_requested=True)
    assert bundle["schema"] == "candidate-bundle/v1"
    assert bundle["summary"] == {
        "total": 2, "material_candidates": 1, "knowledge_candidates": 1,
        "confirmed": 0, "needs_confirmation": 2,
    }
    first, second = bundle["candidates"]
    assert first["evidence"] == {"status": "available", "ref_ids": ["evidence-1"]}
    assert first["authorization"]["status"] == "needs_confirmation"
    assert first["source_revision"] == {"status": "unknown"}
    assert second["status"] == "candidate"
    assert second["source_revision"] == {"knowledge_version": "v2"}
    assert bundle["missing_domains"] == ["person", "voice"]


def test_candidate_bundle_keeps_confirmed_knowledge_distinct():
    claim = SimpleNamespace(
        id="claim-2", knowledge_item_id="knowledge-2", knowledge_version="v3",
        evidence_refs=({"source_ref": "page-1"},), status="confirmed", confidence=0.9,
        field_ref="capacity", value=10, unit="unit/min", origin="user_confirmation",
    )
    candidate = build_candidate_bundle(knowledge_claims=[claim])["candidates"][0]
    assert candidate["status"] == "confirmed"
    assert candidate["authorization"]["status"] == "confirmed"
    assert candidate["evidence"]["status"] == "available"


def test_material_candidate_bundle_read_adapter_returns_revision_and_missing_domains():
    asset = SimpleNamespace(
        id="asset-1", visibility="shared", owner_user_id=None,
        updated_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
        modified_at=SimpleNamespace(isoformat=lambda: "2025-12-31T00:00:00+00:00"),
    )
    match = SimpleNamespace(
        asset_ref="asset:asset-1", segment_ref="segment:segment-1", score=0.4,
        evidence_ref_ids=[], needs_confirmation=True, time_range="0 – 2",
        reasons=["当前工作区已分析素材"], match_tier="structural",
    )
    records = SimpleNamespace(get_asset=lambda _workspace, _asset_id: asset)
    search = SimpleNamespace(search_material_segments=lambda *_args, **_kwargs: [match])
    adapter = MaterialLibraryHTTPAdapter(
        records_for=lambda: records, search_for=lambda: search,
        workspace_for=lambda _request: "workspace-1", reader_for=lambda _request, _workspace: ("member-1", False),
        visible=lambda *_args: True, write_guard=lambda _request: None,
        library_root=lambda: ".", asset_response=lambda value: value,
    )
    result = adapter.candidate_bundle(SimpleNamespace(), "asset-1")
    assert result["schema"] == "candidate-bundle/v1"
    assert result["candidates"][0]["source_revision"]["asset_updated_at"].startswith("2026-")
    assert result["missing_domains"] == ["knowledge", "person", "voice"]


def test_material_candidate_bundle_reads_visible_knowledge_claims_when_requested():
    asset = SimpleNamespace(
        id="asset-1", visibility="shared", owner_user_id=None,
        updated_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
        modified_at=SimpleNamespace(isoformat=lambda: "2025-12-31T00:00:00+00:00"),
    )
    claim = SimpleNamespace(
        id="claim-1", knowledge_item_id="knowledge-1", knowledge_version="v2",
        evidence_refs=(), status="candidate", confidence=None, field_ref="model",
        value="待确认型号", unit=None, origin="model_inference",
    )
    records = SimpleNamespace(get_asset=lambda _workspace, _asset_id: asset)
    search = SimpleNamespace(search_material_segments=lambda *_args, **_kwargs: [])
    adapter = MaterialLibraryHTTPAdapter(
        records_for=lambda: records, search_for=lambda: search,
        workspace_for=lambda _request: "workspace-1", reader_for=lambda _request, _workspace: ("member-1", False),
        visible=lambda *_args: True, write_guard=lambda _request: None,
        library_root=lambda: ".", asset_response=lambda value: value,
        knowledge_claims_for=lambda *_args: [claim],
    )
    result = adapter.candidate_bundle(SimpleNamespace(), "asset-1", knowledge_item_id="knowledge-1")
    assert result["summary"]["knowledge_candidates"] == 1
    assert result["candidates"][0]["source_revision"] == {"knowledge_version": "v2"}
    assert result["missing_domains"] == ["person", "voice"]
