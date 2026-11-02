"""Real SQLite/queue contracts with explicit synthetic encoder boundaries; no model load."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.creation_execution import CreationExecutionActor
from app.device_runtime import OwnerRouter
from app.device_scheduler import DeviceScheduler, RecoveryBlocked
from app.materials.analysis import _visual_fingerprint
from app.materials.retrieval import MaterialRetrievalService, OWNER
from app.host_resource_probes import MaterialVisualQueryBudget
from app.models import utc_now
from app.persistence_json import _json_payload
from app.visual_semantics import ChineseClipSubprocessProvider, visual_feature_version
from tests.test_device_scheduler import Resources, Processes
from tests.test_material_search_candidates import _approved_asset, _repository


@pytest.fixture
def setup(tmp_path):
    repo = _repository(tmp_path)
    asset = _approved_asset(repo, "workspace", "factory.mp4", segments=[
        {"_start": 0.0, "description": "设备运行与出料", "transcript": ""},
        {"_start": 5.0, "description": "无关车间", "transcript": ""},
    ])
    model = tmp_path / "model"
    model.mkdir()
    (model / "manifest.json").write_text("{}")
    worker = tmp_path / "worker.py"
    worker.write_text("synthetic protocol fixture")
    calls = []
    provider = ChineseClipSubprocessProvider(runtime_python_path=worker,
        worker_path=worker, model_path=model, runner=lambda command: calls.append(command))
    active = {"member-a", "member-b"}
    service = MaterialRetrievalService(assets=repo._material_assets, analysis=repo._material_analysis,
        provider=provider, database_path=repo.database_path,
        verify_identity=lambda actor: "ready" if actor.member_id in active else "unauthorized",
        actor_from_identity=CreationExecutionActor.from_event,
        query_budget=MaterialVisualQueryBudget(provider.model_id, visual_feature_version(provider.model_id),
            120, 10., 1024, 1024, 10000, 1, 10000, 4, .1, 20000, 20000))
    actor = CreationExecutionActor("workspace", "member-a")
    with repo._connect() as conn:
        for index, segment in enumerate(asset.segments):
            repo._material_assets.write_visual_segment_feature(conn, "workspace", asset.id, segment.id,
                provider.model_id, _visual_fingerprint(Path(asset.path), segment, provider.model_id),
                json.dumps([1.0, 0.0] if index == 0 else [0.0, 1.0]), 3, utc_now().isoformat())
    return SimpleNamespace(repo=repo, asset=asset, service=service, actor=actor, active=active,
                           provider=provider, calls=calls)


def _search(s, **kwargs):
    return s.service.search("workspace", actor=s.actor, viewer_user_id="member-a", **kwargs)


def _cache(s, query, vector=None):
    identity = s.actor.identity_payload()
    query_id = s.service._query_id("workspace", identity, query.casefold())
    s.service.assets.save_semantic_query(query_id, "workspace", identity, query.casefold(),
        s.provider.model_id, s.service.index_version)
    if vector is not None:
        with s.repo._connect() as conn:
            s.service.assets.commit_semantic_query(conn, "workspace", query_id, vector)
    return query_id


def _queue(s, tmp_path):
    access = SimpleNamespace(can_view_device_summary=lambda actor: False)
    return DeviceScheduler(s.repo.database_path, tmp_path / "device.lock",
        owners=OwnerRouter({OWNER: s.service.registration()}, access),
        resources=Resources(), processes=Processes(), activity_source_reliable=True)


def test_semantic_only_candidate_carries_real_cached_source_evidence(setup):
    s = setup
    _cache(s, "feeding operation", [1.0, 0.0])
    result = _search(s, query="feeding operation")
    assert result.semantic_status == "ready"
    assert len(result.matches) == 1
    item = result.matches[0]
    assert item.asset_ref == f"asset:{s.asset.id}"
    assert item.segment_ref == f"segment:{s.asset.segments[0].id}"
    assert (item.start_seconds, item.end_seconds) == (0, 5)
    assert item.match_tier == "semantic" and item.needs_confirmation
    assert item.visual_model_version == visual_feature_version(s.provider.model_id)
    assert item.visual_sample_timestamps == [1.25, 2.5, 3.75]
    assert item.visual_similarity == 1 and "概率" in result.semantic_message
    assert s.calls == []  # Search can never invoke the ungranted provider.
    assert _search(s, query="feeding operation", automatic_selection=True).matches == []


@pytest.mark.parametrize("change", ["private", "raw", "rejected", "excluded", "exclude_role", "segment_raw", "segment_exclude", "not_for_automation", "processing"])
def test_current_permissions_review_and_exclusion_precede_feature_reads(setup, change, monkeypatch):
    s = setup
    asset = s.repo.get_asset("workspace", s.asset.id)
    if change == "private":
        asset.visibility, asset.owner_user_id = "private", "member-b"
    elif change in {"raw", "rejected"}:
        asset.review_status = change
    elif change in {"excluded", "not_for_automation"}:
        asset.use_state = change
    elif change == "exclude_role":
        asset.usage_role = "exclude"
    elif change == "processing":
        asset.analysis_status = 'pending'
    else:
        for segment in asset.segments:
            if change == "segment_raw":
                segment.review_status = "raw"
            else:
                segment.usage_role = "exclude"
    with s.repo._connect() as conn:
        conn.execute("UPDATE material_assets SET payload=?,visibility=? WHERE id=?",
                     (_json_payload(asset), asset.visibility, asset.id))
    if change in {"raw", "segment_raw"}:
        _cache(s, "设备", [1.0, 0.0])
        assert _search(s, query="设备").matches
        persisted = s.repo.get_asset("workspace", asset.id)
        assert (persisted.review_status == "raw" if change == "raw" else
                all(segment.review_status == "raw" for segment in persisted.segments))
        return
    monkeypatch.setattr(s.service.analysis, "read_visual_features",
                        lambda *_: pytest.fail("ineligible features must not be read"))
    result = _search(s, query="设备", automatic_selection=change == "not_for_automation")
    assert result.matches == []
    if change in {'processing', 'not_for_automation'}:
        assert ('分析尚未就绪' if change == 'processing' else '用途限制') in result.semantic_message


def test_scope_and_structured_filters_apply_before_limit(setup):
    s = setup
    assert _search(s, query="设备", asset_ids=["other"]).matches == []
    assert _search(s, query="设备", eligible_segment_refs=set()).matches == []
    assert _search(s, query="设备", tags=["不存在"]).matches == []
    assert _search(s, query="设备", min_duration_seconds=20).matches == []
    with pytest.raises(PermissionError):
        s.service.search("other-workspace", query="设备", actor=s.actor, viewer_user_id="member-a")
    with pytest.raises(PermissionError):
        s.service.search("workspace", query="设备", actor=s.actor, viewer_user_id="member-b")


def test_file_change_even_with_restored_mtime_invalidates_existing_vectors(setup):
    s = setup
    _cache(s, "feeding operation", [1.0, 0.0])
    path = Path(s.asset.path)
    stat = path.stat()
    path.write_bytes(b"changed")  # same length as the existing fixture
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = _search(s, query="feeding operation")
    assert result.semantic_status == "index_missing" and result.matches == []
    assert s.calls == []


def test_model_revision_invalidates_query_and_segment_caches(setup, monkeypatch):
    s = setup
    _cache(s, "feeding operation", [1.0, 0.0])
    monkeypatch.setattr("app.materials.analysis.visual_feature_version", lambda _: "new-revision")
    monkeypatch.setattr("app.materials.retrieval.visual_feature_version", lambda _: "new-revision")
    result = _search(s, query="feeding operation")
    assert result.semantic_status == "index_missing" and result.matches == []


def test_revoke_between_initial_and_final_candidate_reads_never_returns_stale_hit(setup, monkeypatch):
    s = setup
    _cache(s, "feeding operation", [1.0, 0.0])
    original = s.service.analysis.read_visual_features
    def revoke(*args):
        result = original(*args)
        s.repo.set_asset_visibility("workspace", s.asset.id, "private")
        return result
    monkeypatch.setattr(s.service.analysis, "read_visual_features", revoke)
    assert _search(s, query="feeding operation").matches == []


def test_queue_deduplicates_and_executor_atomically_publishes_query_vector(setup, tmp_path, monkeypatch):
    s = setup
    scheduler = _queue(s, tmp_path)
    first = _search(s, query="feeding operation", scheduler=scheduler)
    second = _search(s, query="feeding operation", scheduler=scheduler)
    assert first.semantic_status == "pending"
    assert first.device_request_id == second.device_request_id
    assert s.calls == []
    grant = scheduler.pick_and_grant()
    assert grant is not None
    def bound(scheduler_arg, grant_arg, limits):
        assert scheduler_arg is scheduler and grant_arg == grant
        scheduler.assert_grant(grant)
        return SimpleNamespace(embed_texts=lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr(s.provider, "for_query_grant", bound)
    s.service.registration().executor.execute(grant, scheduler)
    assert scheduler.view_for(grant.request_id, s.actor)["state"] == "done"
    assert _search(s, query="feeding operation").semantic_status == "ready"
    assert s.calls == []
    # Another process/service instance reads the same persisted cache after restart.
    restarted = MaterialRetrievalService(assets=s.service.assets, analysis=s.service.analysis,
        provider=s.provider, database_path=s.repo.database_path, verify_identity=s.service.verify_identity,
        actor_from_identity=CreationExecutionActor.from_event, query_budget=s.service.query_budget)
    assert restarted.search("workspace", query="feeding operation", actor=s.actor, viewer_user_id="member-a").matches


def test_missing_budget_does_not_submit_or_invoke_encoder(setup, tmp_path):
    s = setup
    s.service.query_budget = None
    result = _search(s, query="feeding operation", scheduler=_queue(s, tmp_path))
    assert result.semantic_status == "unavailable" and result.device_request_id is None
    assert "执行范围" in result.semantic_message and s.calls == []


def test_revoked_member_cannot_execute_queued_query_or_read_private_cache(setup, tmp_path):
    s = setup
    scheduler = _queue(s, tmp_path)
    result = _search(s, query="feeding operation", scheduler=scheduler)
    s.active.remove("member-a")
    assert scheduler.pick_and_grant() is None
    with pytest.raises(PermissionError):
        _search(s, query="feeding operation")
    with pytest.raises(PermissionError):
        scheduler.view_for(result.device_request_id, CreationExecutionActor("workspace", "member-b"))


def test_no_semantic_cache_or_model_still_returns_explained_lexical_results(setup):
    s = setup
    result = _search(s, query="设备")
    assert result.semantic_status == "unavailable"
    assert result.matches[0].match_tier == "text"
    assert "设备" in result.matches[0].evidence_excerpt and s.calls == []
    assert _search(s, query="unrelated").matches == []


def test_query_grant_adapter_refuses_unmeasured_process_monitor(setup, tmp_path):
    s = setup
    scheduler = _queue(s, tmp_path)
    _search(s, query="feeding operation", scheduler=scheduler)
    grant = scheduler.pick_and_grant()
    with pytest.raises(RecoveryBlocked, match="进程组资源范围"):
        s.provider.for_query_grant(scheduler, grant, s.service.execution_limits)
    assert s.calls == []


def test_query_grant_adapter_uses_existing_bounded_group_runner(setup, tmp_path, monkeypatch):
    from app.granted_subprocess import GrantedCommandResult
    from app.host_resource_probes import HostResourceProbe
    s = setup
    scheduler = _queue(s, tmp_path)
    _search(s, query="feeding operation", scheduler=scheduler)
    grant = scheduler.pick_and_grant()
    probe = object.__new__(HostResourceProbe)
    monkeypatch.setattr(probe, "scene_runtime_limits", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(probe, "continue_unit", lambda _: True)
    scheduler.resources = probe
    calls = []
    def run(host, actual_grant, argv, **limits):
        assert host is scheduler and actual_grant == grant
        scheduler.assert_grant(grant)
        request = json.loads(Path(argv[argv.index("--request") + 1]).read_text())
        assert request["mode"] == "text" and request["texts"] == ["feeding operation"]
        response = Path(argv[argv.index("--response") + 1])
        response.write_text(json.dumps({"schema_version": 1, "model_id": s.provider.model_id,
                                        "text_embeddings": [[1.0, 0.0]]}))
        calls.append(limits)
        return GrantedCommandResult(0, b"", b"")
    monkeypatch.setattr("app.granted_subprocess.run_granted_command", run)
    encoder = s.provider.for_query_grant(scheduler, grant, s.service.execution_limits)
    assert encoder.embed_texts(["feeding operation"]) == [[1.0, 0.0]]
    assert calls[0]["max_output_file_bytes"] == 10000
    assert calls[0]["resource_purpose"] == "material_visual_query"
    assert calls[0]["env"]["HF_HUB_OFFLINE"] == "1"
    assert s.calls == []


def test_mismatched_vector_dimension_is_explicit_and_keeps_lexical_result(setup):
    s = setup
    _cache(s, "设备", [1.0, 0.0, 0.0])
    result = _search(s, query="设备")
    assert result.semantic_status == "partial_index"
    assert result.matches[0].match_tier == "text"
    assert result.matches[0].visual_similarity is None


def test_private_query_cache_is_not_reused_for_another_member(setup):
    s = setup
    _cache(s, "feeding operation", [1.0, 0.0])
    result = s.service.search("workspace", query="feeding operation", viewer_user_id="member-b",
        actor=CreationExecutionActor("workspace", "member-b"))
    assert result.semantic_status == "unavailable" and result.matches == []


def test_encoder_failure_keeps_queue_failed_without_partial_vector_or_automatic_retry(setup, tmp_path, monkeypatch):
    s = setup
    scheduler = _queue(s, tmp_path)
    first = _search(s, query="feeding operation", scheduler=scheduler)
    grant = scheduler.pick_and_grant()
    def fail(*_):
        raise ValueError("synthetic invalid result")
    monkeypatch.setattr(s.provider, "for_query_grant", fail)
    s.service.registration().executor.execute(grant, scheduler)
    assert scheduler.view_for(grant.request_id, s.actor)["state"] == "failed"
    again = _search(s, query="feeding operation", scheduler=scheduler)
    assert again.semantic_status == "unavailable"
    assert again.device_request_id == first.device_request_id and scheduler.pick_and_grant() is None
    assert _search(s, query="feeding operation").matches == []


def test_manual_correction_uses_current_text_and_preserves_exact_lexical_selection(setup):
    s = setup
    _cache(s, "设备", [1.0, 0.0])
    assert _search(s, query="设备", automatic_selection=True).matches
    s.repo.update_segment("workspace", s.asset.id, s.asset.segments[0].id,
        description="人工核对：这是空车运转，没有进料", transcript="", review_status="approved", usage_role="b_roll")
    result = _search(s, query="设备")
    assert result.matches[0].evidence_excerpt.startswith("人工核对")
    assert result.matches[0].needs_confirmation
    assert _search(s, query="设备", automatic_selection=True).matches == []


def test_query_pressure_releases_and_resumes_same_device_request(setup, tmp_path, monkeypatch):
    from app.host_memory import MemoryDecision, MemorySample
    from app.granted_subprocess import GrantedCommandMemoryPressure
    s = setup
    scheduler = _queue(s, tmp_path)
    clock = [datetime.now(timezone.utc)]
    scheduler.clock = lambda: clock[0]
    result = _search(s, query="feeding operation", scheduler=scheduler)
    grant = scheduler.pick_and_grant()
    def stop(*_):
        sample = MemorySample("darwin", 0., "synthetic", "critical", 1000, None, {})
        raise GrantedCommandMemoryPressure(MemoryDecision(False, "memory_pressure_critical", sample, 100, None, 0.))
    monkeypatch.setattr(s.provider, "for_query_grant", stop)
    s.service.registration().executor.execute(grant, scheduler)
    view = scheduler.view_for(result.device_request_id, s.actor)
    assert view["state"] == "blocked" and view["wait_reason"] == "resource_pressure"
    with s.repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
    assert scheduler.pick_and_grant() is None
    monkeypatch.setattr(s.provider, "for_query_grant", lambda *_: SimpleNamespace(embed_texts=lambda _: [[1.0, 0.0]]))
    clock[0] += timedelta(seconds=31)
    resumed = scheduler.pick_and_grant()
    assert resumed is not None and resumed.request_id == grant.request_id
    assert resumed.attempt_number == grant.attempt_number and resumed.generation > grant.generation
    s.service.registration().executor.execute(resumed, scheduler)
    assert scheduler.view_for(grant.request_id, s.actor)["state"] == "done"
    assert _search(s, query="feeding operation").semantic_status == "ready"
