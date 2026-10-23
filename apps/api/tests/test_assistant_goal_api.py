from assistant_sdk_fixtures import project_event_fixture
"""Normal HTTP, persistent business events and lifecycle driver; bounded model seam."""
import time
import json
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

import app.main as main
import app.assistant_pi as pi
from dataclasses import replace
from app.creation_execution import CreationExecution, revision_digest
from app.quality_inspection import QualityCheckRepository
from test_assistant_business_continuation import state as state  # noqa: F401
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state  # noqa: F401
import test_media_script_api

script_state = test_media_script_api.state


@pytest.fixture(autouse=True)
def isolated_local_workspace(state, monkeypatch):
    # Existing domain fixtures name their single local workspace workspace_a.
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", state.workspace_id)
    from test_montage_pipeline import synthetic_clip_preview_budget
    monkeypatch.setattr(main, 'clip_preview_budget_from_env', synthetic_clip_preview_budget)
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    configure_synthetic_media_budgets(monkeypatch,main)



def wait_until(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    pytest.fail("normal lifecycle driver did not advance the actual result")


def select_clip(state, chat):
    return state.conversations.save_reference(chat["id"],state.workspace_id,state.member.member_id,
        {"kind":"selection","items":[{"kind":"shot","task_id":state.task.id,"object_id":state.clip.id,
            "version":state.version.id,"label":"当前镜头"}]},chat["reference_version"])


def material_execution_input(state):
    """A confirmed material-only fixture; no presenter or media execution claim."""
    from dataclasses import asdict
    from app.montage import creation_task_montage_plan_to_payload
    plan=state.production.get_creation_plan(state.workspace_id,state.task.plan_id)
    plan.presenter_share_target=0
    with state.production._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?",(json.dumps(asdict(plan),default=str),plan.id))
    reference=state.production.workspace_media_reference_for_asset(state.workspace_id,state.asset.id)
    montage=replace(state.montage,presenter_share_target=0,clips=[replace(clip,person_render_id=None,
        asset_id=state.asset.id,segment_id=state.asset.segments[0].id,source_path=reference) if clip.person_render_id else clip
        for clip in state.montage.clips])
    payload=creation_task_montage_plan_to_payload(montage)
    state.production.save_creation_task_montage_plan(state.workspace_id,state.task.id,montage.id,payload)
    state.version=replace(state.version,id="material-execution-input",parent_version_id=state.version.id,
        sequence=state.version.sequence+1,snapshot={**state.version.snapshot,"montage":payload})
    state.production.create_timeline_version(state.version)


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
    task, saved = state.production.save_creation_task_montage_plan(state.workspace_id, state.task.id,
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
    proposal = state.production.get_edit_proposal(state.workspace_id, preview["proposal"]["id"])
    assert proposal.status == "proposed" and len(calls) == 1
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == state.version.id
    adopted = state.raw_post(state.base_url + f"/edits/{proposal.id}/confirm", json={"request_id": str(uuid4())})
    assert adopted.status_code == 200, adopted.text
    wait_until(lambda: len(calls) == 2)
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == "no_progress")
    assert main._goal_service().reconcile() == 0 and len(calls) == 2
    assert not any(job.kind == "creation_task_execution" for job in state.production.list_job_runs(state.workspace_id, state.task.id))


@pytest.mark.parametrize("damaged", [False, True])
def test_completed_script_slot_is_reused_after_real_adoption_without_new_job_and_rechecks_file(script_state, state, monkeypatch, damaged):
    state.finish()
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    models, receipts = [], []
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
        assert len(state.production.list_job_runs(state.workspace_id, state.task.id)) == 1
        if len(models) == 2:
            prepared = main._prepare_script_request(state.task.id, state.request, main.MediaScriptPreviewRequest.model_validate({
                **script_state.payload, "request_id": uuid4(), "assistant_turn_id": kwargs["turn_id"],
                "requested_outputs": [{**script_state.payload["requested_outputs"][0], "name": "new-slot.png"}]}))
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
    wait_until(lambda: len(models) == 2)
    if not damaged:
        child_id = models[-1]["turn_id"]
        preview = wait_until(lambda: (state.conversations.get_turn(child_id)["result"] or {}).get("response", {}).get("edit_preview"))
        assert state.production.get_edit_proposal(state.workspace_id, preview["proposal"]["id"]).status == "proposed"
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
    assert len(state.production.list_job_runs(state.workspace_id, state.task.id)) == 1
    if not damaged:
        chat = state.conversations.get(chat["id"], state.workspace_id, state.member.member_id)
        new_instruction = "新的独立指令：重新生成同名图片"
        chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, new_instruction, chat["draft_version"])
        task = state.production.get_creation_task(state.workspace_id, state.task.id)
        version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
        new = state.raw_post(state.base_url + "/assistant/turn", json={"conversation_id": chat["id"], "message": new_instruction,
            "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"], "context_stage": "montage",
            "selected_object_refs": [], "expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence})
        assert new.status_code == 200 and new.json()["media_job"]["status"] == "queued", new.text
        assert new.json()["media_job"]["job_id"] != receipts[0]["job_id"]
        assert len(state.production.list_job_runs(state.workspace_id, state.task.id)) == 2 and len(state.calls) == 1


@pytest.mark.parametrize("shutdown,operation", [(False, "ChangeCrop"), (True, "ChangeCrop"), (False, "DeleteClip")])
def test_normal_endpoint_preview_adoption_resumes_original_instruction_once(state, monkeypatch, shutdown, operation):
    state.finish()  # Old history must never become an inferred goal.
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    calls, jobs = [], []
    if operation == "DeleteClip":
        def no_media(*_):
            pytest.fail("this continuation receipt test must not run a media executor")
        execution = CreationExecution(state.production, run_narration=no_media, run_montage_plan=no_media,
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
                    current = state.production.get_creation_task(state.workspace_id, state.task.id)
                    script = state.production.get_creation_task_script(state.workspace_id, current.confirmed_script_id)
                    plan = state.production.get_creation_plan(state.workspace_id, current.plan_id)
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
        state.production.claim_job_run(state.workspace_id, jobs[0].id)
        # A later actual terminal receipt must retain the prior proved deletion.
        state.production.finish_job_run(state.workspace_id, jobs[0].id, status="succeeded")
        main._wake_assistant_continuations()
        wait_until(lambda: len(calls) == 3)
    if shutdown:
        child_id = calls[-1]["turn_id"]
        wait_until(lambda: len(state.production.list_job_runs(state.workspace_id, state.task.id)) == 2)
        main.stop_assistant_continuation_driver()
        child = state.conversations.get_turn(child_id)
        assert child["status"] == "failed" and child["result"]["reason"] == "api_shutdown"
        assert state.conversations.get_task_goal(root_id)["status"] == "waiting_unknown"
        main.start_assistant_continuation_driver()
        assert main._goal_service().reconcile() == 0 and len(calls) == 2
        assert state.runtime.run_once()
        proposal = wait_until(lambda: state.conversations.get_turn(child_id)["result"].get("response", {}).get("edit_preview"))
        second = state.raw_post(state.base_url + f"/edits/{proposal['proposal']['id']}/confirm", json={"request_id": str(uuid4())})
        assert second.status_code == 200, second.text
        wait_until(lambda: len(calls) == 3)
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == "no_progress")
    turns = state.conversations.list_turns(chat["id"])
    assert turns[-1]["user_message"] == "" and calls[-1]["message"] == raw
    assert turns[-1]["status"] == "completed"
    main.stop_assistant_continuation_driver()
    main.start_assistant_continuation_driver()
    assert main._goal_service().reconcile() == 0 and len(calls) == (3 if shutdown or operation == "DeleteClip" else 2)


@pytest.mark.parametrize("code", [403, 409, 422])
def test_original_request_correction_requires_actual_422_no_side_effect(state, monkeypatch, code):
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    request_id = uuid5(NAMESPACE_URL, json.dumps([state.task.id, state.turn["id"], "validation"], separators=(",", ":")))
    locator = main._resolve_assistant_edit_request(state.task.id, state.request, state.turn["id"], {
        "kind": "edit_request_status", "task_id": state.task.id, "request_id": str(request_id),
        "query_url": state.base_url + f"/assistant/edit-requests/{request_id}?phase=preview",
        "status": "not_submitted", "assistant_turn_id": state.turn["id"], "tool_call_id": "validation"})
    # The host derives the deterministic request id; use that exact receipt.
    request_id = locator.request_id
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    event_id, fresh = main._claim_edit_request(task, state.member.member_id, request_id, "preview", {"fixture": "validation"})
    assert fresh
    main._save_edit_request_result(task, event_id, main.JSONResponse(status_code=code, content={"detail": "actual validation"}))
    state.finish(edit_request=locator.model_dump(mode="json"))
    goal = main._goal_service().register_turn(state.turn["id"])
    event = {"type": "business_result", "event_id": "validation", "task_id": state.task.id, "phase": "request",
        "base_version_id": state.version.id, "status": "rejected", "edit_request": locator.model_dump(mode="json")}
    verified = main._verify_current_goal(goal, event)
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
    execution = CreationExecution(state.production, run_narration=no_media, run_montage_plan=no_media,
                                  run_render=no_media, render_is_valid=lambda *_: True)
    monkeypatch.setattr(main, "creation_execution", execution)
    calls, jobs = [], []
    def model(_settings, **kwargs):
        calls.append(kwargs)
        if kwargs.get("continuation_context"):
            assert kwargs["continuation_context"]["business_event"]["phase"] == "execution"
            assert kwargs["continuation_context"]["current_facts"]["execution_status"]["job"]["status"] == "succeeded"
            return "已读取原作业与实际登记输出。", None
        current = state.production.get_creation_task(state.workspace_id, state.task.id)
        script = state.production.get_creation_task_script(state.workspace_id, current.confirmed_script_id)
        plan = state.production.get_creation_plan(state.workspace_id, current.plan_id)
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
    state.production.claim_job_run(state.workspace_id, jobs[0].id)
    state.production.finish_job_run(state.workspace_id, jobs[0].id, status="succeeded")
    if stale:
        state.production.create_timeline_version(replace(state.version, id="unrelated-version", sequence=2,
                                                         parent_version_id=state.version.id, snapshot={}))
    main.start_assistant_continuation_driver()
    expected = "invalidated" if stale else "no_progress"
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == expected)
    assert len(calls) == (1 if stale else 2)
    assert main._project_assistant_execution_results() == 0
    assert main._goal_service().reconcile() == 0
    assert len([job for job in state.production.list_job_runs(state.workspace_id, state.task.id)
                if job.kind == "creation_task_execution"]) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("continuation_failure", [False, True])
def test_actual_start_is_persisted_before_model_failure_then_original_job_continues(state, monkeypatch, streaming, continuation_failure):
    state.finish()
    material_execution_input(state)
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    execution = CreationExecution(state.production, run_narration=lambda *_: None, run_montage_plan=lambda *_: None,
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
        located = state.production.get_task_event(state.workspace_id, "event_assistant_execution_locator_" + turn_id)
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
    state.production.claim_job_run(state.workspace_id, job_ids[0])
    state.production.finish_job_run(state.workspace_id, job_ids[0], status="succeeded")
    original = state.client.get(state.base_url + "/execution", params={"job_id":job_ids[0]})
    assert original.status_code == 200 and original.json()["job"]["id"] == job_ids[0]
    main.start_assistant_continuation_driver()
    wait_until(lambda: state.conversations.get_task_goal(root_id)["status"] == (
        "waiting_unknown" if continuation_failure else "no_progress"))
    if continuation_failure:
        child = state.conversations.get_turn(calls[-1]["turn_id"])
        assert child["status"] == "failed" and child["result"]["kind"] == "failure"
        assert "script_goal_conflict" in child["result"]["message"]
        assert child["assistant_text"] == child["result"]["message"]
        assert "/Volumes/" not in child["assistant_text"]
        assert child["result"].get("reason") != "goal_runner_interrupted"
    assert len(calls) == 2 and main._project_assistant_execution_results() == 0
    assert len([job for job in state.production.list_job_runs(state.workspace_id, state.task.id) if job.kind == "creation_task_execution"]) == 1


@pytest.mark.parametrize("submitted", [False, True])
def test_actual_execution_read_registers_goal_only_for_own_submitted_job(state, monkeypatch, submitted):
    state.finish()
    material_execution_input(state)
    monkeypatch.setattr(main, "quality_check_repo", QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _:SimpleNamespace(
        effective_llm_provider="fixture", effective_llm_model="fixture", effective_llm_api_key=""))
    execution = CreationExecution(state.production, run_narration=lambda *_:None, run_montage_plan=lambda *_:None,
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
        state.production.claim_job_run(state.workspace_id,job_ids[0])
        state.production.finish_job_run(state.workspace_id,job_ids[0],status="failed",failure_reason="isolated old failed job",
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
            state.production.get_task_event(state.workspace_id,"event_assistant_execution_locator_"+turn["id"])
        assert main._project_assistant_execution_results() == 0
    else:
        assert result["execution_action"] == "start" and "本轮未提交" not in result["reply"]
        assert turn["result"]["goal_business_actions"] == [{"kind":"execution_status", "id":job_ids[0]}]
        assert state.conversations.get_task_goal(turn["id"])["status"] == "waiting_result"
        state.production.claim_job_run(state.workspace_id,job_ids[0])
        state.production.finish_job_run(state.workspace_id,job_ids[0],status="succeeded")
        assert main._project_assistant_execution_results() == 1
        assert main._goal_service().reconcile() == 1 and len(calls)==2
        assert state.conversations.get_task_goal(turn["id"])["status"] == "no_progress"
    assert len([job for job in state.production.list_job_runs(state.workspace_id,state.task.id)
        if job.kind=="creation_task_execution"]) == 1


def test_consumed_input_observes_existing_job_then_resumes_without_starting_again(state,monkeypatch):
    state.finish()
    material_execution_input(state)
    monkeypatch.setattr(main,"quality_check_repo",QualityCheckRepository(state.production.database_path))
    monkeypatch.setattr(main,"_workspace_settings_for",lambda _:SimpleNamespace(
        effective_llm_provider="fixture",effective_llm_model="fixture",effective_llm_api_key=""))
    execution=CreationExecution(state.production,run_narration=lambda *_:None,run_montage_plan=lambda *_:None,
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
        input_id=str(uuid4());input_ids.append(input_id)
        accepted=state.raw_post(f"/api/assistant/conversations/{chat['id']}/inputs",json={"target_turn_id":turn_id,
            "client_request_id":input_id,"message":message,"mode":"follow_up","expected_draft_version":draft["draft_version"],
            "expected_reference_version":draft["reference_version"]})
        assert accepted.status_code == 200,accepted.text
        handler=kwargs["input_handler"]
        assert handler("next",mode="any")["input_id"] == input_id
        assert handler("consume",input_id)["environment"]["AI_VIDEO_DIARY_AGENT_INPUT_ID"] == input_id
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
    located=state.production.get_task_event(state.workspace_id,main._assistant_execution_locator_id(root_id,input_ids[0]))
    assert located.payload["source"]["action"] == "observe" and located.job_id == job_ids[0]
    state.production.claim_job_run(state.workspace_id,job_ids[0])
    state.production.finish_job_run(state.workspace_id,job_ids[0],status="succeeded")
    assert main._project_assistant_execution_results() == 2
    assert main._goal_service().reconcile() == 1 and len(calls) == 2
    assert main._goal_service().reconcile() == 0
    assert len([job for job in state.production.list_job_runs(state.workspace_id,state.task.id) if job.kind == "creation_task_execution"]) == 1


@pytest.mark.parametrize("mutation", ["none", "watched_input", "hash", "version", "preview", "foreign_goal", "no_video", "pending", "unknown"])
def test_bounded_checkpoint_requires_same_goal_current_final_hash_and_no_pending(monkeypatch, mutation):
    # Real SQL selection with host/media-reader seams; this is not media acceptance.
    import sqlite3
    monkeypatch.setattr(main,'_workspace_settings_for',lambda _:SimpleNamespace(effective_llm_api_key=''))
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript('CREATE TABLE device_work_requests(id,workspace_id,owner_id,state);'
        'CREATE TABLE task_events(id,workspace_id,task_id,job_id,event_type,payload_json);'
        'CREATE TABLE job_runs(id,workspace_id,task_id,kind,status);')
    goal = {'original_turn_id':'root','conversation_id':'chat','member_id':'member'}
    output = {'artifact_id':'video-artifact','artifact_kind':'rendered_video','sha256':'a'*64,
        'registered_width':1080,'registered_height':1920,'size_bytes':100,'producer_job_id':'render-child',
        'logical_ref':'artifacts/task/video.mp4','mtime_ns':1,'ctime_ns':2}
    source = {'job_id':'execution','input_digest':'b'*64,'source':{'assistant_turn_id':'producer',
        'expected_timeline_version_id':'base','conversation_id':'chat','member_id':'member'}}
    connection.execute('INSERT INTO device_work_requests VALUES(?,?,?,?)',('render-request','workspace','execution','done'))
    connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('locator','workspace','task','execution',
        'assistant_execution_located',json.dumps(source)))
    start = {'arguments':{'_typed_source':{'assistant_turn_id':'observer'}},'input_facts':{'output':dict(output)}}
    if mutation == 'watched_input':
        goal.update(source_input_id='watched-input',source_input_turn_id='root')
        watched={**source,'source':{**source['source'],'input_id':'watched-input','input_turn_id':'root','action':'observe'}}
        connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('watch-locator','workspace','task','execution',
            'assistant_execution_located',json.dumps(watched)))
        start['arguments']['_typed_source'].update(input_id='watched-input',input_turn_id='root')
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
        source = {'assistant_turn_id':'pending','conversation_id':'chat','member_id':'member'}
        if mutation == 'pending':
            connection.execute('INSERT INTO job_runs VALUES(?,?,?,?,?)',('pending-job','workspace','task',main.TASK_MEDIA_KIND,'queued'))
            payload = {'arguments':{'_typed_source':source}}
            connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('event_pending-job_start','workspace','task','pending-job','task_media_submitted',json.dumps(payload)))
        else:
            payload = {'source':source,'locator':{'status':'unknown','request_id':str(uuid4()),'phase':'preview'}}
            connection.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?)',('unknown','workspace','task',None,'assistant_edit_request_located',json.dumps(payload)))
    repository=SimpleNamespace(_connect=lambda:connection,get_task_event=lambda *_:SimpleNamespace(payload=start))
    from types import MethodType
    from app.production import ProductionRepository
    repository.goal_output_provenance_rows=MethodType(ProductionRepository.goal_output_provenance_rows,repository)
    monkeypatch.setattr(main,'production_repo',repository)
    monkeypatch.setattr(main,'conversation_repo',SimpleNamespace(get_task_goal=lambda identifier:
        {'original_turn_id':'foreign' if mutation=='foreign_goal' and identifier=='producer' else 'root'}))
    monkeypatch.setattr(main,'_registered_observation_output',lambda *_:(None,output,{'kind':'composed_output'}))
    monkeypatch.setattr(main,'_read_bound_execution_result',lambda *_:{'job':{'id':'execution','status':'succeeded','input_digest':'b'*64},'video_ready':mutation!='no_video'})
    monkeypatch.setattr(main,'_goal_request_result',lambda *_:{'status':'unknown'})
    task=SimpleNamespace(workspace_id='workspace',id='task')
    version=SimpleNamespace(id='final',sequence=2,parent_version_id='base',snapshot={
        'creation_render_request_id':'render-request','render_input_timeline_version_id':'base'})
    checkpoint=main._goal_output_checkpoint(goal,{'phase':'observation','status':'succeeded'},
        {'media_job':{'job_id':'observation','status':'succeeded','result':result}},task,version,None,None)
    if mutation in {'none','watched_input'}:
        assert checkpoint['scope']=='production_and_output_observation' and checkpoint['sha256']=='a'*64
        assert checkpoint['unavailable']==result['unavailable']
    else:
        assert checkpoint is None
    connection.close()


@pytest.mark.parametrize('rejection', ['foreign_turn','old_version','new_instruction','stopped'])
def test_assistant_execution_query_cannot_authorize_foreign_or_stale_instruction(state, rejection):
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
    current=state.production.get_creation_task(state.workspace_id,state.task.id)
    script=state.production.get_creation_task_script(state.workspace_id,current.confirmed_script_id)
    plan=state.production.get_creation_plan(state.workspace_id,current.plan_id)
    preview={'script_id':script.id,'input_digest':revision_digest(script,plan)}
    before=[job.id for job in state.production.list_job_runs(state.workspace_id,state.task.id)]
    response=state.raw_post(state.base_url+'/execution/start?assistant_turn_id='+(str(uuid4()) if rejection=='foreign_turn' else turn['id']),
        json={'script_id':preview['script_id'],'input_digest':preview['input_digest'],'requirements_confirmed':True})
    assert response.status_code==409,response.text
    assert [job.id for job in state.production.list_job_runs(state.workspace_id,state.task.id)]==before


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
    from test_montage_pipeline import _person_render_edit_state
    from _media_preview_test_fixtures import ControlledResources
    root=tmp_path/'source-registration'
    root.mkdir()
    s=_person_render_edit_state(root,monkeypatch,'local')
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
    task=repo.get_creation_task('local',s.task.id)
    base=case.metadata.ensure_montage_base('local',task,s.montage)
    task=repo.get_creation_task('local',s.task.id)
    action=({'narration_segment_id':s.montage.cues[0].narration_segment_id}
        if operation=='RegenerateNarrationSegment' else {'clip_id':s.montage.clips[0].id})
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
        job=repo.get_job_run('local',result.value['job_id'])
        event=repo.get_task_event('local',f'event_{job.id}_start').payload
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
            with conversations._connect() as conn: conversations._write_goal(conn,{'id':turn['id']},goal)
            intent=SubmissionIntent('local','creation_job',job.id,job.input_digest,request.unit_key,request.policy)
            registration.owner.verify_submission(intent,actor)
            with pytest.raises(EditPreviewRejected):
                case.apply(task,actor,payload.model_copy(update={'request_id':uuid4()}),
                    identity={'member_id':actor.member_id,'local_diagnostic':False})
            with conversations._connect() as conn:
                conversations._write_goal(conn,{'id':turn['id']},{**goal,'status':'stopped'})
            with pytest.raises(ValueError): registration.owner.verify_submission(intent,actor)
            assert repo.get_task_event('local',f'event_{job.id}_start').payload==event

        assert s.provider.output_cache_keys==calls
        assert repo.get_creation_task('local',task.id).current_timeline_version_id==base.id
    finally:
        assert runtime.stop(1)


@pytest.mark.parametrize('case_kind',['adopt','revision','stale','revoked','cancelled','queue_gap'])
def test_segment_visual_http_reads_and_adopts_the_original_job(tmp_path,monkeypatch,case_kind):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.creation.execution_control import CreationExecutionControl
    from app.assistant.task_business import TaskBusiness
    from test_creation_execution import _visual_decision_case,_require_section_visual_decision,submit_creation_stage,_submit_section_revision
    root=tmp_path/'visual-http';root.mkdir()
    case=_visual_decision_case(root,monkeypatch)
    submit_creation_stage(case.execution,case.scheduler,case.job,case.actor)
    if case_kind=='revision':
        for _ in range(2): case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
        _,revision,_=_submit_section_revision(case)
        job=case.repo.get_job_run('local',revision['job_id'])
    else: job=case.job
    _require_section_visual_decision(case,monkeypatch)
    grant=case.scheduler.pick_and_grant()
    assert grant.owner_id==job.id
    case.executor.execute(grant,case.scheduler)
    control=CreationExecutionControl(case.repo,case.execution,SimpleNamespace(valid=lambda *a,**kw:False),
        case.owner.verify_identity,segments=case.segments)
    runtime=SimpleNamespace(scheduler=case.scheduler,wake=lambda:None,
        registrations={'creation_job':SimpleNamespace(owner=case.owner,executor=case.executor)})
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    monkeypatch.setattr(main,'_readable_creation_task',lambda request,task_id:case.repo.get_creation_task('local',task_id))
    monkeypatch.setattr(main,'_request_permit_actor',lambda request,workspace:case.actor)
    monkeypatch.setattr(main,'_verify_creation_identity',case.owner.verify_identity)
    def current_actor(request,workspace):
        from fastapi import HTTPException
        if case.owner.verify_identity(case.actor)!='ready': raise HTTPException(403,'原成员授权已撤销')
        return case.actor
    monkeypatch.setattr(main,'_require_permit_actor',current_actor)
    monkeypatch.setattr(main,'_creation_execution_control',lambda:control)
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(main,'_require_automation_task_open',lambda *args:None)
    app=FastAPI()
    app.add_api_route('/api/creation-tasks/{task_id}/execution',main.get_creation_execution,methods=['GET'])
    path='/api/creation-tasks/{task_id}/execution/jobs/{job_id}/sections/{section_id}/visual-decision'
    app.add_api_route(path,main.get_segment_visual_decision,methods=['GET'])
    app.add_api_route(path,main.adopt_segment_visual_decision,methods=['POST'])
    client=TestClient(app)
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
            case.actor,case.repo.get_creation_task('local',case.task_id),{}, {},{},None)
        assert read==decision
        with pytest.raises(PermissionError):
            control.read_visual_decision('local',case.task_id,job.id,'section_1',replace(case.actor,member_id='other-member'))
        payload={'decision_digest':decision['decision_digest'],'confirmed_clip_ids':decision['required_confirmation_ids']}
        assert client.post(locator['detail_url'],json={**payload,'automatically_confirm':True}).status_code==422
        audio_path=case.repo.resolve_authorized_narration_audio_reference('local',decision['audio']['artifact_reference'])
        before_audio=audio_path.read_bytes()
        before_segments=case.repo.list_narration_segments('local',case.task_id,case.script_id)
        before_usage=case.repo.creation_narration_usage('local',job.id)
        monkeypatch.setattr(case.stage,'prepare_segment',lambda *a,**kw:pytest.fail('不能重新生成已保留配音'))
        if case_kind=='stale': payload['decision_digest']='0'*64
        elif case_kind=='revoked': monkeypatch.setattr(case.owner,'verify_identity',lambda actor:'unauthorized')
        elif case_kind=='cancelled':
            case.repo.finish_job_run('local',job.id,status='cancelled',failure_reason='user_cancelled',impact='保留音频',repair_action='核对原任务')
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
            assert case.segments.video_preparation_id(case.job,'section_1') not in [e.id for e in case.repo.list_task_events('local',case.task_id)]
        assert audio_path.read_bytes()==before_audio
        assert case.repo.list_narration_segments('local',case.task_id,case.script_id)==before_segments
        assert case.repo.creation_narration_usage('local',job.id)==before_usage
    finally:
        case.scheduler.close()


def test_segment_visual_business_locator_uses_persisted_review_goal_not_a_tool_boolean(tmp_path,monkeypatch):
    from app.creation.contracts import JobRun,TaskEvent
    from app.creation.execution_control import CreationExecutionControl
    from app.assistant.task_business import TaskBusiness,TaskBusinessRejected
    from app.conversation_repository import ConversationRepository
    from test_creation_execution import _visual_decision_case
    root=tmp_path/'visual-origin';root.mkdir()
    case=_visual_decision_case(root,monkeypatch)
    conversations=ConversationRepository(case.repo.database_path)
    chat=conversations.create('local',case.actor.member_id,'task',case.task_id)
    conversations.save_draft(chat['id'],'local',case.actor.member_id,'继续修改当前段',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],'local',case.actor.member_id,'继续修改当前段',str(uuid4()),1,
        {'kind':'task','task_id':case.task_id})
    goal={'original_turn_id':turn['id'],'current_turn_id':turn['id'],'workspace_id':'local',
        'task_id':case.task_id,'member_id':case.actor.member_id,'status':'waiting_result'}
    with conversations._connect() as conn: conversations._write_goal(conn,{'id':turn['id']},goal)
    source={'assistant_turn_id':turn['id'],'input_id':turn['id'],'conversation_id':chat['id']}
    case.repo.append_task_event(TaskEvent('visual-review','local',case.task_id,None,'narration_semantic_review',{'source':source}))
    job=case.repo.create_job_run(JobRun('visual-revision','local',case.task_id,'creation_task_segment_revision','visual-revision','original-digest'))
    task=case.repo.get_creation_task('local',case.task_id)
    case.repo.append_task_event(TaskEvent(f'event_{job.id}_start','local',case.task_id,job.id,'segment_revision_requested',
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
        with pytest.raises(TaskBusinessRejected): business.read_execution(foreign,case.actor,job_id=job.id)
    finally:
        case.scheduler.close()


def test_segment_visual_empty_locator_does_not_require_a_generated_script(tmp_path):
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    from app.creation_execution import CreationExecutionActor
    from test_metadata_edit_use_case import metadata_state
    root=tmp_path/'no-visual-job';root.mkdir()
    s=metadata_state(root)
    service=SegmentProductionUseCase(s.repo,narration_stage=None,renderer=None,artifact_store=s.store,output_root=root)
    assert s.task.confirmed_script_id is None
    assert service.visual_decision_locators('ws',s.task.id,CreationExecutionActor('ws',s.member.member_id))=={
        'items':[],'total_count':0,'truncated':False}
