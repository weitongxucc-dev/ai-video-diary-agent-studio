"""Normal HTTP, persistent business events and lifecycle driver; bounded model seam."""

from assistant_sdk_fixtures import project_event_fixture
from app.task_media_script_routes import media_script_submission_source
import time
import json
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

import app.main as main
import app.assistant_pi as pi
from dataclasses import replace
from app.creation_execution import CreationExecution, revision_digest
from app.assistant.execution_binding import execution_locator_id
from app.quality_inspection import QualityCheckRepository
from test_assistant_business_continuation import state as state  # noqa: F401
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state  # noqa: F401
import test_media_script_api

from test_media_script_tool_api import receipt_state as receipt_state

script_state = test_media_script_api.state


@pytest.fixture(autouse=True)
def isolated_local_workspace(request, monkeypatch):
    if request.node.name.split("[")[0] == "test_common_edit_registration_persists_real_start_source_without_running_media":
        monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "local")
        return
    if request.node.name == "test_execution_binding_plain_receipt_preserves_committed_successor_and_bounds":
        import subprocess
        import threading
        import wave
        import httpx
        from PIL import Image
        from app.artifact_store import ArtifactStore
        from app.auth import SupabaseAuthContext
        from app.config import Settings
        from app.device_runtime import DeviceRuntime
        from app.source_repository import SourceRepository
        tmp_path = request.getfixturevalue('tmp_path')

        def no_execution(*args, **kwargs):
            pytest.fail('public receipt projection must not start execution or inspect media')

        for owner, name in ((subprocess, 'Popen'), (threading.Thread, 'start'),
                (wave, 'open'), (Image, 'open'), (Image, 'new'), (Image.Image, 'save'),
                (httpx.HTTPTransport, 'handle_request'), (httpx.AsyncHTTPTransport, 'handle_async_request')):
            monkeypatch.setattr(owner, name, no_execution)
        for name in ('start', 'run_once', 'wake'):
            monkeypatch.setattr(DeviceRuntime, name, no_execution)
        for name in ('_build_device_components', 'recover_interrupted_quality_checks',
                '_device_host_budget', '_wake_assistant_continuations'):
            monkeypatch.setattr(main, name, no_execution)
        settings = Settings(database_path=str(tmp_path / 'studio.sqlite3'),
            video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'output'),
            model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
            draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
            obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
            local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
            native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
        monkeypatch.setattr(main, 'settings', settings)
        monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
        monkeypatch.setattr(main, 'device_runtime', None)
        monkeypatch.setattr(main, 'automation_runtime', None)
        monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
        monkeypatch.setattr(main, 'source_repository', SourceRepository(tmp_path / 'sources.sqlite3'))
        # Absence of execution configuration is declared data, never a host probe.
        monkeypatch.setattr(main, '_device_operation_budget', lambda name: None)
    if request.node.name.split("[")[0] == "test_bounded_checkpoint_requires_same_goal_current_final_hash_and_no_pending":
        # This original bounded SQL representative has no dependency on the media/driver fixture.
        from test_production_foundation import edit_adoption_no_execution
        edit_adoption_no_execution(monkeypatch)
        return
    if "media_result_state" in request.fixturenames:
        request.getfixturevalue("media_result_state")
        return
    if {'execution_binding_state', 'edit_result_state'} & set(request.fixturenames):
        # Install before dynamically requesting any seed fixture. Neither old
        # media helpers nor a native child may run in binding-only evidence.
        import subprocess
        import _media_preview_test_fixtures as media_helpers
        import test_creation_execution as creation_helpers
        def forbidden(*args, **kwargs):
            pytest.fail('binding evidence forbids media helpers, model/driver execution and Popen')
        monkeypatch.setattr(subprocess, 'Popen', forbidden)
        for owner in (media_helpers, creation_helpers):
            for name in ('write_synthetic_video', 'register_synthetic_video_asset',
                         'register_synthetic_profiles', 'synthetic_wav_bytes'):
                monkeypatch.setattr(owner, name, forbidden)
        monkeypatch.setattr(main, 'run_task_turn', forbidden)
        monkeypatch.setattr(main, 'start_assistant_continuation_driver', forbidden)
        monkeypatch.setattr(main.montage_renderer, 'render', forbidden)
    script_runtime = None
    if "script_state" in request.fixturenames:
        script_runtime = request.getfixturevalue("script_state").runtime
    state = request.getfixturevalue("edit_result_state" if "edit_result_state" in request.fixturenames else
        "execution_http_state" if "execution_http_state" in request.fixturenames else "state")
    if script_runtime is not None:
        script_runtime.task_authorizations = state.runtime.task_authorizations
        script_runtime.registrations["creation_job"].owner.task_media.on_settled = main._wake_assistant_continuations
        state.runtime = script_runtime
        monkeypatch.setattr(main, "device_runtime", script_runtime)
        monkeypatch.setattr(main, "_creation_runtime", lambda: script_runtime)
        monkeypatch.setattr(main, "_media_result_read_components", lambda: (
            script_runtime.registrations["creation_job"].owner.task_media, script_runtime.scheduler))
    # Existing domain fixtures name their single local workspace workspace_a.
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", state.workspace_id)
    from test_montage_pipeline import synthetic_clip_preview_budget
    selected_budget_for = main._device_operation_budget
    monkeypatch.setattr(main, '_device_operation_budget',
        lambda name: synthetic_clip_preview_budget() if name == 'clip_preview' else selected_budget_for(name))
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    configure_synthetic_media_budgets(monkeypatch,main)
    if 'script_state' in request.fixturenames:
        from app.member_permits import media_script_budget_from_env
        profile = main._device_host_budget()
        # The script fixture declares its finite experiment budget in env;
        # preserve it alongside this fixture's audio/render envelopes.
        monkeypatch.setattr(main, '_device_host_budget',
            lambda: replace(profile, media_script=media_script_budget_from_env(),
                min_available_memory_bytes=512*1024**2, min_free_disk_bytes=64*1024**2))



def wait_until(predicate, *, diagnostic=None):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    if diagnostic is not None:
        from pathlib import Path
        path = Path("/tmp") / f"yaohe-assistant-goal-wait-{uuid4().hex}.json"
        path.write_text(json.dumps(diagnostic(), ensure_ascii=False, default=str))
        pytest.fail(f"normal lifecycle driver did not advance; isolated facts: {path}")
    pytest.fail("normal lifecycle driver did not advance the actual result")


def select_clip(state, chat):
    return state.conversations.save_reference(chat["id"],state.workspace_id,state.member.member_id,
        {"kind":"selection","items":[{"kind":"shot","task_id":state.task.id,"object_id":state.clip.id,
            "version":state.version.id,"label":"当前镜头"}]},chat["reference_version"])


def material_execution_input(state):
    """A confirmed material-only fixture; no presenter or media execution claim."""
    from dataclasses import asdict
    from app.montage import creation_task_montage_plan_to_payload
    plan=state.production.plan_records.get_creation_plan(state.workspace_id,state.task.plan_id)
    plan.presenter_share_target=0
    with state.production._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?",(json.dumps(asdict(plan),default=str),plan.id))
    reference=state.production.workspace_media_reference_for_asset(state.workspace_id,state.asset.id)
    montage=replace(state.montage,presenter_share_target=0,clips=[replace(clip,person_render_id=None,
        asset_id=state.asset.id,segment_id=state.asset.segments[0].id,source_path=reference) if clip.person_render_id else clip
        for clip in state.montage.clips])
    payload=creation_task_montage_plan_to_payload(montage)
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,montage.id,payload)
    state.version=replace(state.version,id="material-execution-input",parent_version_id=state.version.id,
        sequence=state.version.sequence+1,snapshot={**state.version.snapshot,"montage":payload})
    state.production.timeline_records.create_timeline_version(state.version)


def test_assistant_scope_reads_registered_image_overlay_from_persisted_montage(state):
    import io
    from PIL import Image
    from app.graphic_overlays import ImageOverlay
    from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload

    image = io.BytesIO()
    Image.new("RGBA", (48, 48), (255, 255, 0, 255)).save(image, format="PNG")
    imported = state.raw_post(state.base_url + "/graphics/import",
        files={"image": ("registered-scope.png", image.getvalue(), "image/png")})
    assert imported.status_code == 200, imported.text
    montage = creation_task_montage_plan_from_payload(
        state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id))
    overlay = ImageOverlay(source_ref=imported.json()["source_ref"], name="已登记测试图片",
        start_seconds=0, end_seconds=min(1, montage.duration_seconds)).model_dump()
    montage.graphic_overlays.append(overlay)
    task, saved = state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id, state.task.id,
        montage.id, creation_task_montage_plan_to_payload(montage))
    stored = creation_task_montage_plan_from_payload(
        state.production.get_creation_task_montage_plan(state.workspace_id, task.id))
    assert stored.graphic_overlays == saved["graphic_overlays"] == [overlay]
    assert isinstance(stored.graphic_overlays[0], dict)
    selected = [f"clip:{state.clip.id}", f"overlay:{overlay['id']}"]
    payload = main.CreationTaskAssistantTurnRequest(conversation_id=state.conversation["id"],
        message="查看当前镜头和图片", client_request_id=uuid4(), expected_draft_version=0,
        context_stage="review", selected_object_refs=selected)
    assert main._assistant_turn_scope(state.workspace_id, task, payload) == ("review", selected)
    payload.selected_object_refs = ["overlay:foreign-overlay"]
    with pytest.raises(main.HTTPException) as error:
        main._assistant_turn_scope(state.workspace_id, task, payload)
    assert error.value.status_code == 422


@pytest.mark.parametrize("observation_first", [False, True])
def test_sibling_observation_cannot_advance_goal_past_pending_real_preview_or_adoption(state, monkeypatch, observation_first):
    state.finish()
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    media = state.runtime.registrations["creation_job"].owner.task_media
    def bounded_media_seam(job, event, actor, context):
        # The media observer is a seam; real device/JobRun/event/HTTP ownership is exercised.
        context.check()
        assert event["operation"] == "output_observation" and actor.member_id == state.member.member_id
        return {"observation_id": job.id, "task_id": job.task_id, "fixture": "bounded_reader_result"}
    def validate_observation(job, event, result, context):
        context.check()
        assert result == {"observation_id": job.id, "task_id": job.task_id, "fixture": "bounded_reader_result"}
        return []
    monkeypatch.setattr(media.output_observation, "execute", bounded_media_seam)
    monkeypatch.setattr(media.output_observation, "validate_result", validate_observation)
    calls = []
    def model(_settings, **kwargs):
        calls.append(kwargs)
        if kwargs.get("continuation_context"):
            assert kwargs["continuation_context"]["business_event"]["status"] == "confirmed"
            return "已读取真实采用结果。", None
        observed = main.CreationTaskObservationRequest(target={"kind": "final"},
            expected_timeline_version_id=state.version.id, expected_timeline_sequence=state.version.sequence,
            assistant_turn_id=kwargs["turn_id"], request_id=uuid4())
        source = main._prepare_observation_request(state.task.id, state.request, observed)
        actor = main._require_permit_actor(state.request, state.workspace_id)
        def submit_observation():
            media.submit(state.runtime, actor, state.task.id, "output_observation",
                {"payload": observed.model_dump(mode="json"), "_typed_source": source, "_media_kind": "output_observation"}, "observation")
        if observation_first:
            submit_observation()
        preview = state.raw_post(state.base_url + "/edits/exact/preview", json={
            "operation_type": "ChangeCrop", "clip_id": state.clip.id, "fit_mode": "fill", "crop_x": .2, "crop_y": .5,
            "request_id": str(uuid4()), "assistant_turn_id": kwargs["turn_id"],
            "expected_timeline_version_id": state.version.id, "expected_timeline_sequence": state.version.sequence})
        assert preview.status_code == 202, preview.text
        if not observation_first:
            submit_observation()
        return "已安排观察与待采用预览。", {"kind": "media_job", "media_job": preview.json()}
    monkeypatch.setattr(main, "run_task_turn", model)
    chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
    chat = state.conversations.save_reference(chat["id"], state.workspace_id, state.member.member_id,
        {"kind":"selection","items":[{"kind":"shot","task_id":state.task.id,"object_id":state.clip.id,
            "version":state.version.id,"label":"当前镜头"}]},chat["reference_version"])
    raw = "检查实际结果并准备修改；等我实际采用，再继续生成"
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    result = state.raw_post(state.base_url + "/assistant/turn", json={"conversation_id": chat["id"], "message": raw,
        "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"],
        "expected_reference_version":chat["reference_version"], "context_stage": "review",
        "selected_object_refs": [f"clip:{state.clip.id}"], "expected_timeline_version_id": state.version.id,
        "expected_timeline_sequence": state.version.sequence})
    assert result.status_code == 200, result.text
    root_id = result.json()["turn_id"]
    main.start_assistant_continuation_driver()
    assert state.runtime.run_once()
    if observation_first:
        wait_until(lambda: state.conversations.get_turn(root_id)["result"].get("continuation", {}).get("phase") == "observation")
        assert main._goal_service().reconcile() == 0 and len(calls) == 1
        assert state.conversations.get_task_goal(root_id)["status"] == "waiting_result"
        assert state.runtime.run_once()
    preview = wait_until(lambda: state.conversations.get_turn(root_id)["result"]["response"].get("edit_preview"))
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == "waiting_adoption")
    if not observation_first:
        assert state.runtime.run_once()  # An unrelated completed observation must keep the actual preview card.
        wait_until(lambda: state.conversations.get_turn(root_id)["result"].get("continuation", {}).get("phase") == "observation")
        assert state.conversations.get_turn(root_id)["result"]["response"]["edit_preview"]["proposal"]["id"] == preview["proposal"]["id"]
    proposal = state.production.edit_records.get_edit_proposal(state.workspace_id, preview["proposal"]["id"])
    assert proposal.status == "proposed" and len(calls) == 1
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == state.version.id
    adopted = state.raw_post(state.base_url + f"/edits/{proposal.id}/confirm", json={"request_id": str(uuid4())})
    assert adopted.status_code == 200, adopted.text
    wait_until(lambda: len(calls) == 2)
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == "no_progress")
    assert main._goal_service().reconcile() == 0 and len(calls) == 2
    assert not any(job.kind == "creation_task_execution" for job in state.production.job_records.list_job_runs(state.workspace_id, state.task.id))


@pytest.mark.parametrize("damaged", [False, True])
def test_completed_script_slot_is_reused_after_real_adoption_without_new_job_and_rechecks_file(script_state, state, monkeypatch, damaged):
    state.finish()
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    models, receipts = [], []
    original_job_ids = {job.id for job in state.production.job_records.list_job_runs(state.workspace_id, state.task.id)}
    def job_ids():
        return {job.id for job in state.production.job_records.list_job_runs(state.workspace_id, state.task.id)}
    def submit_source(kwargs, *, name="card.png"):
        version = kwargs["task_version"]
        return state.raw_post(state.base_url + "/media-scripts/preview", json={**script_state.payload,
            "request_id": str(uuid4()), "assistant_turn_id": kwargs["turn_id"],
            "expected_timeline_version_id": version["timeline_version_id"], "expected_timeline_sequence": version["timeline_sequence"],
            "script_source": f"# another generated program at call {len(models)}\nprint('different')",
            "requested_outputs": [{**script_state.payload["requested_outputs"][0], "name": name}]})
    def model(_settings, **kwargs):
        models.append(kwargs)
        if not kwargs.get("continuation_context"):
            response = submit_source(kwargs)
            assert response.status_code == 202, response.text
            receipts.append(response.json())
            return "已安排一次图片来源。", {"kind": "media_job", "media_job": response.json()}
        slots = kwargs["continuation_context"]["completed_source_slots"]
        assert len(slots) == 1 and slots[0]["job_id"] == receipts[0]["job_id"]
        assert "script_source" not in slots[0]
        if damaged:
            main.artifact_store.resolve_file(slots[0]["source_ref"]).write_bytes(b"changed actual PNG")
        repeated = submit_source(kwargs)
        assert repeated.status_code == (409 if damaged else 200), repeated.text
        assert len(state.calls) == 1
        if damaged:
            return "原已完成来源发生变化，不能复用或自动重执行。", None
        reused = repeated.json()
        assert reused["job_id"] == receipts[0]["job_id"] and reused["result"]["reused_completed_source"] is True
        assert reused["result"]["applied"] is False
        assert job_ids() == original_job_ids | {receipts[0]["job_id"]}
        if len(models) == 2:
            prepared = main._media_script_submission_http_adapter().run(state.request, main._media_script_submission_use_case().prepare_source(state.task.id, media_script_submission_source(main.MediaScriptPreviewRequest.model_validate({
                **script_state.payload, "request_id": uuid4(), "assistant_turn_id": kwargs["turn_id"],
                "requested_outputs": [{**script_state.payload["requested_outputs"][0], "name": "new-slot.png"}]}))))
            assert "reused_completed_source" not in prepared  # A different slot follows normal scheduling.
            proposal = state.raw_post(state.base_url + "/edits/exact/preview", json={
                "operation_type": "DeleteClip", "clip_id": state.clip.id, "request_id": str(uuid4()),
                "assistant_turn_id": kwargs["turn_id"], "expected_timeline_version_id": state.version.id,
                "expected_timeline_sequence": state.version.sequence})
            assert proposal.status_code == 200, proposal.text
            return "请实际采用删除提案。", proposal.json()
        assert kwargs["continuation_context"]["business_event"]["status"] == "confirmed"
        assert kwargs["task_version"]["timeline_version_id"] != state.version.id
        ordinary = state.client.get(receipts[0]["result_url"])
        assert ordinary.status_code == 200 and ordinary.json()["status"] == "stale"
        return "同目标已完成来源已重新核验，仅复用来源，未再次生成。", {"kind": "media_job", "media_job": reused}
    monkeypatch.setattr(main, "run_task_turn", model)
    chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
    raw = "生成命名图片一次；实际采用修改后继续使用这份已完成来源"
    chat = select_clip(state,chat)
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    response = state.raw_post(state.base_url + "/assistant/turn", json={"conversation_id": chat["id"], "message": raw,
        "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"],
        "expected_reference_version":chat["reference_version"], "context_stage": "review",
        "selected_object_refs": [f"clip:{state.clip.id}"], "expected_timeline_version_id": state.version.id,
        "expected_timeline_sequence": state.version.sequence})
    assert response.status_code == 200, response.text
    root_id = response.json()["turn_id"]
    main.start_assistant_continuation_driver()
    assert state.runtime.run_once()
    source_job = state.production.job_records.get_job_run(state.workspace_id, receipts[0]["job_id"])
    assert source_job.status == "succeeded", main.jsonable_encoder(source_job)
    wait_until(lambda: len(models) == 2, diagnostic=lambda: {"goal":state.conversations.get_task_goal(root_id),
        "turns":state.conversations.list_turns(state.conversation["id"]),"calls":len(models),
        "jobs":[main.jsonable_encoder(job) for job in state.production.job_records.list_job_runs(state.workspace_id,state.task.id)],
        "task_events":[main.jsonable_encoder(event) for event in state.production.execution_records.list_task_events(state.workspace_id,state.task.id)]})
    if not damaged:
        child_id = models[-1]["turn_id"]
        preview = wait_until(lambda: (state.conversations.get_turn(child_id)["result"] or {}).get("response", {}).get("edit_preview"))
        assert state.production.edit_records.get_edit_proposal(state.workspace_id, preview["proposal"]["id"]).status == "proposed"
        pending_goal = state.conversations.get_task_goal(root_id)
        assert pending_goal["status"] == "waiting_adoption", {
            "goal":pending_goal, "turn":state.conversations.get_turn(child_id)}
        adopted = state.raw_post(state.base_url + f"/edits/{preview['proposal']['id']}/confirm", json={"request_id": str(uuid4())})
        assert adopted.status_code == 200, adopted.text
        wait_until(lambda: len(models) == 3)
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == "no_progress")
    final = state.conversations.get_turn(models[-1]["turn_id"])
    assert final["status"] == "completed" and final["result"]["goal_business_actions"] == []
    if not damaged:
        assert final["result"]["response"]["media_job"]["status"] == "succeeded"
        assert final["result"]["response"]["media_job"]["result"]["reused_completed_source"] is True
    assert len(state.calls) == 1 and main._goal_service().reconcile() == 0
    assert job_ids() == original_job_ids | {receipts[0]["job_id"]}
    if not damaged:
        chat = state.conversations.get(chat["id"], state.workspace_id, state.member.member_id)
        new_instruction = "新的独立指令：重新生成同名图片"
        chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, new_instruction, chat["draft_version"])
        task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
        version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
        new = state.raw_post(state.base_url + "/assistant/turn", json={"conversation_id": chat["id"], "message": new_instruction,
            "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"], "context_stage": "montage",
            "selected_object_refs": [], "expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence})
        assert new.status_code == 200 and new.json()["media_job"]["status"] == "queued", new.text
        assert new.json()["media_job"]["job_id"] != receipts[0]["job_id"]
        assert job_ids() == original_job_ids | {receipts[0]["job_id"], new.json()["media_job"]["job_id"]}
        assert len(state.calls) == 1


@pytest.mark.parametrize("shutdown,operation", [(False, "ChangeCrop"), (True, "ChangeCrop"), (False, "DeleteClip")])
def test_normal_endpoint_preview_adoption_resumes_original_instruction_once(state, monkeypatch, request, shutdown, operation):
    state.finish()  # Old history must never become an inferred goal.
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    calls, jobs = [], []
    if operation == "DeleteClip":
        def no_media(*_):
            pytest.fail("this continuation receipt test must not run a media executor")
        execution = CreationExecution(state.production, run_montage_plan=no_media,
                                      run_render=no_media, render_is_valid=lambda *_: True)
        monkeypatch.setattr(main, "creation_execution", execution)
    def model(_settings, **kwargs):
        calls.append(kwargs)
        if kwargs.get("continuation_context"):
            assert kwargs["stage"] == "montage"  # Adopted review legitimately became montage.
            assert kwargs["task_version"]["timeline_version_id"] != state.version.id
            event = kwargs["continuation_context"]["business_event"]
            assert event["status"] == ("succeeded" if operation == "DeleteClip" and len(calls) == 3 else "confirmed")
            if event["status"] == "confirmed":
                assert event["waiting_reason"] == "当前草稿已保存；这次修改尚未反映到成片。"
                assert "等待明确生成决定" not in event["waiting_reason"]
            assert kwargs["selected_refs"] == ([] if operation == "DeleteClip" else [f"clip:{state.clip.id}"])
            if operation == "DeleteClip":
                assert kwargs["task_version"]["removed_selected_refs"] == [f"clip:{state.clip.id}"]
                if len(calls) == 2:
                    current = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
                    script = state.production.script_records.get_creation_task_script(state.workspace_id, current.confirmed_script_id)
                    plan = state.production.plan_records.get_creation_plan(state.workspace_id, current.plan_id)
                    actor = main._require_permit_actor(state.request, state.workspace_id)
                    source, prior = main._assistant_execution_source(current.id,state.request,kwargs["turn_id"],"start",revision_digest(script,plan))
                    assert prior is None
                    jobs.append(execution.start(state.workspace_id, current.id, script.id, revision_digest(script, plan), actor=actor))
                    status=main._bind_assistant_execution_result(current.id,state.request,kwargs["turn_id"],
                        main._creation_execution_status(state.workspace_id,current.id),source=source)
                    return "已按删除后的当前版本登记制作。", {"kind": "execution_action", "action": "start",
                        "status": status}
                assert event["phase"] == "execution"
            if not shutdown or len(calls) == 3:
                return "实际采用结果已读取，原要求完成。", None
            next_preview = state.raw_post(state.base_url + "/edits/exact/preview", json={
                "operation_type": "ChangeCrop", "clip_id": state.clip.id, "fit_mode": "fill", "crop_x": .3, "crop_y": .5,
                "request_id": str(uuid4()), "assistant_turn_id": kwargs["turn_id"],
                "expected_timeline_version_id": kwargs["task_version"]["timeline_version_id"],
                "expected_timeline_sequence": kwargs["task_version"]["timeline_sequence"]})
            assert next_preview.status_code == 202, next_preview.text
            assert pi._turn_stop_events[kwargs["turn_id"]].wait(4)
            raise main.AssistantStopped()
        preview = state.raw_post(state.base_url + "/edits/exact/preview", json={
            "operation_type": operation, "clip_id": state.clip.id,
            **({"fit_mode": "fill", "crop_x": .2, "crop_y": .5} if operation == "ChangeCrop" else {}),
            "request_id": str(uuid4()), "assistant_turn_id": kwargs["turn_id"],
            "expected_timeline_version_id": state.version.id, "expected_timeline_sequence": state.version.sequence})
        assert preview.status_code == (200 if operation == "DeleteClip" else 202), preview.text
        return "原目标的真实预览已提交。", (preview.json() if operation == "DeleteClip" else
            {"kind": "media_job", "media_job": preview.json()})
    monkeypatch.setattr(main, "run_task_turn", model)
    chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
    raw = "将当前镜头裁剪向左；采用后继续读取实际结果"
    chat = select_clip(state,chat)
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    response = state.raw_post(state.base_url + "/assistant/turn", json={
        "conversation_id": chat["id"], "message": raw, "client_request_id": str(uuid4()),
        "expected_draft_version": chat["draft_version"], "expected_reference_version":chat["reference_version"], "context_stage": "review",
        "selected_object_refs": [f"clip:{state.clip.id}"],
        "expected_timeline_version_id": state.version.id, "expected_timeline_sequence": state.version.sequence})
    assert response.status_code == 200, response.text
    root_id = response.json()["turn_id"]
    assert state.conversations.get_task_goal(root_id)["original_instruction"] == raw
    main.start_assistant_continuation_driver()
    if operation != "DeleteClip":
        assert state.runtime.run_once()
    root = wait_until(lambda: state.conversations.get_turn(root_id)["result"]["response"].get("edit_preview"))
    assert len(calls) == 1  # Actual proposal waits for the user's adoption.
    adoption = state.raw_post(state.base_url + f"/edits/{root['proposal']['id']}/confirm", json={"request_id": str(uuid4())})
    assert adoption.status_code == 200, adoption.text
    wait_until(lambda: len(calls) == 2)
    if operation == "DeleteClip":
        wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == "waiting_result")
        assert state.conversations.get_task_goal(root_id)["target_snapshot"]["removed_selected_refs"] == [f"clip:{state.clip.id}"]
        state.production.job_records.claim_job_run(state.workspace_id, jobs[0].id)
        # A later actual terminal receipt must retain the prior proved deletion.
        state.production.job_records.finish_job_run(state.workspace_id, jobs[0].id, status="succeeded")
        main._wake_assistant_continuations()
        wait_until(lambda: len(calls) == 3)
    if shutdown:
        child_id = calls[-1]["turn_id"]
        wait_until(lambda: len(state.production.job_records.list_job_runs(state.workspace_id, state.task.id)) == 2)
        main.stop_assistant_continuation_driver()
        child = state.conversations.get_turn(child_id)
        assert child["status"] == "failed" and child["result"]["reason"] == "api_shutdown"
        assert state.conversations.get_task_goal(root_id)["status"] == "waiting_unknown"
        from assistant_turn_control_fixtures import isolate_assistant_turn_control
        isolate_assistant_turn_control(monkeypatch, request)
        main.start_assistant_continuation_driver()
        assert main._goal_service().reconcile() == 0 and len(calls) == 2
        assert state.runtime.run_once()
        proposal = wait_until(lambda: state.conversations.get_turn(child_id)["result"].get("response", {}).get("edit_preview"))
        second = state.raw_post(state.base_url + f"/edits/{proposal['proposal']['id']}/confirm", json={"request_id": str(uuid4())})
        assert second.status_code == 200, second.text
        wait_until(lambda: len(calls) == 3, diagnostic=lambda: {"goal":state.conversations.get_task_goal(root_id),
            "turns":state.conversations.list_turns(state.conversation["id"]),"calls":len(calls),
            "task_events":[main.jsonable_encoder(event) for event in state.production.execution_records.list_task_events(state.workspace_id,state.task.id)]})
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == "no_progress")
    turns = state.conversations.list_turns(chat["id"])
    assert turns[-1]["user_message"] == "" and calls[-1]["message"] == raw
    assert turns[-1]["status"] == "completed"
    main.stop_assistant_continuation_driver()
    main.start_assistant_continuation_driver()
    assert main._goal_service().reconcile() == 0 and len(calls) == (3 if shutdown or operation == "DeleteClip" else 2)


@pytest.mark.parametrize("code", [403, 409, 422])
def test_original_request_correction_requires_actual_422_no_side_effect(edit_result_state, monkeypatch, code):
    state = edit_result_state
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    request_id = uuid5(NAMESPACE_URL, json.dumps([state.task.id, state.turn["id"], "validation"], separators=(",", ":")))
    locator = main._resolve_assistant_edit_request(state.task.id, state.request, state.turn["id"], {
        "kind": "edit_request_status", "task_id": state.task.id, "request_id": str(request_id),
        "query_url": state.base_url + f"/assistant/edit-requests/{request_id}?phase=preview",
        "status": "not_submitted", "assistant_turn_id": state.turn["id"], "tool_call_id": "validation"})
    # The host derives the deterministic request id; use that exact receipt.
    request_id = locator.request_id
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    event_id, fresh = main._task_edit_receipt_http_adapter().claim_edit_request(task, state.member.member_id, request_id, "preview", {"fixture": "validation"})
    assert fresh
    main._task_edit_receipt_http_adapter().save_edit_request_result(task, event_id, main.JSONResponse(status_code=code, content={"detail": "actual validation"}))
    state.finish(edit_request=locator.model_dump(mode="json"))
    goal = main._goal_service().register_turn(state.turn["id"])
    event = {"type": "business_result", "event_id": "validation", "task_id": state.task.id, "phase": "request",
        "base_version_id": state.version.id, "status": "rejected", "edit_request": locator.model_dump(mode="json")}
    verified = main._goal_readiness_use_case().verify(goal, event)
    assert verified["status"] == ("ready" if code == 422 else "invalidated")
    if code == 422:
        assert verified["no_side_effect"] is True


@pytest.mark.parametrize("stale", [False, True])
def test_normal_execution_locator_terminal_event_is_consumed_once_and_fences_versions(state, monkeypatch, stale):
    state.finish()
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    def no_media(*_):
        pytest.fail("this receipt test must not run any media executor")
    execution = CreationExecution(state.production, run_montage_plan=no_media,
                                  run_render=no_media, render_is_valid=lambda *_: True)
    monkeypatch.setattr(main, "creation_execution", execution)
    calls, jobs = [], []
    def model(_settings, **kwargs):
        calls.append(kwargs)
        if kwargs.get("continuation_context"):
            assert kwargs["continuation_context"]["business_event"]["phase"] == "execution"
            assert kwargs["continuation_context"]["current_facts"]["execution_status"]["job"]["status"] == "succeeded"
            json.dumps(kwargs["continuation_context"])
            return "已读取原作业与实际登记输出。", None
        current = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
        script = state.production.script_records.get_creation_task_script(state.workspace_id, current.confirmed_script_id)
        plan = state.production.plan_records.get_creation_plan(state.workspace_id, current.plan_id)
        actor = main._require_permit_actor(state.request, state.workspace_id)
        source, prior = main._assistant_execution_source(current.id,state.request,kwargs["turn_id"],"start",revision_digest(script,plan))
        assert prior is None
        jobs.append(execution.start(state.workspace_id, current.id, script.id, revision_digest(script, plan), actor=actor))
        status=main._bind_assistant_execution_result(current.id,state.request,kwargs["turn_id"],
            main._creation_execution_status(state.workspace_id,current.id),source=source)
        return "已登记原制作作业。", {"kind": "execution_action", "action": "start",
                                    "status":status}
    monkeypatch.setattr(main, "run_task_turn", model)
    chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
    raw = "生成后查看实际成片"
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    response = state.raw_post(state.base_url + "/assistant/turn", json={
        "conversation_id": chat["id"], "message": raw, "client_request_id": str(uuid4()),
        "expected_draft_version": chat["draft_version"], "context_stage": "review", "selected_object_refs": [],
        "expected_timeline_version_id": state.version.id, "expected_timeline_sequence": state.version.sequence})
    assert response.status_code == 200, response.text
    root_id = response.json()["turn_id"]
    assert state.conversations.get_turn(root_id)["result"]["goal_business_actions"] == [
        {"kind": "execution_status", "id": jobs[0].id}]
    # Explicit terminal row fixture; real rendering is root's separate QA.
    state.production.job_records.claim_job_run(state.workspace_id, jobs[0].id)
    state.production.job_records.finish_job_run(state.workspace_id, jobs[0].id, status="succeeded")
    if stale:
        state.production.timeline_records.create_timeline_version(replace(state.version, id="unrelated-version", sequence=2,
                                                         parent_version_id=state.version.id, snapshot={}))
    main.start_assistant_continuation_driver()
    expected = "invalidated" if stale else "no_progress"
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == expected)
    assert len(calls) == (1 if stale else 2)
    assert main._assistant_execution_projector()() == 0
    assert main._goal_service().reconcile() == 0
    assert len([job for job in state.production.job_records.list_job_runs(state.workspace_id, state.task.id)
                if job.kind == "creation_task_execution"]) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("continuation_failure", [False, True])
def test_actual_start_is_persisted_before_model_failure_then_original_job_continues(state, monkeypatch, streaming, continuation_failure):
    state.finish()
    material_execution_input(state)
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    execution = CreationExecution(state.production, run_montage_plan=lambda *_: None,
        run_render=lambda *_: None, render_is_valid=lambda *_: True)
    monkeypatch.setattr(main, "creation_execution", execution)
    monkeypatch.setattr(main, "submit_creation_stage", lambda *_: "isolated-receipt-test; no media execution")
    calls, job_ids, turn_ids = [], [], []
    def model(_settings, **kwargs):
        calls.append(kwargs)
        if kwargs.get("continuation_context"):
            assert kwargs["continuation_context"]["current_facts"]["execution_status"]["job"]["id"] == job_ids[0]
            if continuation_failure:
                raise pi.AssistantUnavailable("业务回执 script_goal_conflict；同步核查后模型等待超时。来源 /Volumes/private/source.wav",
                    cause="tool_failed", service_contacted=False)
            return "已回读原制作作业，尚无语义或听感结论。", None
        turn_id = kwargs["turn_id"]
        turn_ids.append(turn_id)
        preview = state.client.get(state.base_url + "/execution/preview").json()
        started = state.raw_post(state.base_url + "/execution/start?assistant_turn_id=" + turn_id, json={
            "script_id":preview["script_id"], "input_digest":preview["input_digest"], "requirements_confirmed":True})
        assert started.status_code == 202, started.text
        job_id = started.json()["job"]["id"]
        job_ids.append(job_id)
        # The real route already owns the locator/proof while this model is still running.
        located = state.production.execution_records.get_task_event(state.workspace_id, "event_assistant_execution_locator_" + turn_id)
        assert located.job_id == job_id and located.payload["input_digest"] == preview["input_digest"]
        assert state.conversations.get_task_goal(turn_id)["status"] == "waiting_result"
        assert state.conversations.get_turn(turn_id)["result"]["goal_business_actions"] == [{"kind":"execution_status", "id":job_id}]
        replayed = state.raw_post(state.base_url + "/execution/start?assistant_turn_id=" + turn_id, json={
            "script_id":preview["script_id"], "input_digest":preview["input_digest"], "requirements_confirmed":True})
        assert replayed.status_code == 202 and replayed.json()["job"]["id"] == job_id
        if kwargs.get("on_event"):
            kwargs["on_event"]({"type":"tool_end", "tool_id":42, "label":"原制作已提交", "ok":False,
                "summary":"模型回复随后中断", "failure":{"reason":"模型中断，输入位于 /Volumes/private/source.wav",
                    "repair_action":"查询原制作作业，不重复提交", "raw_payload":"never expose"}})
        raise pi.AssistantUnavailable("isolated model interruption after actual start")
    monkeypatch.setattr(main, "run_task_turn", model)
    chat = state.conversations.get(state.conversation["id"], state.workspace_id, state.member.member_id)
    text = "生成后检查当前输出"
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, text, chat["draft_version"])
    response = state.raw_post(state.base_url + "/assistant/turn", headers={"Accept":"text/event-stream"} if streaming else {}, json={
        "conversation_id":chat["id"], "message":text, "client_request_id":str(uuid4()),
        "expected_draft_version":chat["draft_version"], "context_stage":"review", "selected_object_refs":[],
        "expected_timeline_version_id":state.version.id, "expected_timeline_sequence":state.version.sequence})
    assert response.status_code == (200 if streaming else 503), response.text
    root_id = turn_ids[0]
    saved = state.conversations.get_turn(root_id)
    assert saved["status"] == "failed"
    assert saved["result"]["response"]["execution_status"]["job"]["id"] == job_ids[0]
    if streaming:
        projected = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        failure = next(event['failure'] for event in projected if event.get('tool_id') == 42)
        historical = next(event['failure'] for event in saved['events'] if event.get('tool_id') == 42)
        assert failure == historical and set(failure) == {'reason','repair_action'}
        assert '/Volumes/' not in failure['reason'] and 'raw_payload' not in response.text
    state.production.job_records.claim_job_run(state.workspace_id, job_ids[0])
    state.production.job_records.finish_job_run(state.workspace_id, job_ids[0], status="succeeded")
    original = state.client.get(state.base_url + "/execution", params={"job_id":job_ids[0]})
    assert original.status_code == 200 and original.json()["job"]["id"] == job_ids[0]
    main.start_assistant_continuation_driver()
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == (
        "waiting_unknown" if continuation_failure else "no_progress"), diagnostic=lambda: {
            "goal":state.conversations.get_task_goal(root_id),
            "turns":state.conversations.list_turns(state.conversation["id"]),"calls":len(calls)})
    if continuation_failure:
        child = state.conversations.get_turn(calls[-1]["turn_id"])
        assert child["status"] == "failed" and child["result"]["kind"] == "failure"
        assert "script_goal_conflict" in child["result"]["message"]
        assert child["assistant_text"] == child["result"]["message"]
        assert "/Volumes/" not in child["assistant_text"]
        assert child["result"].get("reason") != "goal_runner_interrupted"
    assert len(calls) == 2 and main._assistant_execution_projector()() == 0
    assert len([job for job in state.production.job_records.list_job_runs(state.workspace_id, state.task.id) if job.kind == "creation_task_execution"]) == 1


@pytest.mark.parametrize("submitted", [False, True])
def test_actual_execution_read_registers_goal_only_for_own_submitted_job(state, monkeypatch, submitted):
    state.finish()
    material_execution_input(state)
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _:SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    execution = CreationExecution(state.production, run_montage_plan=lambda *_:None,
        run_render=lambda *_:None, render_is_valid=lambda *_:True)
    monkeypatch.setattr(main, "creation_execution", execution)
    monkeypatch.setattr(main, "submit_creation_stage", lambda *_:"receipt test; no media")
    preview = state.client.get(state.base_url + "/execution/preview").json()
    body = {"script_id":preview["script_id"], "input_digest":preview["input_digest"], "requirements_confirmed":True}
    job_ids, calls = [], []
    if not submitted:
        old = state.raw_post(state.base_url+"/execution/start", json=body)
        assert old.status_code == 202, old.text
        job_ids.append(old.json()["job"]["id"])
        state.production.job_records.claim_job_run(state.workspace_id,job_ids[0])
        state.production.job_records.finish_job_run(state.workspace_id,job_ids[0],status="failed",failure_reason="isolated old failed job",
            impact="No output from this isolated fixture",repair_action="Read the original job")
    def model(_settings, **kwargs):
        calls.append(kwargs)
        if kwargs.get("continuation_context"):
            assert kwargs["continuation_context"]["current_facts"]["execution_status"]["job"]["id"] == job_ids[0]
            return "已读取原作业终态。", None
        events = []
        if submitted:
            accepted = state.raw_post(state.base_url+"/execution/start?assistant_turn_id="+kwargs["turn_id"],json=body)
            assert accepted.status_code == 202, accepted.text
            job_ids.append(accepted.json()["job"]["id"])
            events.append({"type":"tool_execution_end", "toolName":"start_creation_execution", "isError":False,
                "result":{"details":{"kind":"execution_action", "action":"start", "status":accepted.json()}}})
        read = state.client.get(state.base_url+"/execution",params={"job_id":job_ids[0]})
        assert read.status_code == 200, read.text
        events += [{"type":"tool_execution_end", "toolName":"codemode", "isError":False,
            "result":{"details":{"incomplete_calls":False,"business_receipts":[{"tool_name":"read_creation_execution",
                "tool_call_id":"nested-read", "is_error":False,"result":{"kind":"execution_result", "status":read.json()}}]}}},
            {"type":"message_end","message":{"role":"assistant", "stopReason":"stop",
                "content":[{"type":"text","text":"已提交恢复，已经完成。"}]}}, {"type":"agent_settled"}]
        return project_event_fixture("\n".join(json.dumps(item) for item in events))
    monkeypatch.setattr(main, "run_task_turn", model)
    chat = state.conversations.get(state.conversation["id"],state.workspace_id,state.member.member_id)
    chat = state.conversations.save_draft(chat["id"],state.workspace_id,state.member.member_id,"继续生成并检查输出",chat["draft_version"])
    response = state.raw_post(state.base_url+"/assistant/turn",json={"conversation_id":chat["id"],"message":chat["draft_text"],
        "client_request_id":str(uuid4()),"expected_draft_version":chat["draft_version"],"context_stage":"review",
        "selected_object_refs":[],"expected_timeline_version_id":state.version.id,"expected_timeline_sequence":state.version.sequence})
    assert response.status_code == 200,response.text
    result = response.json()
    turn = state.conversations.get_turn(result["turn_id"])
    assert result["execution_status"]["job"]["id"] == job_ids[0]
    if not submitted:
        assert result["execution_action"] is None and "本轮未提交新的开始或恢复请求" in result["reply"]
        assert turn["result"]["goal_business_actions"] == []
        assert state.conversations.get_task_goal(turn["id"]) is None
        with pytest.raises(KeyError):
            state.production.execution_records.get_task_event(state.workspace_id,"event_assistant_execution_locator_"+turn["id"])
        assert main._assistant_execution_projector()() == 0
    else:
        assert result["execution_action"] == "start" and "本轮未提交" not in result["reply"]
        assert turn["result"]["goal_business_actions"] == [{"kind":"execution_status", "id":job_ids[0]}]
        assert state.conversations.get_task_goal(turn["id"])["status"] == "waiting_result"
        state.production.job_records.claim_job_run(state.workspace_id,job_ids[0])
        state.production.job_records.finish_job_run(state.workspace_id,job_ids[0],status="succeeded")
        assert main._assistant_execution_projector()() == 1
        assert main._goal_service().reconcile() == 1 and len(calls)==2
        assert state.conversations.get_task_goal(turn["id"])["status"] == "no_progress"
    assert len([job for job in state.production.job_records.list_job_runs(state.workspace_id,state.task.id)
        if job.kind=="creation_task_execution"]) == 1


def test_consumed_input_observes_existing_job_then_resumes_without_starting_again(state,monkeypatch):
    state.finish()
    material_execution_input(state)
    monkeypatch.setattr(main,"quality_check_repo",QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main,"_workspace_settings_for",lambda _:SimpleNamespace(
        effective_llm_provider="fixture",effective_llm_model="fixture",effective_llm_api_key=""))
    execution=CreationExecution(state.production,run_montage_plan=lambda *_:None,
        run_render=lambda *_:None,render_is_valid=lambda *_:True)
    monkeypatch.setattr(main,"creation_execution",execution)
    monkeypatch.setattr(main,"submit_creation_stage",lambda *_:"receipt test; no media")
    calls,job_ids,input_ids=[],[],[]
    def model(_settings,**kwargs):
        calls.append(kwargs)
        if kwargs.get("continuation_context"):
            assert kwargs["continuation_context"]["source_input_id"] == input_ids[0]
            assert kwargs["continuation_context"]["current_facts"]["execution_status"]["job"]["id"] == job_ids[0]
            assert kwargs["message"] == "等当前作业结束后检查结果，不要重新制作"
            return "已回读当前原作业的实际终态。",None
        turn_id=kwargs["turn_id"]
        preview=state.client.get(state.base_url+"/execution/preview").json()
        started=state.raw_post(state.base_url+"/execution/start?assistant_turn_id="+turn_id,json={
            "script_id":preview["script_id"],"input_digest":preview["input_digest"],"requirements_confirmed":True})
        assert started.status_code == 202,started.text
        job_ids.append(started.json()["job"]["id"])
        message="等当前作业结束后检查结果，不要重新制作"
        chat=state.conversations.get(state.conversation["id"],state.workspace_id,state.member.member_id)
        draft=state.conversations.save_draft(chat["id"],state.workspace_id,state.member.member_id,message,chat["draft_version"])
        input_id=str(uuid4())
        input_ids.append(input_id)
        accepted=state.raw_post(f"/api/assistant/conversations/{chat['id']}/inputs",json={"target_turn_id":turn_id,
            "client_request_id":input_id,"message":message,"mode":"follow_up","expected_draft_version":draft["draft_version"],
            "expected_reference_version":draft["reference_version"]})
        assert accepted.status_code == 200,accepted.text
        handler=kwargs["input_handler"]
        assert handler("next",mode="any")["input_id"] == input_id
        assert handler("consume",input_id)["environment"]["AI_VIDEO_DIARY_AGENT_INPUT_ID"] == input_id
        understood=state.raw_post(state.base_url+"/assistant/input-understanding",json={
            "assistant_turn_id":turn_id,"input_id":input_id,"kind":"effective_modification",
            "summary":"仅在原作业完成后观察其结果，禁止重新制作或修改作品",
            "target_refs":[f"task:{state.task.id}",f"job:{job_ids[0]}",f"timeline:{state.version.id}"]},
            headers={"X-Assistant-Input-ID":input_id})
        assert understood.status_code == 200,understood.text
        read=state.client.get(state.base_url+"/execution",params={"job_id":job_ids[0]})
        assert read.status_code == 200,read.text
        handler("complete",input_id)
        details={"kind":"execution_result","status":read.json(),"input_id":input_id,
            "input_turn_id":turn_id,"invocation_input_id":input_id}
        return project_event_fixture("\n".join(json.dumps(item) for item in [
            {"type":"tool_execution_end","toolName":"start_creation_execution","isError":False,
                "result":{"details":{"kind":"execution_action","action":"start","status":started.json(),"invocation_input_id":None}}},
            {"type":"tool_execution_end","toolName":"read_creation_execution","isError":False,"result":{"details":details}},
            {"type":"message_end","message":{"role":"assistant","stopReason":"stop","content":[{"type":"text","text":"已查询原作业"}]}},
            {"type":"agent_settled"}]))
    monkeypatch.setattr(main,"run_task_turn",model)
    chat=state.conversations.get(state.conversation["id"],state.workspace_id,state.member.member_id)
    chat=state.conversations.save_draft(chat["id"],state.workspace_id,state.member.member_id,"开始制作后检查结果",chat["draft_version"])
    response=state.raw_post(state.base_url+"/assistant/turn",json={"conversation_id":chat["id"],"message":chat["draft_text"],
        "client_request_id":str(uuid4()),"expected_draft_version":chat["draft_version"],"expected_reference_version":0,
        "context_stage":"review","selected_object_refs":[],"expected_timeline_version_id":state.version.id,"expected_timeline_sequence":state.version.sequence})
    assert response.status_code == 200,response.text
    root_id=response.json()["turn_id"]
    assert response.json()["execution_action"] is None
    goal=state.conversations.get_task_goal(root_id)
    assert goal["source_input_id"] == input_ids[0] and goal["revision"] == 1
    located=state.production.execution_records.get_task_event(state.workspace_id,execution_locator_id(root_id,input_ids[0]))
    assert located.payload["source"]["action"] == "observe" and located.job_id == job_ids[0]
    state.production.job_records.claim_job_run(state.workspace_id,job_ids[0])
    state.production.job_records.finish_job_run(state.workspace_id,job_ids[0],status="succeeded")
    assert main._assistant_execution_projector()() == 2
    assert main._goal_service().reconcile() == 1 and len(calls) == 2
    assert main._goal_service().reconcile() == 0
    assert len([job for job in state.production.job_records.list_job_runs(state.workspace_id,state.task.id) if job.kind == "creation_task_execution"]) == 1
    assert state.production.task_records.get_creation_task(
        state.workspace_id,state.task.id).current_timeline_version_id == state.version.id


@pytest.mark.parametrize("mutation", ["none", "watched_input", "older_goal_locator", "source_trimmed", "scripted_short", "hash", "version", "preview", "foreign_goal", "no_video", "pending", "unknown"])
def test_bounded_checkpoint_requires_same_goal_current_final_hash_and_no_pending(monkeypatch, mutation, tmp_path):
    # Real SQL selection with host/media-reader seams; this is not media acceptance.
    import sqlite3
    monkeypatch.setattr(main,'_workspace_settings_for',lambda _:SimpleNamespace(effective_llm_api_key=''))
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript('CREATE TABLE device_work_requests(id,workspace_id,owner_id,state);'
        'CREATE TABLE task_events(id,workspace_id,task_id,job_id,event_type,payload_json);'
        'CREATE TABLE job_runs(id,workspace_id,task_id,kind,status);')
    from app.conversation_repository import ConversationRepository
    conversations = ConversationRepository(tmp_path/"output-checkpoint.sqlite3")
    chat = conversations.create('workspace','member','task','task')
    chat = conversations.save_draft(chat['id'],'workspace','member','核对当前成片输出',chat['draft_version'])
    turn,_ = conversations.create_turn_idempotent(chat['id'],'workspace','member',chat['draft_text'],str(uuid4()),
        chat['draft_version'],request_context={'kind':'task','task_id':'task','stage':'review',
            'target_snapshot':{'timeline_version_id':'final','timeline_sequence':2}})
    conversations.finish_turn(turn['id'],'completed','等待实际输出观察',
        {'kind':'task_turn','response':{'media_job':{'job_id':'observation','status':'queued'}}})
    assert conversations.register_task_goal(turn['id'])
    goal = conversations.get_task_goal(turn['id'])
    output = {'artifact_id':'video-artifact','artifact_kind':'rendered_video','sha256':'a'*64,
        'registered_width':1080,'registered_height':1920,'size_bytes':100,'producer_job_id':'render-child',
        'logical_ref':'artifacts/task/video.mp4','mtime_ns':1,'ctime_ns':2}
    source = {'job_id':'execution','input_digest':'b'*64,'source':{'assistant_turn_id':'producer',
        'expected_timeline_version_id':'base','conversation_id':chat['id'],'member_id':'member'}}
    connection.execute('INSERT INTO device_work_requests VALUES(?,?,?,?)',('render-request','workspace','execution','done'))
    connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('locator','workspace','task','execution',
        'assistant_execution_located',json.dumps(source)))
    if mutation == 'older_goal_locator':
        for identifier, patch in [('foreign-locator', {'assistant_turn_id':'other-producer'}),
                                  ('wrong-base-locator', {'expected_timeline_version_id':'old-base'})]:
            connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',(identifier,'workspace','task','execution',
                'assistant_execution_located',json.dumps({**source,'source':{**source['source'],**patch}})))
    start = {'arguments':{'_typed_source':{'assistant_turn_id':'observer'}},'input_facts':{'output':dict(output)}}
    if mutation == 'watched_input':
        goal.update(source_input_id='watched-input',source_input_turn_id=goal['original_turn_id'])
        watched={**source,'source':{**source['source'],'input_id':'watched-input','input_turn_id':goal['original_turn_id'],'action':'observe'}}
        connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('watch-locator','workspace','task','execution',
            'assistant_execution_located',json.dumps(watched)))
        start['arguments']['_typed_source'].update(input_id='watched-input',input_turn_id=goal['source_input_turn_id'])
    result = {'target':{'kind':'final'},'output_role':'composed_output','timeline_version_id':'final',
        'timeline_sequence':2,'measurement_scope':'actual_output_bounded_frames',
        'output':{key:value for key,value in output.items() if key not in {'logical_ref','mtime_ns','ctime_ns'}},
        'unavailable':[{'scope':'visual_semantics','reason':'No semantic validator'}]}
    if mutation == 'hash':
        result['output']['sha256'] = 'c'*64
    elif mutation == 'version':
        result['timeline_version_id'] = 'older'
    elif mutation == 'preview':
        result['target']['kind'] = 'media_job'
    elif mutation in {'pending','unknown'}:
        source = {'assistant_turn_id':'pending','conversation_id':chat['id'],'member_id':'member'}
        if mutation == 'pending':
            connection.execute('INSERT INTO job_runs VALUES(?,?,?,?,?)',('pending-job','workspace','task',main.TASK_MEDIA_KIND,'queued'))
            payload = {'arguments':{'_typed_source':source}}
            connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('event_pending-job_start','workspace','task','pending-job','task_media_submitted',json.dumps(payload)))
        else:
            payload = {'source':source,'locator':{'status':'unknown','request_id':str(uuid4()),'phase':'preview'}}
            connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('unknown','workspace','task',None,'assistant_edit_request_located',json.dumps(payload)))
    repository=SimpleNamespace(_connect=lambda:connection,execution_records=SimpleNamespace(get_task_event=lambda *_:SimpleNamespace(payload=start)))
    repository.goal_render_request_row = lambda ws, identifier: connection.execute(
        'SELECT owner_id,state FROM device_work_requests WHERE workspace_id=? AND id=?', (ws, identifier)).fetchone()
    from types import MethodType
    from app.creation.execution_records import ExecutionRecordStore
    repository.execution_records._connect=repository._connect
    repository.execution_records.goal_output_provenance_rows=MethodType(ExecutionRecordStore.goal_output_provenance_rows,repository.execution_records)
    monkeypatch.setattr(main,'production_repo',repository)
    monkeypatch.setattr(main,'conversation_repo',SimpleNamespace(get_task_goal=lambda identifier:
        {'original_turn_id':'foreign' if identifier=='other-producer' or mutation=='foreign_goal' and identifier=='producer' else goal['original_turn_id']}))
    monkeypatch.setattr(main,'_registered_observation_output',lambda *_:(None,output,{'kind':'composed_output'}))
    business = SimpleNamespace(read_execution=lambda *_args, **_kwargs:
        {'job':{'id':'execution','status':'succeeded','input_digest':'b'*64},'video_ready':mutation!='no_video'})
    request_reads=[]
    def read_request(*args):
        request_reads.append(args)
        return {'status':'unknown'}
    monkeypatch.setattr(main,'_goal_request_metadata',read_request)
    task=SimpleNamespace(workspace_id='workspace',id='task')
    version=SimpleNamespace(id='final',workspace_id='workspace',task_id='task',sequence=2,parent_version_id='base',snapshot={
        'creation_render_request_id':'render-request','render_input_timeline_version_id':'base'})
    if mutation in {'source_trimmed', 'scripted_short'}:
        from dataclasses import asdict
        from app.montage import CreationTaskMontagePlan
        version.snapshot['montage'] = asdict(CreationTaskMontagePlan(
            id='montage', workspace_id='workspace', task_id='task',
            script_id='script' if mutation == 'scripted_short' else None,
            script_title='现场', status='ready', cues=[], clips=[], duration_seconds=15.64,
            low_confidence_clip_count=0))
        goal['creation_adoption'] = {'source': {}}
        result['media'] = {'duration_seconds': 15.633333}
        monkeypatch.setattr(main, '_goal_readiness_plan', lambda _: SimpleNamespace(
            source_media={'source_start_seconds': 0, 'source_end_seconds': 16},
            duration_target_seconds=16))
    checkpoint=main._goal_readiness_use_case().output_checkpoint(goal,{'phase':'observation','status':'succeeded'},
        {'media_job':{'job_id':'observation','status':'succeeded','result':result}},task,version,None,business)
    if mutation == 'none':
        saved_observation = {'job_id': 'observation', 'status': 'succeeded', 'stale': False, 'result': result}
        late_execution = main._goal_readiness_use_case().output_checkpoint(goal,
            {'phase': 'execution', 'status': 'succeeded'},
            {'observations': {'observations': [saved_observation]}}, task, version, None, business)
        assert late_execution == checkpoint
        saved_observation['stale'] = True
        assert main._goal_readiness_use_case().output_checkpoint(goal,
            {'phase': 'execution', 'status': 'succeeded'},
            {'observations': {'observations': [saved_observation]}}, task, version, None, business) is None
    if mutation in {'none','watched_input','older_goal_locator','source_trimmed','scripted_short'}:
        assert checkpoint['scope']=='production_and_output_observation' and checkpoint['sha256']=='a'*64
        assert checkpoint['unavailable']==result['unavailable']
        if mutation in {'source_trimmed', 'scripted_short'}:
            assert checkpoint['status'] == ('completed' if mutation == 'source_trimmed' else 'incomplete')
            assert checkpoint['delivery_duration']['target_duration_seconds'] == (15.64 if mutation == 'source_trimmed' else 16)
    else:
        assert checkpoint is None
    expected = conversations.get_task_goal(turn['id'])
    if checkpoint is not None:
        assert conversations.record_goal_output_checkpoint(turn['id'],checkpoint,expected_goal=expected)
        assert ConversationRepository(conversations.database_path).get_task_goal(turn['id'])['completed_output_checkpoint']==checkpoint
        assert not conversations.record_goal_output_checkpoint(turn['id'],{'status':'untrusted'},
            expected_goal={**expected,'revision':expected.get('revision',0)+1})
        assert conversations.get_task_goal(turn['id'])['completed_output_checkpoint']==checkpoint
    else:
        assert 'completed_output_checkpoint' not in conversations.get_task_goal(turn['id'])
    if mutation=='unknown':
        assert len(request_reads)==1 and connection.execute('SELECT count(*) FROM job_runs').fetchone()[0]==0
    connection.close()


@pytest.fixture
def execution_http_state(tmp_path, monkeypatch, request):
    """Persist only the HTTP source/version facts; no media or execution dispatch."""
    from dataclasses import asdict
    from fastapi.testclient import TestClient
    from app.conversation_repository import ConversationRepository
    from app.creation.contracts import TimelineVersion
    from app.local_members import LocalMemberService
    from test_creation_execution import _seed

    root = tmp_path / 'execution-http'
    root.mkdir()
    repo, task_id, script_id, _ = _seed(root, workspace_id='local', voice=getattr(request, 'param', True))
    members = LocalMemberService(repo.database_path)
    member, token = members.join(members.create_invite_code(), 'HTTP来源隔离成员')
    task = repo.task_records.get_creation_task('local', task_id)
    task.owner_user_id = member.member_id
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
            (json.dumps(asdict(task), default=str), task.id))
    version = repo.timeline_records.create_timeline_version(TimelineVersion(
        'http-source-version', 'local', task.id, None, 1, {}, None))
    conversations = ConversationRepository(repo.database_path)
    chat = conversations.create('local', member.member_id, 'task', task.id)
    chat = conversations.save_draft(chat['id'], 'local', member.member_id, '核对当前作品', chat['draft_version'])
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', member.member_id,
        chat['draft_text'], str(uuid4()), chat['draft_version'], request_context={
            'kind':'task', 'task_id':task.id, 'stage':'review',
            'target_snapshot':{'timeline_version_id':version.id, 'timeline_sequence':version.sequence}})
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, '_verify_creation_identity', lambda actor:
        'ready' if actor.member_id == member.member_id and main.find_member(members, actor.member_id) else 'unauthorized')
    monkeypatch.setattr(main, '_creation_runtime', lambda: pytest.fail('rejected source must not start runtime'))
    client = TestClient(main.app, headers={'X-Local-Member-Token':token})
    return SimpleNamespace(production=repo, workspace_id='local', task=task, members=members,
        member=member, conversations=conversations, conversation=chat, version=version,
        finish=lambda: conversations.finish_turn(turn['id'], 'completed', result={}),
        raw_post=client.post, base_url='/api/creation-tasks/'+task.id,
        client=client, token=token, turn_id=turn['id'], script_id=script_id)


@pytest.mark.parametrize('rejection', ['foreign_turn','old_version','new_instruction','stopped'])
def test_assistant_execution_query_cannot_authorize_foreign_or_stale_instruction(execution_http_state, rejection):
    state = execution_http_state
    state.finish()
    chat=state.conversations.get(state.conversation['id'],state.workspace_id,state.member.member_id)
    text='开始制作'
    chat=state.conversations.save_draft(chat['id'],state.workspace_id,state.member.member_id,text,chat['draft_version'])
    turn,_=state.conversations.create_turn_idempotent(chat['id'],state.workspace_id,state.member.member_id,text,str(uuid4()),
        chat['draft_version'],request_context={'kind':'task','task_id':state.task.id,'stage':'review',
            'target_snapshot':{'timeline_version_id':'older' if rejection=='old_version' else state.version.id,
                'timeline_sequence':state.version.sequence}})
    if rejection=='new_instruction':
        state.conversations.finish_turn(turn['id'],'failed',result={'kind':'failure','message':'model interrupted'})
        chat=state.conversations.get(chat['id'],state.workspace_id,state.member.member_id)
        chat=state.conversations.save_draft(chat['id'],state.workspace_id,state.member.member_id,'只读，不生成',chat['draft_version'])
        state.conversations.create_turn_idempotent(chat['id'],state.workspace_id,state.member.member_id,chat['draft_text'],
            str(uuid4()),chat['draft_version'],request_context={'kind':'task','task_id':state.task.id})
    if rejection=='stopped':
        state.conversations.finish_turn(turn['id'],'stopped',result={'reason':'user_stopped'})
    current=state.production.task_records.get_creation_task(state.workspace_id,state.task.id)
    script=state.production.script_records.get_creation_task_script(state.workspace_id,current.confirmed_script_id)
    plan=state.production.plan_records.get_creation_plan(state.workspace_id,current.plan_id)
    preview={'script_id':script.id,'input_digest':revision_digest(script,plan)}
    before=[job.id for job in state.production.job_records.list_job_runs(state.workspace_id,state.task.id)]
    response=state.raw_post(state.base_url+'/execution/start?assistant_turn_id='+(str(uuid4()) if rejection=='foreign_turn' else turn['id']),
        json={'script_id':preview['script_id'],'input_digest':preview['input_digest'],'requirements_confirmed':True})
    assert response.status_code==409,response.text
    assert [job.id for job in state.production.job_records.list_job_runs(state.workspace_id,state.task.id)]==before


@pytest.mark.parametrize('operation',['ChangePersonSegment','RegeneratePersonRender','RegenerateNarrationSegment'])
def test_common_edit_registration_persists_real_start_source_without_running_media(tmp_path,monkeypatch,operation):
    from functools import partial
    from uuid import uuid4
    from app.conversation_repository import ConversationRepository
    from app.creation.execution_control import CreationExecutionControl
    from app.creation.editing.exact_preview_use_case import validate_edit_origin
    from app.creation_execution import CreationExecutionActor
    from app.device_runtime import build_device_runtime
    from app.device_scheduler import SubmissionIntent
    from app.creation.editing.metadata_use_case import EditPreviewRejected
    from app.host_resource_probes import PresenterBudget,NarrationBudget,CpuAudioBudget,MontageBudget,HostProcessProbe
    from app.member_permits import MemberDeviceAccess,creation_job_registration
    from app.schemas import CreationTaskExactEditRequest
    from test_montage_pipeline import _person_render_edit_state, _author_person_voice_revision
    from _media_preview_test_fixtures import ControlledResources
    root=tmp_path/'source-registration'
    root.mkdir()
    s=_person_render_edit_state(root,monkeypatch,'local')
    if operation == 'ChangePersonSegment':
        _author_person_voice_revision(s)
    repo=s.production
    actor=CreationExecutionActor('local',s.member.member_id)
    conversations=ConversationRepository(repo.database_path)
    monkeypatch.setattr(main,'conversation_repo',conversations)
    verifier=partial(validate_edit_origin,conversations)
    access=MemberDeviceAccess(s.members)
    presenter=PresenterBudget(1,1024**2,30,4096,1,5,64*1024**2,32,.1,64*1024**2,4096)
    narration=NarrationBudget(374,3,1000000,30,3*1024**2,1,300,1024,16,.1,2048,1024,input_scope='single_call_experiment_v1')
    audio=CpuAudioBudget(2,32*1024**2,30,30,48000,2,3*1024**2,1,30,1024,16,.1,2048,1024)
    def forbidden(*args,**kwargs): pytest.fail('注册不能运行媒体或模型')
    registration=creation_job_registration(repo,render_action=forbidden,render_is_valid=forbidden,
        presenter_budget=presenter,presenter_models={'fast':s.provider.model_id},
        presenter_cpu_render_budget=None,resolve_presenter_output=main._person_render_media_path,
        narration_budget=narration,narration_audio_budget=audio,
        montage_budget=MontageBudget(1,4,100000,100000,5,1),
        verify_identity=access.verify_identity,source_verifier=verifier)
    runtime=build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'registration.lock',
        device_access=access,registrations={'creation_job':registration},resources=ControlledResources(),processes=HostProcessProbe())
    control=CreationExecutionControl(repo,None,None,access.verify_identity)
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(main,'_creation_execution_control',lambda:control)
    case=main._exact_preview_use_case()
    task=repo.task_records.get_creation_task('local',s.task.id)
    main._task_authorizations_for_goal(repo).ensure(task, actor, interactive=True)
    base=case.metadata.ensure_montage_base('local',task,s.montage)
    task=repo.task_records.get_creation_task('local',s.task.id)
    action=({'narration_segment_id':s.montage.cues[0].narration_segment_id}
        if operation=='RegenerateNarrationSegment' else {'clip_id':next(clip.id for clip in s.montage.clips
            if (clip.person_render_id is None) == (operation == 'ChangePersonSegment'))})
    assistant={}
    if operation!='ChangePersonSegment':
        chat=conversations.create('local',actor.member_id,'task',task.id)
        conversations.save_draft(chat['id'],'local',actor.member_id,'继续完成当前修改',0)
        turn,_=conversations.create_turn_idempotent(chat['id'],'local',actor.member_id,'继续完成当前修改',str(uuid4()),1,
            {'kind':'task','task_id':task.id,'target_snapshot':{'timeline_version_id':base.id,'timeline_sequence':base.sequence}})
        assistant={'assistant_turn_id':turn['id']}
    payload=CreationTaskExactEditRequest(operation_type=operation,**action,**assistant,request_id=uuid4(),
        expected_timeline_version_id=base.id,expected_timeline_sequence=base.sequence)
    calls=list(s.provider.output_cache_keys)
    try:
        result=case.apply(task,actor,payload,identity={'member_id':actor.member_id,'local_diagnostic':False})
        assert result.status_code==202,result
        job=repo.job_records.get_job_run('local',result.value['job_id'])
        event=repo.execution_records.get_task_event('local',f'event_{job.id}_start').payload
        assert event['source']['workspace_id']=='local' and event['source']['task_id']==task.id
        assert event['source']['actor_identity']==actor.identity_payload()
        assert event['source']['request_id']==str(payload.request_id)
        if not assistant:
            assert event['source']['source_kind']=='interactive' and 'assistant_turn_id' not in event['source']
        else:
            assert event['source']['assistant_turn_id']==turn['id']
        assert event['source']['expected_timeline_version_id']==base.id
        request=runtime.scheduler._get(result.value['request_id'])
        assert request.state=='waiting'
        if assistant:
            terminal='completed' if operation=='RegeneratePersonRender' else 'failed'
            conversations.finish_turn(turn['id'],terminal,'原请求已提交',{})
            goal={'workspace_id':'local','task_id':task.id,'member_id':actor.member_id,
                'original_turn_id':turn['id'],'current_turn_id':turn['id'],'status':'waiting_result',
                'original_instruction':'继续完成当前修改'}
            with conversations._connect() as conn:
                conversations._write_goal(conn,{'id':turn['id']},goal)
            intent=SubmissionIntent('local','creation_job',job.id,job.input_digest,request.unit_key,request.policy)
            registration.owner.verify_submission(intent,actor)
            with pytest.raises(EditPreviewRejected):
                case.apply(task,actor,payload.model_copy(update={'request_id':uuid4()}),
                    identity={'member_id':actor.member_id,'local_diagnostic':False})
            with conversations._connect() as conn:
                conversations._write_goal(conn,{'id':turn['id']},{**goal,'status':'stopped'})
            with pytest.raises(ValueError):
                registration.owner.verify_submission(intent,actor)
            assert repo.execution_records.get_task_event('local',f'event_{job.id}_start').payload==event

        assert s.provider.output_cache_keys==calls
        assert repo.task_records.get_creation_task('local',task.id).current_timeline_version_id==base.id
    finally:
        assert runtime.stop(1)


@pytest.mark.parametrize('case_kind',['adopt','revision','stale','revoked','cancelled','queue_gap'])
def test_segment_visual_http_reads_and_adopts_the_original_job(tmp_path,monkeypatch,case_kind,execution_http_state):
    from fastapi.testclient import TestClient
    from app.creation.execution_control import CreationExecutionControl
    from app.assistant.task_business import TaskBusiness
    from test_creation_execution import _segment_units_case,_require_section_visual_decision,submit_creation_stage,_submit_section_revision
    root=tmp_path/'visual-http'
    root.mkdir()
    case=_segment_units_case(root,monkeypatch,declared_source=True)
    asset=case.repo.get_asset('local','section_video')
    asset.segments[0].end_seconds=asset.duration_seconds
    case.repo._save_asset(asset)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    if case_kind=='revision':
        for _ in range(2):
            case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
        _,revision,_=_submit_section_revision(case)
        job=case.repo.job_records.get_job_run('local',revision['job_id'])
    else:
        job=case.job
    monkeypatch.setattr(case.segments.renderer,'render',lambda *a,**kw:pytest.fail('visual HTTP must not render'))
    _require_section_visual_decision(case,monkeypatch)
    grant=case.scheduler.pick_and_grant()
    assert grant.owner_id==job.id
    case.executor.execute(grant,case.scheduler)
    control=CreationExecutionControl(case.repo,case.execution,SimpleNamespace(valid=lambda *a,**kw:False),
        case.owner.verify_identity,segments=case.segments)
    runtime=SimpleNamespace(scheduler=case.scheduler,wake=lambda:None,
        registrations={'creation_job':SimpleNamespace(owner=case.owner,executor=case.executor)})
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    monkeypatch.setattr(main,'_readable_creation_task',lambda request,task_id:case.repo.task_records.get_creation_task('local',task_id))
    monkeypatch.setattr(main,'_request_permit_actor',lambda request,workspace:case.actor)
    monkeypatch.setattr(main,'_verify_creation_identity',case.owner.verify_identity)
    def current_actor(request,workspace):
        from fastapi import HTTPException
        if case.owner.verify_identity(case.actor)!='ready':
            raise HTTPException(403,'原成员授权已撤销')
        return case.actor
    monkeypatch.setattr(main,'_require_permit_actor',current_actor)
    monkeypatch.setattr(main,'_creation_execution_control',lambda:control)
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(main,'_require_automation_task_open',lambda *args:None)
    monkeypatch.setattr(main,'device_runtime',runtime)
    # Exercise the registered router, not retired main handler exports.
    client=TestClient(main.app)
    root_url=f'/api/creation-tasks/{case.task_id}/execution'
    try:
        status=client.get(root_url)
        assert status.status_code==200,status.text
        locators=status.json()['visual_decisions']
        assert locators['total_count']==1 and not locators['truncated'],locators
        locator=locators['items'][0]
        detail=client.get(locator['detail_url'])
        assert detail.status_code==200,detail.text
        decision=detail.json()
        assert decision['audio']['persisted'] and decision['usage']['settled']
        assert decision['candidate_clips'][0]['preview_url'].startswith('/api/production/materials/')
        business=object.__new__(TaskBusiness)
        business.execution=control
        business.visual_job_belongs_to_goal=lambda goal,actor,job_id:job_id==job.id
        read=business.execute('read_segment_visual_decision',{'job_id':job.id,'section_id':'section_1'},
            case.actor,case.repo.task_records.get_creation_task('local',case.task_id),{}, {},{},None)
        assert read==decision
        with pytest.raises(PermissionError):
            control.read_visual_decision('local',case.task_id,job.id,'section_1',replace(case.actor,member_id='other-member'))
        payload={'decision_digest':decision['decision_digest'],'confirmed_clip_ids':decision['required_confirmation_ids']}
        assert client.post(locator['detail_url'],json={**payload,'automatically_confirm':True}).status_code==422
        audio_path=case.repo.narration_artifacts.resolve_authorized_narration_audio_reference('local',decision['audio']['artifact_reference'])
        before_audio=audio_path.read_bytes()
        before_segments=case.repo.narration_records.list_narration_segments('local',case.task_id,case.script_id)
        before_usage=case.repo.narration_units.creation_narration_usage('local',job.id)
        monkeypatch.setattr(case.stage,'prepare_segment',lambda *a,**kw:pytest.fail('不能重新生成已保留配音'))
        if case_kind=='stale':
            payload['decision_digest']='0'*64
        elif case_kind=='revoked':
            monkeypatch.setattr(case.owner,'verify_identity',lambda actor:'unauthorized')
        elif case_kind=='cancelled':
            case.repo.job_records.finish_job_run('local',job.id,status='cancelled',failure_reason='user_cancelled',impact='保留音频',repair_action='核对原任务')
        elif case_kind=='queue_gap':
            original_submit=case.scheduler.submit
            monkeypatch.setattr(case.scheduler,'submit',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('after adoption before queue')))
        response=client.post(locator['detail_url'],json=payload)
        if case_kind in {'adopt','revision'}:
            assert response.status_code==200,response.text
            assert response.json()['decision']['adopted']
            assert response.json()['decision']['usage']==decision['usage']
            assert response.json()['execution']['job']['id']==job.id
            replay=client.post(locator['detail_url'],json=payload)
            assert replay.status_code==200 and replay.json()['queue']['request_id']==response.json()['queue']['request_id'],replay.text
            assert replay.json()['queue']['replayed']
            assert not client.get(root_url).json()['visual_decisions']['items']
        elif case_kind=='queue_gap':
            assert response.status_code==409 and response.json()['detail']['submission_effect']=='unknown',response.text
            assert client.get(locator['detail_url']).json()['adopted']
            assert client.get(root_url).json()['visual_decisions']['items'][0]['adopted']
            monkeypatch.setattr(case.scheduler,'submit',original_submit)
            resumed=client.post(locator['detail_url'],json=payload)
            assert resumed.status_code==200 and resumed.json()['queue']['replayed'],resumed.text
        else:
            assert response.status_code in {403,409},response.text
            assert case.segments.video_preparation_id(case.job,'section_1') not in [e.id for e in case.repo.execution_records.list_task_events('local',case.task_id)]
        assert audio_path.read_bytes()==before_audio
        assert case.repo.narration_records.list_narration_segments('local',case.task_id,case.script_id)==before_segments
        assert case.repo.narration_units.creation_narration_usage('local',job.id)==before_usage
    finally:
        case.scheduler.close()


def test_segment_visual_business_locator_uses_persisted_review_goal_not_a_tool_boolean(tmp_path,monkeypatch):
    from app.creation.contracts import JobRun,TaskEvent
    from app.creation.execution_control import CreationExecutionControl
    from app.assistant.task_business import TaskBusiness,TaskBusinessRejected
    from app.conversation_repository import ConversationRepository
    from test_creation_execution import _visual_decision_case
    root=tmp_path/'visual-origin'
    root.mkdir()
    case=_visual_decision_case(root,monkeypatch)
    conversations=ConversationRepository(case.repo.database_path)
    chat=conversations.create('local',case.actor.member_id,'task',case.task_id)
    conversations.save_draft(chat['id'],'local',case.actor.member_id,'继续修改当前段',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],'local',case.actor.member_id,'继续修改当前段',str(uuid4()),1,
        {'kind':'task','task_id':case.task_id})
    goal={'original_turn_id':turn['id'],'current_turn_id':turn['id'],'workspace_id':'local',
        'task_id':case.task_id,'member_id':case.actor.member_id,'status':'waiting_result'}
    with conversations._connect() as conn:
        conversations._write_goal(conn,{'id':turn['id']},goal)
    source={'assistant_turn_id':turn['id'],'input_id':turn['id'],'conversation_id':chat['id']}
    case.repo.execution_records.append_task_event(TaskEvent('visual-review','local',case.task_id,None,'narration_semantic_review',{'source':source}))
    job=case.repo.job_records.create_job_run(JobRun('visual-revision','local',case.task_id,'creation_task_segment_revision','visual-revision','original-digest'))
    task=case.repo.task_records.get_creation_task('local',case.task_id)
    case.repo.execution_records.append_task_event(TaskEvent(f'event_{job.id}_start','local',case.task_id,job.id,'segment_revision_requested',
        {**case.actor.identity_payload(),'input_digest':job.input_digest,'script_id':case.script_id,'plan_id':task.plan_id,
            'command':{'input_id':turn['id'],'semantic_review_ref':'visual-review'}}))
    business=object.__new__(TaskBusiness)
    business.production,business.conversations=case.repo,conversations
    business.execution=CreationExecutionControl(case.repo,case.execution,SimpleNamespace(valid=lambda *a,**kw:False),
        case.owner.verify_identity,segments=case.segments)
    try:
        assert business.visual_job_belongs_to_goal(goal,case.actor,job.id)
        assert business.read_execution(goal,case.actor)['job']['id']==job.id
        foreign={**goal,'original_turn_id':'another-goal'}
        assert not business.visual_job_belongs_to_goal(foreign,case.actor,job.id)
        with pytest.raises(TaskBusinessRejected):
            business.read_execution(foreign,case.actor,job_id=job.id)
    finally:
        case.scheduler.close()


def test_segment_visual_empty_locator_does_not_require_a_generated_script(tmp_path):
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    from app.creation_execution import CreationExecutionActor
    from test_metadata_edit_use_case import metadata_state
    root=tmp_path/'no-visual-job'
    root.mkdir()
    s=metadata_state(root)
    service=SegmentProductionUseCase(s.repo,narration_stage=None,renderer=None,artifact_store=s.store,output_root=root)
    assert s.task.confirmed_script_id is None
    assert service.visual_decision_locators('ws',s.task.id,CreationExecutionActor('ws',s.member.member_id))=={
        'items':[],'total_count':0,'truncated':False}


@pytest.fixture
def execution_binding_state(execution_http_state, monkeypatch):
    """Real isolated owners and declared Job facts; no stage/model/media work."""
    from app.creation.contracts import JobRun
    from app.creation.execution_control import CreationExecutionControl
    from app.creation_execution import CreationExecutionActor
    from app.creation.editing.exact_preview_use_case import ExactPreviewUseCase
    from starlette.requests import Request
    state = execution_http_state
    repo = state.production
    script = repo.script_records.get_creation_task_script(state.workspace_id, state.script_id)
    plan = repo.plan_records.get_creation_plan(state.workspace_id, state.task.plan_id)
    actor = CreationExecutionActor(state.workspace_id, state.member.member_id)
    digest = revision_digest(script, plan)
    job = repo.creation_execution_jobs.create_creation_execution_job(JobRun(
        id='declared-binding-job', workspace_id=state.workspace_id, task_id=state.task.id,
        kind='creation_task_execution', idempotency_key='declared-binding-job', input_digest=digest),
        start_payload={'script_id':script.id, 'plan_id':plan.id, 'input_digest':digest,
                       **actor.identity_payload()})
    def no_work(*args, **kwargs):
        pytest.fail('binding representative must not dispatch work')
    execution = CreationExecution(repo, run_montage_plan=no_work, run_render=no_work,
        render_is_valid=lambda *args:False)
    control = CreationExecutionControl(repo, execution, SimpleNamespace(valid=lambda *a,**kw:False),
        main._verify_creation_identity)
    exact = ExactPreviewUseCase(repo, None, None, None, None, None, None, state.conversations, None)
    monkeypatch.setattr(main, 'creation_execution', execution)
    monkeypatch.setattr(main, '_creation_execution_control', lambda:control)
    monkeypatch.setattr(main, '_exact_preview_use_case', lambda:exact)
    state.actor, state.job, state.control, state.exact = actor, job, control, exact
    state.request = Request({'type':'http', 'headers':[
        (b'x-local-member-token', state.token.encode())]})
    return state


@pytest.mark.parametrize('execution_http_state', [pytest.param(False, id='metadata-no-voice')], indirect=True)
def test_preparation_only_runtime_keeps_persisted_goal_authority(execution_binding_state, monkeypatch):
    """Device preparation may exist before media owners have been assembled."""
    from datetime import datetime, timezone
    state = execution_binding_state
    authority = main._task_authorizations_for_goal()
    authority.ensure(state.task, state.actor, interactive=True)
    monkeypatch.setattr(main, 'device_runtime', SimpleNamespace(task_authorizations=None))
    observed = main._task_authorizations_for_goal()
    assert observed.verdict(state.actor, state.task.id, 'task_goal', check_scope=False) == 'ready'
    saved = state.production.task_authorization_records.get_task_authorization(state.workspace_id, state.task.id)
    state.production.task_authorization_records.save_task_authorization(
        replace(saved, revision=saved.revision+1, revoked_at=datetime.now(timezone.utc)), expected_revision=saved.revision)
    assert observed.verdict(state.actor, state.task.id, 'task_goal', check_scope=False) == 'unauthorized'


@pytest.mark.parametrize('execution_http_state', [pytest.param(False, id='metadata-no-voice')], indirect=True)
@pytest.mark.parametrize('authorization_revoked', [False, True], ids=['valid', 'revoked'])
def test_execution_projection_rechecks_persisted_authority_and_keeps_stale_history(execution_binding_state, monkeypatch, authorization_revoked):
    from datetime import datetime, timezone
    from app.creation.contracts import JobRun, TimelineVersion
    from app.conversation_repository import ConversationRepository
    state = execution_binding_state
    repo, conversations = state.production, state.conversations
    authority = main._task_authorizations_for_goal()
    monkeypatch.setattr(main, '_goal_continuation_service', None)
    service = main._goal_service()
    real_observe = service.observe_business_result
    observed = []
    def observe_persisted(turn_id, event):
        persisted = ConversationRepository(conversations.database_path).get_turn(turn_id)
        assert any(item.get('event_id') == event['event_id'] for item in persisted['events'])
        marker = repo.execution_records.get_task_event(state.workspace_id, event['event_id'])
        assert marker.event_type == 'assistant_business_projected'
        assert event['actionable'] is False
        observed.append((turn_id, event))
        real_observe(turn_id, event)
        return False  # Notification acceptance does not decide the projection count.
    monkeypatch.setattr(service, 'observe_business_result', observe_persisted)
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: pytest.fail('execution projection must not wake'))
    def bind_declared_job(turn_id, job):
        source, replay = main._assistant_execution_source(state.task.id, state.request, turn_id,
            'start', input_digest=job.input_digest)
        assert replay is None
        actual = main._read_bound_execution_result(state.task.id, state.request, job.id)
        main._assistant_execution_binding().record_result(state.workspace_id, state.task.id,
            state.member.member_id, turn_id, actual, source=source)
        goal = conversations.get_task_goal(turn_id)
        assert goal is not None
        task = repo.task_records.get_creation_task(state.workspace_id, state.task.id)
        authority.bind_business_context(task, state.actor, goal, memory={})
        conversations.finish_turn(turn_id, 'completed', result={'kind': 'task_turn',
            'response': {'execution_status': actual}})
        return conversations.get_task_goal(turn_id)
    goal = bind_declared_job(state.turn_id, state.job)
    repo.job_records.claim_job_run(state.workspace_id, state.job.id)
    repo.job_records.finish_job_run(state.workspace_id, state.job.id, status='failed',
        failure_reason='declared metadata failure', impact='declared job stopped without media work',
        repair_action='read original job metadata')
    if authorization_revoked:
        authorization = repo.task_authorization_records.get_task_authorization(state.workspace_id, state.task.id)
        repo.task_authorization_records.save_task_authorization(replace(authorization, revision=authorization.revision + 1,
            revoked_at=datetime.now(timezone.utc)), expected_revision=authorization.revision)
        assert authority.verdict(state.actor, state.task.id, 'task_goal', check_scope=False) == 'unauthorized'
        before_turn = conversations.get_turn(state.turn_id)
        before_events = repo.execution_records.list_task_events(state.workspace_id, state.task.id)
        assert main._assistant_execution_projector()() == 0
        assert conversations.get_turn(state.turn_id) == before_turn
        assert conversations.get_task_goal(state.turn_id) == goal
        assert repo.execution_records.list_task_events(state.workspace_id, state.task.id) == before_events
        assert observed == []
        return
    assert authority.verdict(state.actor, state.task.id, 'task_goal', check_scope=False) == 'ready'
    assert main._assistant_execution_projector()() == 1
    assert len(observed) == 1 and observed[0][1]['status'] == 'failed'
    published = ConversationRepository(conversations.database_path).get_turn(state.turn_id)
    assert main._assistant_execution_projector()() == 0
    assert conversations.get_turn(state.turn_id) == published and len(observed) == 1

    chat = conversations.get(state.conversation['id'], state.workspace_id, state.member.member_id)
    chat = conversations.save_draft(chat['id'], state.workspace_id, state.member.member_id,
        '核对另一条原制作作业', chat['draft_version'])
    turn, _ = conversations.create_turn_idempotent(chat['id'], state.workspace_id, state.member.member_id,
        chat['draft_text'], str(uuid4()), chat['draft_version'], request_context={
            'kind': 'task', 'task_id': state.task.id, 'stage': 'review',
            'target_snapshot': {'timeline_version_id': state.version.id, 'timeline_sequence': state.version.sequence}})
    plan = repo.plan_records.get_creation_plan(state.workspace_id, state.task.plan_id)
    second = repo.creation_execution_jobs.create_creation_execution_job(JobRun('declared-stale-projection', state.workspace_id,
        state.task.id, 'creation_task_execution', 'declared-stale-projection', state.job.input_digest),
        start_payload={'script_id': state.script_id, 'plan_id': plan.id, 'input_digest': state.job.input_digest,
            **state.actor.identity_payload()})
    bind_declared_job(turn['id'], second)
    repo.timeline_records.create_timeline_version(TimelineVersion('projection-later-head', state.workspace_id,
        state.task.id, state.version.id, 2, {}, None))
    repo.job_records.claim_job_run(state.workspace_id, second.id)
    repo.job_records.finish_job_run(state.workspace_id, second.id, status='failed',
        failure_reason='declared stale metadata failure', impact='declared historical job stopped without media work',
        repair_action='read original job metadata')
    assert main._assistant_execution_projector()() == 1
    assert len(observed) == 2 and observed[-1][0] == turn['id']
    assert observed[-1][1]['status'] == 'stale' and observed[-1][1]['result_version_id'] is None
    stale_turn = ConversationRepository(conversations.database_path).get_turn(turn['id'])
    assert stale_turn['status'] == 'completed'
    assert main._assistant_execution_projector()() == 0
    assert conversations.get_turn(turn['id']) == stale_turn and len(observed) == 2
    assert {job.id for job in repo.job_records.list_job_runs(state.workspace_id, state.task.id)} == {state.job.id, second.id}


def test_execution_binding_preserves_source_actor_and_receipt_error_order(execution_binding_state, monkeypatch):
    from fastapi import HTTPException
    from app.creation.contracts import TimelineVersion
    state = execution_binding_state
    task_id, request = state.task.id, state.request
    calls = []
    def actor_denied(*args):
        calls.append('actor')
        raise HTTPException(403, 'declared actor parse rejection')
    monkeypatch.setattr(main, '_request_permit_actor', actor_denied)
    assert main._assistant_execution_source('missing', object(), None, 'start') == (None, None)
    with pytest.raises(HTTPException) as bad_receipt:
        main._bind_assistant_execution_result('missing', object(), state.turn_id, {})
    assert bad_receipt.value.status_code == 502 and calls == []
    state.production.timeline_records.create_timeline_version(TimelineVersion('unrelated-head', state.workspace_id,
        task_id, state.version.id, 2, {}, None))
    with pytest.raises(HTTPException) as stale:
        main._assistant_execution_source(task_id, request, state.turn_id, 'start')
    assert stale.value.status_code == 409 and calls == []
    state.finish()
    def new_turn(stage):
        chat = state.conversations.get(state.conversation['id'], state.workspace_id, state.member.member_id)
        chat = state.conversations.save_draft(chat['id'], state.workspace_id, state.member.member_id,
            '开始制作', chat['draft_version'])
        turn, _ = state.conversations.create_turn_idempotent(chat['id'], state.workspace_id,
            state.member.member_id, chat['draft_text'], str(uuid4()), chat['draft_version'],
            request_context={'kind':'task', 'task_id':task_id, 'stage':stage,
                'target_snapshot':{'timeline_version_id':'unrelated-head', 'timeline_sequence':2}})
        return turn['id']
    current_turn = new_turn('review')
    with pytest.raises(HTTPException) as denied:
        main._assistant_execution_source(task_id, request, current_turn, 'start')
    assert denied.value.status_code == 403 and calls == ['actor']
    state.conversations.finish_turn(current_turn, 'completed', result={})
    missing_turn = new_turn('script')
    with monkeypatch.context() as missing:
        missing.setattr(state.production.plan_records, 'get_creation_plan',
            lambda *a,**kw:(_ for _ in ()).throw(KeyError('missing plan')))
        with pytest.raises(HTTPException) as no_plan:
            main._assistant_execution_source(task_id, request, missing_turn, 'start')
    assert no_plan.value.status_code == 404 and calls == ['actor']


@pytest.mark.parametrize('execution_http_state', [pytest.param(False, id='metadata-no-voice')], indirect=True)
def test_execution_binding_repairs_conversation_after_original_locator_commit(execution_binding_state, monkeypatch):
    state = execution_binding_state
    source, replay = main._assistant_execution_source(state.task.id, state.request,
        state.turn_id, 'start', state.job.input_digest)
    assert replay is None
    wakes = []
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda:wakes.append('wake'))
    original_record = state.conversations.record_task_execution
    with monkeypatch.context() as interrupted:
        interrupted.setattr(state.conversations, 'record_task_execution',
            lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('after durable locator before conversation')))
        with pytest.raises(RuntimeError, match='after durable locator'):
            main._bind_assistant_execution_result(state.task.id, state.request, state.turn_id,
                {'job':{'id':state.job.id}}, source=source)
    located = state.production.execution_records.get_task_event(state.workspace_id, execution_locator_id(state.turn_id))
    assert located.job_id == state.job.id and wakes == []
    assert state.conversations.get_turn(state.turn_id)['result'] is None
    assert state.conversations.record_task_execution == original_record
    result = main._bind_assistant_execution_result(state.task.id, state.request,
        state.turn_id, {'job':{'id':state.job.id}})
    assert result['job']['id'] == state.job.id and wakes == ['wake']
    assert state.production.execution_records.get_task_event(state.workspace_id, located.id) == located
    assert state.conversations.get_turn(state.turn_id)['result']['goal_business_actions'] == [
        {'kind':'execution_status', 'id':state.job.id}]
    assert main._assistant_execution_source(state.task.id, state.request, state.turn_id,
        'start', state.job.input_digest)[1]['job']['id'] == state.job.id
    assert [job.id for job in state.production.job_records.list_job_runs(state.workspace_id, state.task.id)] == [state.job.id]


def test_execution_binding_late_input_and_version_gate_precede_actor_and_write(execution_binding_state, monkeypatch):
    from app.creation.contracts import TimelineVersion
    state = execution_binding_state
    def consume(message):
        chat = state.conversations.get(state.conversation['id'], state.workspace_id, state.member.member_id)
        chat = state.conversations.save_draft(chat['id'], state.workspace_id, state.member.member_id,
            message, chat['draft_version'])
        item, _ = state.conversations.enqueue_input(chat['id'], state.workspace_id, state.member.member_id,
            target_turn_id=state.turn_id, client_request_id=str(uuid4()), message=message, mode='follow_up',
            expected_draft_version=chat['draft_version'], expected_reference_version=chat['reference_version'],
            request_context={'kind':'task', 'task_id':state.task.id, 'stage':'review', 'selected_reference':None,
                'target_snapshot':{'timeline_version_id':state.version.id, 'timeline_sequence':state.version.sequence}})
        state.conversations.transition_input(chat['id'], state.workspace_id, state.member.member_id,
            item['input_id'], 'consumed')
        return item['input_id']
    old_input = consume('只观察原作业')
    state.conversations.transition_input(state.conversation['id'], state.workspace_id,
        state.member.member_id, old_input, 'completed')
    new_input = consume('核对当前版本')
    result = {'job':{'id':state.job.id, 'status':'queued'}}
    actor_calls = []
    def current_actor(*args):
        actor_calls.append('actor')
        return state.actor
    monkeypatch.setattr(main, '_request_permit_actor', current_actor)
    monkeypatch.setattr(main, '_exact_edit_access', lambda *a,**kw:pytest.fail('must not enter write access'))
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda:pytest.fail('must not wake'))
    assert main._bind_assistant_execution_observation(state.task.id, state.request, state.turn_id,
        {'input_id':old_input, 'input_turn_id':state.turn_id}, result) is result
    assert actor_calls == []
    state.production.timeline_records.create_timeline_version(TimelineVersion('changed-head', state.workspace_id,
        state.task.id, state.version.id, 2, {}, None))
    assert main._bind_assistant_execution_observation(state.task.id, state.request, state.turn_id,
        {'input_id':new_input, 'input_turn_id':state.turn_id}, result) is result
    assert actor_calls == ['actor']
    assert not [e for e in state.production.execution_records.list_task_events(state.workspace_id, state.task.id)
        if e.event_type == 'assistant_execution_located']
    assert state.conversations.task_turn_source(state.turn_id, state.workspace_id,
        state.member.member_id, state.task.id)['input_id'] == new_input


def test_execution_binding_plain_receipt_preserves_committed_successor_and_bounds(execution_binding_state, tmp_path):
    from fastapi import HTTPException
    from app.creation.contracts import EditIntent, EditProposal, EditOperation, TimelineVersion
    state = execution_binding_state
    repo, task = state.production, state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    request_id = str(uuid4())
    origin = state.conversations.task_turn_source(state.turn_id, state.workspace_id, state.member.member_id, task.id)
    snapshot = origin['context']['target_snapshot']
    edit_source = {'workspace_id':state.workspace_id, 'task_id':task.id, 'member_id':state.member.member_id,
        'conversation_id':origin['conversation_id'], 'assistant_turn_id':state.turn_id,
        'input_id':None, 'input_turn_id':None, 'expected_timeline_version_id':state.version.id,
        'expected_timeline_sequence':state.version.sequence, 'request_id':request_id}
    repo.edit_records.create_edit_intent(EditIntent('binding-edit-intent', state.workspace_id, task.id, 'declared crop edit',
        'review', [], None, 'ChangeCrop', {}, {'request_source':edit_source}, 1, 'ready'))
    repo.edit_records.create_edit_proposal(EditProposal('binding-edit-proposal', state.workspace_id, task.id,
        'binding-edit-intent', 'declared committed edit', ['clip://declared-clip'], {'operation_type':'ChangeCrop'}, [], {}, []))
    repo.edit_records.transition_edit_proposal(state.workspace_id, task.id, 'binding-edit-proposal', status='confirmed')
    version = repo.timeline_records.create_timeline_version(TimelineVersion('committed-edit-head', state.workspace_id, task.id,
        state.version.id, 2, {}, 'binding-edit-operation'))
    from app.models import utc_now
    applied_at = utc_now()
    operation = repo.edit_records.create_edit_operation(EditOperation('binding-edit-operation', state.workspace_id, task.id,
        'binding-edit-proposal', 'ChangeCrop', {'clip_id':'declared-clip'}, state.version.id, version.id, 'applied', created_at=applied_at, applied_at=applied_at))
    task = repo.task_records.get_creation_task(state.workspace_id, task.id)
    inputs = {'operation_type':'ChangeCrop', 'assistant_turn_id':state.turn_id,
        'expected_timeline_version_id':state.version.id, 'expected_timeline_sequence':state.version.sequence,
        'clip_id':'declared-clip', 'ignored_private_field':'x'*100000}
    event_id, _ = repo.execution_records.claim_edit_request(task, state.member.member_id, request_id, 'apply', inputs)
    # Prepare the actual production TaskBusiness port before any read-only snapshot.
    business = main._task_business()

    def public_receipt(identity=request_id):
        return business.request_result(task, state.actor, identity, 'apply')

    def raw_receipt(identity=request_id):
        return main._goal_request_metadata(task, state.actor, identity, 'apply')

    def durable_snapshot():
        import sqlite3
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                        for name in tables}
        return databases, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                           for path in tmp_path.rglob('*')}

    before = durable_snapshot()
    unknown = next(route.endpoint for route in main.app.routes if route.name == 'get_exact_edit_request_result')(task.id, request_id, state.request, 'apply')
    assert unknown['status'] == 'unknown' and unknown['result'] is None and unknown['result_status_code'] is None
    assert 'ignored_private_field' not in unknown['submitted_input']
    assert public_receipt() == unknown
    assert raw_receipt()['submitted_input'] == inputs
    assert main._assistant_edited_execution_version(task, state.member.member_id, state.turn_id,
        origin, snapshot, version, state.request) is None
    assert durable_snapshot() == before
    repo.execution_records.save_edit_request_result(task, event_id, status_code=200,
        response={'operation':{'id':operation.id, 'result_version_id':version.id}})
    before = durable_snapshot()
    recorded = next(route.endpoint for route in main.app.routes if route.name == 'get_exact_edit_request_result')(task.id, request_id, state.request, 'apply')
    assert recorded['status'] == 'recorded' and recorded['result']['operation']['id'] == operation.id
    assert public_receipt() == recorded
    assert raw_receipt()['status'] == 'recorded' and raw_receipt()['submitted_input'] == inputs
    successor = main._assistant_edited_execution_version(task, state.member.member_id, state.turn_id,
        origin, snapshot, version, state.request)
    assert successor == {**snapshot, 'timeline_version_id':version.id, 'timeline_sequence':version.sequence}
    assert main._assistant_execution_source(task.id, state.request, state.turn_id, 'start')[0][
        'expected_timeline_version_id'] == version.id
    assert durable_snapshot() == before
    large_id = str(uuid4())
    repo.execution_records.claim_edit_request(task, state.member.member_id, large_id, 'apply',
        {**inputs, 'clip_ids':['中'*22000]})
    before = durable_snapshot()
    with pytest.raises(HTTPException) as too_large:
        next(route.endpoint for route in main.app.routes if route.name == 'get_exact_edit_request_result')(task.id, large_id, state.request, 'apply')
    assert too_large.value.status_code == 409 and '有界查询范围' in str(too_large.value.detail)
    from app.creation.editing.metadata_use_case import EditPreviewRejected
    with pytest.raises(EditPreviewRejected) as public_too_large:
        public_receipt(large_id)
    assert public_too_large.value.status_code == 409
    assert public_too_large.value.detail == too_large.value.detail
    assert raw_receipt(large_id)['status'] == 'unknown'
    assert raw_receipt(large_id)['submitted_input']['clip_ids'] == ['中'*22000]
    assert durable_snapshot() == before
    assert len(repo.edit_records.list_edit_operations(state.workspace_id, task.id)) == 1
    # Even a previously checked task/source cannot bypass the former self-HTTP
    # reader's current write gate. Shared visibility exercises owner rejection.
    from dataclasses import asdict
    other, _ = state.members.join(state.members.create_invite_code(), '后续编辑任务成员')
    saved_task = asdict(task)
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=?,visibility=? WHERE id=?',
            (json.dumps({**saved_task, 'owner_user_id':other.member_id}, default=str), 'shared', task.id))
    assert repo.task_records.get_creation_task(state.workspace_id, task.id).visibility == 'shared'
    before = durable_snapshot()
    try:
        with pytest.raises(HTTPException) as changed_owner:
            main._assistant_edited_execution_version(task, state.member.member_id, state.turn_id,
                origin, snapshot, version, state.request)
        assert changed_owner.value.status_code == 403
        # Public Agent projection adds no new HTTP owner gate; internal reconciliation stays raw.
        assert public_receipt() == recorded
        assert raw_receipt()['result'] == recorded['result']
        assert durable_snapshot() == before
    finally:
        with repo._connect() as conn:
            conn.execute('UPDATE creation_tasks SET payload=?,visibility=? WHERE id=?',
                (json.dumps(saved_task, default=str), saved_task['visibility'], task.id))


@pytest.fixture
def edit_result_state(tmp_path, monkeypatch):
    """Metadata-only real edit/Conversation owners, without model or SDK setup."""
    from fastapi.testclient import TestClient
    from starlette.requests import Request
    from app.conversation_repository import ConversationRepository
    from app.creation_execution import CreationExecutionActor
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from test_metadata_edit_use_case import metadata_state
    state = metadata_state(tmp_path)
    conversations = ConversationRepository(state.repo.database_path)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, '_goal_continuation_service', None)
    monkeypatch.setattr(main, 'assistant_continuation_driver', None)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request:'ws')
    monkeypatch.setattr(main, '_verify_creation_identity', lambda actor:
        'ready' if main.find_member(state.members, actor.member_id) else 'unauthorized')
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(tmp_path/'outputs')))
    chat = conversations.create('ws', state.member.member_id, 'task', state.task.id)
    chat = conversations.save_draft(chat['id'], 'ws', state.member.member_id, '核对本次编辑请求', chat['draft_version'])
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'ws', state.member.member_id,
        chat['draft_text'], str(uuid4()), chat['draft_version'], request_context={
            'kind':'task', 'task_id':state.task.id, 'stage':'review', 'selected_object_refs':[],
            'target_snapshot':{'timeline_version_id':state.base.id, 'timeline_sequence':state.base.sequence}})
    actor = CreationExecutionActor('ws', state.member.member_id)
    authority = TaskAuthorizationUseCase(state.repo, lambda identity:
        'ready' if identity['workspace_id'] == 'ws' and main.find_member(
            state.members, identity['member_id']) else 'unauthorized')
    business_for = main._task_business
    def current_business():
        business = business_for()
        business.authorizations = authority
        return business
    monkeypatch.setattr(main, '_task_business', current_business)
    def finish(**response):
        conversations.finish_turn(turn['id'], 'completed', '已回读原请求', result={
            'kind':'task_turn', 'edit_preview':False, 'response':response})
        goal = main._goal_service().register_turn(turn['id'])
        assert goal is not None
        authorization = authority.ensure(state.repo.task_records.get_creation_task('ws', state.task.id), actor, interactive=True)
        required = authority._requirements('ws', state.task.id)[1]
        state.repo.task_authorization_records.save_task_authorization(replace(authorization, revision=authorization.revision+1,
            resource_scope={**authorization.resource_scope, 'references':required['references']}),
            expected_revision=authorization.revision)
        authority.bind_business_context(state.repo.task_records.get_creation_task('ws', state.task.id), actor, goal, memory={})
    state.production, state.workspace_id, state.version = state.repo, 'ws', state.base
    state.conversations, state.conversation, state.turn = conversations, chat, turn
    state.request = Request({'type':'http', 'headers':[(b'x-local-member-token', state.token.encode())]})
    state.client = TestClient(main.app, headers={'X-Local-Member-Token':state.token})
    state.base_url = '/api/creation-tasks/'+state.task.id
    state.finish = finish
    return state


def test_edit_result_unknown_preserves_old_source_without_new_request(edit_result_state, monkeypatch):
    state = edit_result_state
    turn_id = state.turn['id']
    chat = state.conversations.get(state.conversation['id'], 'ws', state.member.member_id)
    chat = state.conversations.save_draft(chat['id'], 'ws', state.member.member_id, '核对原输入', chat['draft_version'])
    old_input = str(uuid4())
    state.conversations.enqueue_input(chat['id'], 'ws', state.member.member_id,
        target_turn_id=turn_id, client_request_id=old_input, message=chat['draft_text'], mode='follow_up',
        expected_draft_version=chat['draft_version'], expected_reference_version=chat['reference_version'],
        request_context={'kind':'task', 'task_id':state.task.id, 'selected_reference':None,
            'target_snapshot':{'timeline_version_id':state.base.id, 'timeline_sequence':state.base.sequence}})
    state.conversations.transition_input(chat['id'], 'ws', state.member.member_id, old_input, 'consumed')
    call_id = 'unknown-edit'
    request_id = uuid5(NAMESPACE_URL, json.dumps([state.task.id, turn_id, call_id], separators=(',', ':')))
    details = {'request_id':str(request_id), 'query_url':state.base_url+f'/assistant/edit-requests/{request_id}?phase=apply',
        'phase':'apply', 'assistant_turn_id':turn_id, 'tool_call_id':call_id, 'status':'result_unknown',
        'input_id':old_input, 'input_turn_id':turn_id}
    locator = main._resolve_assistant_edit_request(state.task.id, state.request, turn_id, details)
    assert locator.status == 'unknown' and locator.result is None
    stored = state.repo.execution_records.get_task_event('ws', f'event_edit_locator_{turn_id}_{request_id}')
    assert stored.payload['source']['input_id'] == old_input
    with state.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='edit_request_claimed'").fetchone()[0] == 0
    with monkeypatch.context() as replay:
        replay.setattr(state.conversations, 'task_result_source', lambda *a,**kw:pytest.fail('must restore original source'))
        replay.setattr(main, 'auth_context', SimpleNamespace(enabled=True))
        replay.setattr(main, '_request_permit_actor', lambda *a:pytest.fail('restored source must not resolve actor'))
        assert main._resolve_assistant_edit_request(state.task.id, state.request, turn_id, details) == locator
    assert state.repo.execution_records.get_task_event('ws', stored.id) == stored
    # Applied verification follows the durable locator, even on rejection.
    with pytest.raises(main.HTTPException) as not_applied:
        main._assistant_applied_edit_receipt(state.task.id, state.request, turn_id, details)
    assert not_applied.value.status_code == 502
    assert state.repo.execution_records.get_task_event('ws', stored.id) == stored
    assert state.repo.edit_records.list_edit_operations('ws', state.task.id) == []


def test_edit_result_does_not_swallow_reader_permission_or_conflict(edit_result_state, monkeypatch):
    state = edit_result_state
    request_id = uuid5(NAMESPACE_URL, json.dumps([state.task.id, state.turn['id'], 'reader'], separators=(',', ':')))
    details = {'request_id':str(request_id), 'query_url':state.base_url+f'/assistant/edit-requests/{request_id}?phase=preview',
        'assistant_turn_id':state.turn['id'], 'tool_call_id':'reader', 'status':'not_submitted'}
    case = main._exact_preview_use_case()
    monkeypatch.setattr(main, '_exact_preview_use_case', lambda:case)
    for code in (403, 409):
        def denied(*args, **kwargs):
            raise main.EditPreviewRejected(code, 'original reader rejection')
        with monkeypatch.context() as blocked:
            blocked.setattr(case, 'read_request_receipt', denied)
            blocked.setattr(state.conversations, 'task_result_source', lambda *a,**kw:pytest.fail('reader refusal must not use fallback'))
            with pytest.raises(main.HTTPException) as error:
                main._resolve_assistant_edit_request(state.task.id, state.request, state.turn['id'], details)
            assert error.value.status_code == code
    assert not [event for event in state.repo.execution_records.list_task_events('ws', state.task.id)
        if event.event_type in {'assistant_edit_request_located', 'edit_request_claimed'}]


@pytest.fixture
def media_result_state(receipt_state, monkeypatch, tmp_path):
    """Persisted producer/PNG metadata and adopted-version facts; never runs media."""
    from app.creation.contracts import ArtifactRecord, TransformRun, TaskEvent, TimelineVersion
    from app.creation_execution import CreationJobOwnerVerifier, CreationJobUnitExecutor
    from app.device_runtime import build_device_runtime, OwnerRegistration
    from app.member_permits import MemberDeviceAccess
    from app.models import utc_now
    from app.task_audio import TaskMediaJobs
    from test_device_runtime import Resources, Processes
    from test_host_resource_probes import _render_resources
    from test_task_media_api import _result_read_guards
    _result_read_guards(monkeypatch)
    state = receipt_state
    repo, actor, conversations = state.production, state.actor, state.conversations
    resources = _render_resources()
    media = TaskMediaJobs(repo, facts=lambda *args: ({'base_version_id':
        repo.task_records.get_creation_task('local', state.task.id).current_timeline_version_id}, resources),
        verify_identity=main._verify_creation_identity)
    runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path/'read-only-device.lock',
        device_access=MemberDeviceAccess(state.members), registrations={'creation_job': OwnerRegistration(
            CreationJobOwnerVerifier(repo, task_media=media, verify_identity=main._verify_creation_identity),
            CreationJobUnitExecutor(repo, render_action=None, render_is_valid=None, task_media=media))},
        resources=Resources(), processes=Processes())
    monkeypatch.setattr(main, '_creation_runtime', lambda:runtime)
    monkeypatch.setattr(main, 'device_runtime', runtime)
    origin = conversations.task_turn_source(state.turn_id, 'local', state.member.member_id, state.task.id)
    source = {'workspace_id':'local', 'task_id':state.task.id, 'member_id':state.member.member_id,
        'assistant_turn_id':state.turn_id, 'conversation_id':origin['conversation_id'],
        'actor_identity':actor.identity_payload(), 'expected_timeline_version_id':None, 'expected_timeline_sequence':0}
    contract = {'name':'card.png', 'width':64, 'height':64, 'max_bytes':4096}
    job = media.submit(runtime, actor, state.task.id, 'media_script_preview',
        {'_typed_source':source, 'payload':{'requested_outputs':[contract], 'input_refs':[]}}, 'graphic_source')
    running = repo.job_records.claim_job_run('local', job.id)
    # Opaque fixture bytes stand for an already verified publication. No PNG is generated/decoded.
    blob = state.store.publish_bytes('artifacts/declared-result.png', b'declared existing PNG publication')
    stat = state.store.resolve_file(blob.logical_ref).stat()
    revision = {'size_bytes':stat.st_size, 'mtime_ns':stat.st_mtime_ns, 'ctime_ns':stat.st_ctime_ns}
    record = repo.execution_records.create_artifact_record(ArtifactRecord('declared-result-png','local',state.task.id,
        'graphic_source',blob.logical_ref,blob.sha256,blob.size_bytes,job.id,[]))
    repo.lineage_records.create_transform_run(TransformRun('declared-result-transform','local',state.task.id,
        job.id,'media_script',job.input_digest,[],[record.logical_ref],'declared metadata',
        {'owner_member_id':actor.member_id, 'name':'card.png', 'width':16, 'height':16, 'file_revision':revision},
        'succeeded',running.started_at,utc_now()))
    result = {'source_ref':record.logical_ref, 'artifact_id':record.id, 'sha256':record.sha256,
        'size_bytes':record.size_bytes, 'width':16, 'height':16, 'name':'card.png', 'applied':False}
    repo.execution_records.append_task_event(TaskEvent('event_'+job.id+'_result','local',state.task.id,job.id,'task_media_result',{'result':result}))
    repo.execution_records.append_task_event(TaskEvent('event_'+job.id+'_verified','local',state.task.id,job.id,'task_media_verified',
        {'outputs':[{'reference':record.logical_ref, **revision}]}))
    repo.job_records.finish_job_run('local',job.id,status='succeeded',output_refs=[record.logical_ref])
    receipt = main._media_result_receipt(media,job,actor,runtime)
    conversations.finish_turn(state.turn_id,'completed','declared completed publication',result={
        'kind':'task_turn','response':{'media_job':receipt}})
    goal = conversations.register_task_goal(state.turn_id)
    assert goal is not None
    slot = main._goal_script_source_slot(goal,job.id,actor)
    assert conversations.record_goal_completed_sources(state.turn_id,[slot],expected_goal=goal)
    # Adoption is an input fact here, not a claim to exercise the editor or renderer.
    version = repo.timeline_records.create_timeline_version(TimelineVersion('declared-adopted-version','local',state.task.id,None,1,{'montage':{}},None))
    event = {'type':'business_result','event_id':'declared-actual-adoption','phase':'apply','status':'confirmed',
        'task_id':state.task.id,'job_id':job.id,'base_version_id':None,'result_version_id':version.id,'actionable':False}
    assert conversations.project_business_result(state.turn_id,'local',actor.member_id,event,
        response_patch={}, message='declared adopted-version event')
    candidate = next(item for item in conversations.goal_event_candidates() if item['event']['event_id']==event['event_id'])
    claim = conversations.claim_goal_event(candidate,{'timeline_version_id':version.id,'timeline_sequence':version.sequence},max_rounds=3)
    assert claim is not None
    state.task = repo.task_records.get_creation_task('local',state.task.id)
    state.original_job, state.media, state.runtime = job, media, runtime
    state.slot, state.contract, state.child_id, state.version = slot, contract, claim['turn_id'], version
    yield state
    runtime.scheduler.close()


@pytest.mark.parametrize('damaged',[False,True])
def test_media_result_completed_slot_keeps_adopted_chain_and_rechecks_file(media_result_state, damaged):
    state = media_result_state
    before = [job.id for job in state.production.job_records.list_job_runs('local',state.task.id)]
    ordinary = state.client.get(f'/api/creation-tasks/{state.task.id}/media-jobs/{state.original_job.id}')
    assert ordinary.status_code==200 and ordinary.json()['status']=='stale'
    assert 'script_source' not in state.slot
    if damaged:
        state.store.resolve_file(state.slot['source_ref']).write_bytes(b'changed actual publication')
        with pytest.raises(main.HTTPException) as changed:
            main._completed_media_result(state.task,state.request,state.child_id,state.slot,
                contract=state.contract,input_refs=[])
        assert changed.value.status_code==409
    else:
        reused = main._completed_media_result(state.task,state.request,state.child_id,state.slot,
            contract=state.contract,input_refs=[])
        assert reused['job_id']==state.original_job.id and reused['status']=='succeeded'
        assert reused['result']['reused_completed_source'] is True and reused['result']['applied'] is False
        selected = main._media_result_binding().select_finish(state.child_id,{**reused,'result':{**reused['result'],'source_ref':'forged'}})
        assert selected.job_id==state.original_job.id and selected.completed_slot['source_ref']==state.slot['source_ref']
        assert state.conversations.completed_goal_source(state.child_id,
            requested_outputs=[{**state.contract,'name':'new-slot.png'}],input_refs=[]) is None
        # A real task version change without updating the original goal is refused.
        from dataclasses import replace
        state.production.timeline_records.create_timeline_version(replace(state.version,id='unbound-later-version',parent_version_id=state.version.id,sequence=2))
        task = state.production.task_records.get_creation_task('local',state.task.id)
        with pytest.raises(main.HTTPException) as stale:
            main._completed_media_result(task,state.request,state.child_id,state.slot,contract=state.contract,input_refs=[])
        assert stale.value.status_code==409
    assert [job.id for job in state.production.job_records.list_job_runs('local',state.task.id)]==before
    assert state.conversations.get_turn(state.child_id)['status']=='running'
