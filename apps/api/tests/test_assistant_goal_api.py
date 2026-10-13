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
from test_media_script_api import state as script_state  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_local_workspace(state, monkeypatch):
    # Existing domain fixtures name their single local workspace workspace_a.
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", state.workspace_id)


def wait_until(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    pytest.fail("normal lifecycle driver did not advance the actual result")


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
    execute = media.execute_operation
    def bounded_media_seam(job, event, actor):
        if event["operation"] == "output_observation":
            # The media observer is a seam; real device/JobRun/event/HTTP ownership is exercised.
            return {"observation_id": job.id, "task_id": job.task_id, "fixture": "bounded_reader_result"}
        return execute(job, event, actor)
    monkeypatch.setattr(media, "execute_operation", bounded_media_seam)
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
    raw = "检查实际结果并准备修改；等我实际采用，再继续生成"
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    result = state.raw_post(state.base_url + "/assistant/turn", json={"conversation_id": chat["id"], "message": raw,
        "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"], "context_stage": "review",
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
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    response = state.raw_post(state.base_url + "/assistant/turn", json={"conversation_id": chat["id"], "message": raw,
        "client_request_id": str(uuid4()), "expected_draft_version": chat["draft_version"], "context_stage": "review",
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
            assert kwargs["selected_refs"] == ([] if operation == "DeleteClip" else [f"clip:{state.clip.id}"])
            if operation == "DeleteClip":
                assert kwargs["task_version"]["removed_selected_refs"] == [f"clip:{state.clip.id}"]
                if len(calls) == 2:
                    current = state.production.get_creation_task(state.workspace_id, state.task.id)
                    script = state.production.get_creation_task_script(state.workspace_id, current.confirmed_script_id)
                    plan = state.production.get_creation_plan(state.workspace_id, current.plan_id)
                    actor = main._require_permit_actor(state.request, state.workspace_id)
                    jobs.append(execution.start(state.workspace_id, current.id, script.id, revision_digest(script, plan), actor=actor))
                    return "已按删除后的当前版本登记制作。", {"kind": "execution_action", "action": "start",
                        "status": main._creation_execution_status(state.workspace_id, current.id)}
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
    chat = state.conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    response = state.raw_post(state.base_url + "/assistant/turn", json={
        "conversation_id": chat["id"], "message": raw, "client_request_id": str(uuid4()),
        "expected_draft_version": chat["draft_version"], "context_stage": "review",
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
        jobs.append(execution.start(state.workspace_id, current.id, script.id, revision_digest(script, plan), actor=actor))
        return "已登记原制作作业。", {"kind": "execution_action", "action": "start",
                                    "status": main._creation_execution_status(state.workspace_id, current.id)}
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
