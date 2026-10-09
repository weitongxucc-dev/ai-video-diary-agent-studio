from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.creation_execution import CreationExecutionActor
from test_narration_edit_execution import fixture, unchanged  # noqa: F401


@pytest.fixture
def narration_api(fixture, monkeypatch):  # noqa: F811 (explicit existing pytest fixture import)
    state = fixture
    monkeypatch.setattr(main_module, "production_repo", state.repo)
    monkeypatch.setattr(main_module, "local_members_service", state.members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "local")
    # The real roster and owner verifier check this controlled request actor;
    # no process, host sampling, model, or background service is started.
    monkeypatch.setattr(main_module, "_require_permit_actor", lambda *_: state.actor)
    monkeypatch.setattr(main_module, "_request_permit_actor", lambda *_: state.actor)
    monkeypatch.setattr(main_module, "_creation_runtime", lambda: state.runtime)
    state.client = TestClient(main_module.app)
    state.confirm_url = f"/api/creation-tasks/{state.task.id}/edits/{state.proposal.id}/confirm"
    return state


def test_confirm_queues_once_and_publishes_only_after_real_grant(narration_api):
    state = narration_api
    state.resources.allowed = False
    first = state.client.post(state.confirm_url, json={})
    assert first.status_code == 202, first.text
    receipt = first.json()
    assert receipt["status"] == "queued"
    assert state.client.post(state.confirm_url, json={}).json() == receipt
    assert not state.runtime.run_once()
    assert not state.calls
    unchanged(state)
    request = state.runtime.scheduler._get(receipt["request_id"])
    assert request.unit_key.startswith("narration_edit:")
    assert request.work_class == "preview_dependency"
    state.resources.allowed = True
    assert state.runtime.run_once()
    assert len(state.calls) == 1
    assert state.repo.get_creation_task("local", state.task.id).current_timeline_version_id == "result"
    assert state.repo.get_job_run("local", receipt["job_id"]).status == "succeeded"
    assert state.client.post(state.confirm_url, json={}).status_code == 409


def test_queue_cancel_preserves_audio_and_version(narration_api):
    state = narration_api
    state.resources.allowed = False
    response = state.client.post(state.confirm_url, json={})
    assert response.status_code == 202, response.text
    receipt = response.json()
    response = state.client.post(f"/api/device/queue/{receipt['request_id']}/cancel", json={})
    assert response.status_code == 200, response.text
    assert state.repo.get_job_run("local", receipt["job_id"]).status == "cancelled"
    assert not state.runtime.run_once()
    assert not state.calls
    unchanged(state)


def test_confirm_rechecks_stale_audio_before_any_device_request(narration_api):
    state = narration_api
    state.segments[0] = replace(state.segments[0], audio_reference="narration://artifact/other")
    state.repo.save_narration_segments("local", state.task.id, "script", state.segments)
    response = state.client.post(state.confirm_url, json={})
    assert response.status_code == 409, response.text
    assert not state.calls
    unchanged(state)
    with state.repo._connect() as connection:
        assert connection.execute("SELECT count(*) FROM device_work_requests").fetchone()[0] == 0


def test_other_member_cannot_acquire_local_narration_grant(narration_api, monkeypatch):
    state = narration_api
    member = state.members.add_member("other synthetic member")
    other = CreationExecutionActor("local", member.member_id)
    monkeypatch.setattr(main_module, "_require_permit_actor", lambda *_: other)
    response = state.client.post(state.confirm_url, json={})
    assert response.status_code == 403, response.text
    assert not state.calls
    unchanged(state)


@pytest.fixture
def generator_api(narration_api, tmp_path, monkeypatch):
    from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
    from app.production import SubtitleTrack, TimelineVersion
    from test_montage_pipeline import _asset
    from test_narration_edit_execution import wav_bytes
    from app.narration import NarrationArtifact
    state = narration_api
    state.segments = [replace(item, language="zh") for item in state.segments]
    state.repo.save_narration_segments("local", state.task.id, "script", state.segments)
    asset = _asset(tmp_path / "library", workspace_id="local", owner_user_id=state.task.owner_user_id)
    state.repo._save_asset(asset)
    montage = CreationTaskMontagePlan("full-montage", "local", state.task.id, "script", "合成", "ready",
        [NarrationCue(f"cue-{i}", f"section-{i}", f"第{i}段", 2, f"n-{i}") for i in (1, 2)],
        [MontageClip(f"clip-{i}", f"cue-{i}", asset.id, asset.segments[i-1].id,
                     state.repo.workspace_media_reference_for_asset("local", asset.id),
                     (i-1)*8, (i-1)*8+2, (i-1)*2, i*2, 1, ["合成"], False) for i in (1, 2)],
        4, 0, narration_audio_path="narration://artifact/old")
    payload = creation_task_montage_plan_to_payload(montage)
    state.repo.save_creation_task_montage_plan("local", state.task.id, montage.id, payload)
    state.base = state.repo.create_timeline_version(TimelineVersion("full-base", "local", state.task.id,
        "base", 2, {"montage": payload}, None))
    state.proposal = state.repo.create_edit_proposal(replace(state.proposal, id="full-proposal",
        operation={"operation_type": "RegenerateNarrationSegment",
                   "payload": {**state.proposal.operation["payload"], "base_version_id": state.base.id}}))
    state.task = state.repo.get_creation_task("local", state.task.id)
    state.confirm_url = f"/api/creation-tasks/{state.task.id}/edits/{state.proposal.id}/confirm"
    state.before_clips = payload["clips"]
    state.original_track = SubtitleTrack("unavailable", "local", state.task.id, state.base.id, "en-US", "zh-CN",
        {}, [], "translation", None, None, None, "unavailable", "未翻译", "无英文输出", "选择模型后翻译")
    state.repo.create_subtitle_track(state.original_track)
    state.executor.stage_actions["narration_edit"] = main_module._narration_edit_stage_with_grant
    state.generated_calls = []
    state.generated_failure = None
    state.generated_oversize = False
    state.commit_failure = False
    def generated(**kwargs):
        context = kwargs["grant_context"]
        context.check()
        assert context.grant.unit_key.startswith("narration_edit:")
        assert kwargs["repository"] is state.repo
        assert kwargs["viewer_user_id"] == state.task.owner_user_id
        assert kwargs["segment_id"] == "n-1"
        state.generated_calls.append(kwargs)
        if state.generated_failure:
            raise state.generated_failure
        path = tmp_path / "generated.wav"
        path.write_bytes(b"x" * (context.max_output_bytes+1) if state.generated_oversize else wav_bytes())
        unchanged_task = state.repo.get_creation_task("local", state.task.id)
        assert unchanged_task.current_timeline_version_id == state.base.id
        assert state.repo.get_narration_artifact("local", "1" * 64) is None
        return NarrationArtifact(task_id=state.task.id, script_id="script", audio_path=str(path), duration_seconds=2,
            sample_rate=8000, model_id="synthetic-provider", voice_profile_id=state.plan.selected_voice.profile_id,
            cache_key="1" * 64, language="zh", pronunciation_version="rules-v1")
    def combined(**kwargs):
        context = kwargs["grant_context"]
        context.check()
        assert context.max_output_bytes < state.budget.max_output_bytes
        assert [item.cache_key for item in kwargs["segments"]] == ["1" * 64, "old"]
        assert state.repo.get_narration_artifact("local", "1" * 64) is None
        path = tmp_path / "combined.wav"
        path.write_bytes(wav_bytes())
        return NarrationArtifact(task_id=state.task.id, script_id="script", audio_path=str(path), duration_seconds=4,
            sample_rate=8000, model_id="synthetic-provider", voice_profile_id=state.plan.selected_voice.profile_id,
            cache_key="2" * 64, language="zh", pronunciation_version="rules-v1")
    monkeypatch.setattr(main_module.narration_generator, "generate_for_creation_task", generated)
    monkeypatch.setattr(main_module.narration_generator, "combine_creation_task_segments", combined)
    monkeypatch.setattr(main_module, "_publish_subtitle_text", lambda **_: pytest.fail("不能在配音终态另写字幕产物"))
    actual_checkpoint = state.executor._narration_edit_checkpoint
    def checkpoint(*args, **kwargs):
        result = actual_checkpoint(*args, **kwargs)
        if state.commit_failure and args[2] == "succeeded":
            raise ValueError("受控终态事务失败")
        return result
    monkeypatch.setattr(state.executor, "_narration_edit_checkpoint", checkpoint)
    return state


def test_actual_main_generator_callback_commits_audio_version_and_job_atomically(generator_api):
    state = generator_api
    state.resources.allowed = False
    response = state.client.post(state.confirm_url, json={})
    assert response.status_code == 202, response.text
    assert not state.runtime.run_once()
    assert not state.generated_calls
    assert state.repo.get_creation_task("local", state.task.id).current_timeline_version_id == state.base.id
    state.resources.allowed = True
    assert state.runtime.run_once()
    assert len(state.generated_calls) == 1
    completed_job = state.repo.get_job_run("local", response.json()["job_id"])
    assert completed_job.status == "succeeded", completed_job.failure_reason
    current = state.repo.get_creation_task("local", state.task.id)
    assert current.current_timeline_version_id != state.base.id
    assert current.narration_cache_key == "2" * 64 and not current.output_ready
    assert state.repo.get_narration_artifact("local", "1" * 64) is not None
    assert state.repo.get_narration_artifact("local", "2" * 64) is not None
    segments = state.repo.list_narration_segments("local", state.task.id, "script")
    assert segments[0].audio_reference == "narration://artifact/" + "1" * 64
    assert segments[1] == state.segments[1]
    montage = state.repo.get_creation_task_montage_plan("local", state.task.id)
    assert montage["clips"][1] == state.before_clips[1]
    tracks = state.repo.list_subtitle_tracks("local", state.task.id, timeline_version_id=current.current_timeline_version_id)
    assert len(tracks) == 1
    assert (tracks[0].status, tracks[0].failure_reason, tracks[0].impact, tracks[0].repair_action) == (
        "unavailable", "未翻译", "无英文输出", "选择模型后翻译")
    assert state.repo.get_job_run("local", response.json()["job_id"]).status == "succeeded"
    assert state.runtime.scheduler._get(response.json()["request_id"]).state == "done"


@pytest.mark.parametrize("failure", ["provider", "budget", "transaction"])
def test_actual_callback_failure_keeps_original_audio_and_version(generator_api, failure):
    state = generator_api
    if failure == "provider":
        state.generated_failure = ValueError("合成provider失败")
    elif failure == "budget":
        state.generated_oversize = True
    else:
        state.commit_failure = True
    response = state.client.post(state.confirm_url, json={})
    assert response.status_code == 202, response.text
    state.resources.allowed = True
    assert state.runtime.run_once()
    assert state.repo.get_creation_task("local", state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.list_narration_segments("local", state.task.id, "script") == state.segments
    assert state.repo.get_narration_artifact("local", "1" * 64) is None
    assert state.repo.get_narration_artifact("local", "2" * 64) is None
    assert state.repo.get_job_run("local", response.json()["job_id"]).status == "failed"


@pytest.mark.parametrize("mutation", ["other_segment", "other_source", "montage"])
def test_queue_seals_all_audio_and_montage_consumed_by_combination(generator_api, mutation):
    state = generator_api
    response = state.client.post(state.confirm_url, json={})
    assert response.status_code == 202, response.text
    if mutation == "other_segment":
        state.repo.save_narration_segments("local", state.task.id, "script",
            [state.segments[0], replace(state.segments[1], pronunciation_version="changed-rules")])
    elif mutation == "other_source":
        from pathlib import Path
        path = Path(state.repo.get_narration_artifact("local", "old").audio_path)
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        montage = state.repo.get_creation_task_montage_plan("local", state.task.id)
        montage["clips"][1]["crop_x"] = 0.4
        state.repo.save_creation_task_montage_plan("local", state.task.id, montage["id"], montage)
    state.resources.allowed = True
    assert not state.runtime.run_once()
    assert not state.generated_calls
    assert state.repo.get_creation_task("local", state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.get_narration_artifact("local", "1" * 64) is None


def test_actual_generator_cancellation_retains_original_facts(generator_api, monkeypatch):
    state = generator_api
    generate = main_module.narration_generator.generate_for_creation_task
    def cancel(**kwargs):
        result = generate(**kwargs)
        state.executor.request_cancel(kwargs["grant_context"].grant.request_id)
        return result
    monkeypatch.setattr(main_module.narration_generator, "generate_for_creation_task", cancel)
    response = state.client.post(state.confirm_url, json={})
    state.resources.allowed = True
    assert state.runtime.run_once()
    assert state.repo.get_job_run("local", response.json()["job_id"]).status == "cancelled"
    assert state.repo.get_creation_task("local", state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.get_narration_artifact("local", "1" * 64) is None


def test_actual_callback_unknown_release_does_not_publish(generator_api):
    from app.device_scheduler import RecoveryBlocked
    state = generator_api
    response = state.client.post(state.confirm_url, json={})
    state.resources.allowed = True
    state.resources.release = None
    try:
        with pytest.raises(RecoveryBlocked):
            state.runtime.run_once()
        assert state.repo.get_creation_task("local", state.task.id).current_timeline_version_id == state.base.id
        assert state.repo.get_narration_artifact("local", "1" * 64) is None
        assert state.repo.get_narration_artifact("local", "2" * 64) is None
        with state.repo._connect() as connection:
            row = connection.execute("SELECT state,request_id FROM device_permit WHERE id=1").fetchone()
            assert row["state"] != "free" and row["request_id"] == response.json()["request_id"]
    finally:
        state.resources.release = True
        state.resources.allowed = False
