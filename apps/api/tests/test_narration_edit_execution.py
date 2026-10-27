"""Controlled creation_job narration edit; no model, service or media subprocess."""

import io
import json
import wave
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.avatar_lipsync import ProfileReference, VoiceProfile
from app.creation_execution import (
    NARRATION_EDIT_KIND,
    CreationExecutionActor,
    CreationJobOwnerVerifier,
    CreationJobUnitExecutor,
    narration_edit_digest,
    narration_edit_stage_facts,
    revision_digest,
)
from app.device_runtime import OwnerRegistration, build_device_runtime
from app.device_scheduler import (
    ProcessIdentity,
    RecoveryBlocked,
    SchedulePolicy,
    SubmissionIntent,
)
from app.host_resource_probes import NarrationBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess
from app.production import CreationPlan, NarrationArtifactRecord, ProductionRepository
from app.creation.contracts import (
    CreationPlanVoiceReference,
    CreationTask,
    EditIntent,
    EditOperation,
    EditProposal,
    JobRun,
    NarrationSegment,
    TaskEvent,
    TimelineVersion,
)


def wav_bytes(seconds=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x01\x00" * round(8000 * seconds))
    return output.getvalue()


class Resources:
    allowed = False
    release = True

    def admit(self, request):
        assert request.resources["kind"] == "creation_narration"
        from app.host_resource_probes import HostResourceDecision
        return HostResourceDecision(self.allowed, dict(request.resources), {})

    def continue_unit(self, grant):
        return True

    def released(self, grant):
        return self.release


class Processes:
    recovery = "released"

    def current(self):
        return ProcessIdentity(100, "controlled-test-start", "controlled-test-boot")

    def alive(self, identity):
        return False

    def group_alive(self, group):
        return False

    def children_released(self, children):
        return True

    def recovery_state(self, holder, children):
        return self.recovery


@pytest.fixture
def fixture(tmp_path):
    repo = ProductionRepository(
        database_path=tmp_path / "db.sqlite3", video_library_path=tmp_path / "library"
    )
    members = LocalMemberService(repo.database_path)
    member = members.add_member("isolated narration member")
    actor = CreationExecutionActor("local", member.member_id)
    reference, _, _ = repo.import_voice_reference_wav(
        "local", wav_bytes(), verified_owner_user_id=member.member_id
    )
    voice = repo.save_versioned_voice_profile(
        VoiceProfile(
            "voice",
            "local",
            "v1",
            "zh",
            reference,
            "合成参考",
            display_name="合成声音",
            model_id="synthetic-provider",
            reference=ProfileReference(),
        ),
        verified_owner_user_id=member.member_id,
    )
    plan = CreationPlan(
        "plan",
        "local",
        "合成目标",
        "合成测试",
        1,
        "zh-CN",
        "16:9",
        4,
        [],
        [],
        CreationPlanVoiceReference(
            voice.id,
            voice.version,
            "",
            "",
            "",
            "",
            "",
            "",
            language="zh-CN",
            model_id=voice.model_id,
        ),
        [],
        0,
        0,
        "合成",
        [],
        [],
        [],
        "fixture",
        presenter_share_target=0,
    )
    task = CreationTask(
        "task",
        "local",
        plan.id,
        "合成测试",
        "knowledge_explanation",
        owner_user_id=member.member_id,
    )
    repo.confirm_creation_plan(plan, task)
    now = datetime.now(timezone.utc).isoformat()
    script = {
        "id": "script",
        "workspace_id": "local",
        "task_id": task.id,
        "run_id": "run",
        "topic_id": "topic",
        "title": "合成文案",
        "language": "zh-CN",
        "selection_status": "confirmed",
        "confirmed_at": now,
        "sections": [
            {
                "id": f"section-{i}",
                "title": "正文",
                "content": f"第{i}段",
                "visual_prompt": "合成",
                "evidence_reference_ids": [],
            }
            for i in (1, 2)
        ],
        "created_at": now,
        "updated_at": now,
    }
    task.confirmed_script_id = "script"
    task.stage = "montage"
    with repo._connect() as conn:
        conn.execute(
            "INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
            ("script", "local", task.id, "run", json.dumps(script), now),
        )
        conn.execute(
            "UPDATE creation_tasks SET payload=? WHERE id=?",
            (json.dumps(asdict(task), default=str), task.id),
        )
    audio = tmp_path / "original.wav"
    audio.write_bytes(wav_bytes())
    repo.save_narration_artifact(
        NarrationArtifactRecord(
            id="old",
            workspace_id="local",
            task_id=task.id,
            script_id="script",
            voice_profile_id=voice.id,
            cache_key="old",
            audio_path=str(audio),
            duration_seconds=4,
            sample_rate=8000,
            model_id="synthetic-provider",
        )
    )
    segments = [
        NarrationSegment(
            f"n-{i}",
            "local",
            task.id,
            "script",
            f"section-{i}",
            f"第{i}段",
            "narration://artifact/old",
            2,
            8000,
            voice.model_id,
            voice.id,
            voice.version,
            "zh-CN",
            "rules-v1",
            "ready",
        )
        for i in (1, 2)
    ]
    repo.save_narration_segments("local", task.id, "script", segments)
    montage = {
        "id": "montage",
        "workspace_id": "local",
        "task_id": task.id,
        "script_id": "script",
        "status": "ready",
        "duration_seconds": 4,
        "cues": [
            {
                "id": f"cue-{i}",
                "narration_segment_id": f"n-{i}",
                "text": f"第{i}段",
                "duration_seconds": 2,
            }
            for i in (1, 2)
        ],
        "clips": [
            {
                "id": f"clip-{i}",
                "cue_id": f"cue-{i}",
                "timeline_start_seconds": (i - 1) * 2,
                "timeline_end_seconds": i * 2,
            }
            for i in (1, 2)
        ],
        "subtitle_tracks": [
            {
                "id": "original-translated",
                "status": "unavailable",
                "reason": "untranslated",
            }
        ],
    }
    repo.save_creation_task_montage_plan("local", task.id, "montage", montage)
    base = repo.create_timeline_version(
        TimelineVersion("base", "local", task.id, None, 1, {"montage": montage}, None)
    )
    intent = repo.create_edit_intent(
        EditIntent(
            "intent",
            "local",
            task.id,
            "重配第一段",
            "narration",
            ["narration:n-1"],
            None,
            "regenerate_narration_segment",
            {},
            {},
            1,
            "interpreted",
        )
    )
    proposal = repo.create_edit_proposal(
        EditProposal(
            "proposal",
            "local",
            task.id,
            intent.id,
            "重配第一段",
            ["narration:n-1"],
            {
                "operation_type": "RegenerateNarrationSegment",
                "payload": {
                    "narration_segment_id": "n-1",
                    "expected_audio_reference": segments[0].audio_reference,
                    "delivery_instruction": "重配第一段",
                    "base_version_id": base.id,
                },
            },
            [],
            {},
            [],
        )
    )
    task = repo.get_creation_task("local", task.id)
    plan = repo.get_creation_plan("local", plan.id)
    budget = NarrationBudget(
        1000, 1, 1000000, 10, 1000000, 1, 60, 1000000, 2, 0.1, 1000000, 1000000
    )
    owner = CreationJobOwnerVerifier(
        repo,
        narration_budget=budget,
        verify_identity=MemberDeviceAccess(members).verify_identity,
    )
    digest = narration_edit_digest(proposal.operation)
    job = repo.create_job_run(
        JobRun(
            "job",
            "local",
            task.id,
            NARRATION_EDIT_KIND,
            "narration-edit:proposal",
            digest,
        )
    )
    event = {
        **actor.identity_payload(),
        "script_id": "script",
        "plan_id": plan.id,
        "revision_digest": revision_digest(
            repo.get_creation_task_script("local", "script"), plan
        ),
        "input_digest": digest,
        "edit_proposal_id": proposal.id,
    }
    repo.append_task_event(
        TaskEvent(
            "event_job_start",
            "local",
            task.id,
            job.id,
            "narration_edit_requested",
            event,
        )
    )
    resources = Resources()
    access = MemberDeviceAccess(members)
    calls = []

    def action(workspace, task_id, context, proposal_id):
        context.check()
        calls.append(context.grant)
        changed = replace(segments[0], audio_reference="narration://artifact/new")
        updated = json.loads(json.dumps(montage))
        updated["cues"][0]["audio_reference"] = changed.audio_reference
        applied_at = datetime.now(timezone.utc)
        operation = EditOperation(
            "operation",
            "local",
            task.id,
            proposal.id,
            "RegenerateNarrationSegment",
            proposal.operation["payload"],
            base.id,
            "result",
            "applied",
            created_at=applied_at,
            applied_at=applied_at,
        )
        version = TimelineVersion(
            "result", "local", task.id, base.id, 2, {"montage": updated}, operation.id
        )

        def commit(conn):
            repo.confirm_edit_proposal(
                "local",
                task.id,
                proposal.id,
                operation=operation,
                result_version=version,
                montage_plan_id="montage",
                montage_payload=updated,
                narration_segments=[changed, segments[1]],
                connection=conn,
            )
            return operation

        return commit

    executor = CreationJobUnitExecutor(
        repo,
        render_action=lambda *_: pytest.fail("not render"),
        render_is_valid=lambda *_: False,
        stage_actions={"narration_edit": action},
    )
    runtime = build_device_runtime(
        database_path=repo.database_path,
        device_lock_path=tmp_path / "device.lock",
        device_access=access,
        registrations={"creation_job": OwnerRegistration(owner, executor)},
        resources=resources,
        processes=Processes(),
    )
    data = SimpleNamespace(
        repo=repo,
        actor=actor,
        members=members,
        task=task,
        plan=plan,
        base=base,
        proposal=proposal,
        job=job,
        segments=segments,
        montage=montage,
        budget=budget,
        owner=owner,
        resources=resources,
        executor=executor,
        runtime=runtime,
        calls=calls,
        action=action,
    )
    yield data
    assert runtime.stop(1)


def submit(f):
    key, _ = f.owner.narration_edit_facts(
        f.job, f.task, f.repo.get_task_event("local", "event_job_start").payload, f.plan
    )
    return f.runtime.scheduler.submit(
        SubmissionIntent(
            "local", "creation_job", f.job.id, f.job.input_digest, key, SchedulePolicy()
        ),
        f.actor,
    )


def unchanged(f):
    assert (
        f.repo.get_creation_task("local", f.task.id).current_timeline_version_id
        == f.base.id
    )
    assert f.repo.list_narration_segments("local", f.task.id, "script") == f.segments
    assert f.repo.get_edit_proposal("local", f.proposal.id).status == "proposed"


def test_waiting_duplicate_grant_and_atomic_local_success(fixture):
    f = fixture
    request = submit(f)
    assert submit(f).id == request.id and request.work_class == "preview_dependency"
    assert not f.runtime.run_once() and not f.calls
    unchanged(f)
    f.resources.allowed = True
    assert f.runtime.run_once()
    assert len(f.calls) == 1 and f.calls[0].request_id == request.id
    assert (
        f.repo.get_creation_task("local", f.task.id).current_timeline_version_id
        == "result"
    )
    assert f.repo.get_job_run("local", f.job.id).status == "succeeded"
    assert (
        f.repo.list_narration_segments("local", f.task.id, "script")[1] == f.segments[1]
    )
    changed = f.repo.get_creation_task_montage_plan("local", f.task.id)
    assert (
        changed["clips"] == f.montage["clips"]
        and changed["subtitle_tracks"] == f.montage["subtitle_tracks"]
    )
    assert (
        f.repo.get_task_event("local", "event_job_narration_edit_succeeded").payload[
            "request_id"
        ]
        == request.id
    )
    assert not f.runtime.run_once()
    with pytest.raises((PermissionError, ValueError)):
        submit(f)


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "audio",
        "script",
        "voice",
        "reference",
        "member",
        "scope",
        "artifact_scope",
    ],
)
def test_waiting_stale_or_unauthorized_never_calls_provider(fixture, mutation):
    f = fixture
    request = submit(f)
    if mutation == "version":
        task = f.repo.get_creation_task("local", f.task.id)
        task.current_timeline_version_id = "different"
        with f.repo._connect() as conn:
            conn.execute(
                "UPDATE creation_tasks SET payload=? WHERE id=?",
                (json.dumps(asdict(task), default=str), task.id),
            )
    elif mutation == "audio":
        f.repo.save_narration_segments(
            "local",
            f.task.id,
            "script",
            [
                replace(f.segments[0], audio_reference="narration://artifact/other"),
                f.segments[1],
            ],
        )
    elif mutation == "script":
        with f.repo._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE id='script'"
            ).fetchone()
            payload = json.loads(row[0])
            payload["sections"][0]["content"] = "changed"
            conn.execute(
                "UPDATE creation_task_scripts SET payload=? WHERE id='script'",
                (json.dumps(payload),),
            )
    elif mutation == "voice":
        voice = f.repo.get_versioned_voice_profile("local", "voice")
        f.repo.save_versioned_voice_profile(
            replace(voice, version="v2", use_state="excluded"),
            verified_owner_user_id=f.actor.member_id,
        )
    elif mutation == "reference":
        voice = f.repo.get_versioned_voice_profile("local", "voice")
        asset = f.repo.get_asset("local", voice.reference.asset_id)
        Path(asset.path).write_bytes(wav_bytes() + b"changed")
    elif mutation == "artifact_scope":
        artifact = f.repo.get_narration_artifact("local", "old")
        f.repo.save_narration_artifact(replace(artifact, task_id="other-task"))
    elif mutation == "member":
        f.members.remove_member(f.actor.member_id)
    else:
        with f.repo._connect() as conn:
            conn.execute(
                "UPDATE device_work_requests SET work_class='normal_make' WHERE id=?",
                (request.id,),
            )
    f.resources.allowed = True
    assert not f.runtime.run_once() and not f.calls
    with f.repo._connect() as conn:
        assert (
            conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0]
            == "free"
        )


@pytest.mark.parametrize(
    "kind",
    [
        "provider_failure",
        "cancel",
        "timeout",
        "stale_after_generation",
        "bad_checkpoint",
    ],
)
def test_no_partial_commit_on_failure_cancel_or_stale(fixture, kind):
    f = fixture
    submit(f)

    def action(ws, task_id, context, proposal_id):
        if kind == "provider_failure":
            raise ValueError("synthetic provider failure")
        if kind == "timeout":
            replace(context, deadline_monotonic=0).check()
        if kind == "cancel":
            f.executor.request_cancel(context.grant.request_id)
            context.check()
        commit = f.action(ws, task_id, context, proposal_id)
        if kind == "stale_after_generation":
            f.repo.save_narration_segments(
                "local",
                f.task.id,
                "script",
                [
                    replace(f.segments[0], pronunciation_version="late-change"),
                    f.segments[1],
                ],
            )
        if kind == "bad_checkpoint":

            def bad(conn):
                operation = commit(conn)
                return replace(operation, result_version_id="not-saved")

            return bad
        return commit

    f.executor.stage_actions["narration_edit"] = action
    f.resources.allowed = True
    assert f.runtime.run_once()
    assert (
        f.repo.get_creation_task("local", f.task.id).current_timeline_version_id
        == f.base.id
    )
    assert f.repo.get_edit_proposal("local", f.proposal.id).status == "proposed"
    assert f.repo.get_job_run("local", f.job.id).status == (
        "cancelled" if kind == "cancel" else "failed"
    )
    if kind != "stale_after_generation":
        assert (
            f.repo.list_narration_segments("local", f.task.id, "script") == f.segments
        )


def test_unknown_release_retains_original_permit_and_unpublished_version(fixture):
    f = fixture
    request = submit(f)
    f.resources.allowed = True
    f.resources.release = None
    with pytest.raises(RecoveryBlocked):
        f.runtime.run_once()
    unchanged(f)
    with f.repo._connect() as conn:
        permit = conn.execute(
            "SELECT state, request_id FROM device_permit WHERE id=1"
        ).fetchone()
        assert permit["state"] != "free" and permit["request_id"] == request.id
    f.resources.release = True
    f.resources.allowed = False
    assert f.runtime.stop(1)
    processes = Processes()
    processes.recovery = "unknown"
    recovered = build_device_runtime(
        database_path=f.repo.database_path,
        device_lock_path=f.runtime.scheduler.lock_path,
        device_access=MemberDeviceAccess(f.members),
        registrations=f.runtime.registrations,
        resources=f.resources,
        processes=processes,
    )
    try:
        with pytest.raises(RecoveryBlocked):
            recovered.run_once()
        unchanged(f)
        assert len(f.calls) == 1
        processes.recovery = "released"
        assert not recovered.run_once()
        unchanged(f)
        assert len(f.calls) == 1
        with f.repo._connect() as conn:
            assert conn.execute(
                "SELECT state,request_id FROM device_permit WHERE id=1"
            ).fetchone()[:] == ("free", None)
    finally:
        assert recovered.stop(1)


def test_unknown_admission_and_wrong_scope_are_not_permits(fixture):
    f = fixture
    f.resources.allowed = None
    submit(f)
    assert not f.runtime.run_once() and not f.calls
    unchanged(f)
    with pytest.raises(PermissionError):
        f.runtime.scheduler.submit(
            SubmissionIntent(
                "other",
                "creation_job",
                f.job.id,
                f.job.input_digest,
                "narration_edit:any",
                SchedulePolicy(),
            ),
            f.actor,
        )
    with pytest.raises(ValueError, match="预算"):
        narration_edit_stage_facts(f.repo, f.task, f.proposal, f.plan, None)


def test_wrong_grant_cannot_enter_narration_callback(fixture):
    f = fixture
    submit(f)
    f.resources.allowed = True
    grant = f.runtime.scheduler.pick_and_grant()
    with pytest.raises(PermissionError):
        f.executor.execute(
            replace(grant, unit_key="person_edit:wrong"), f.runtime.scheduler
        )
    assert not f.calls
    unchanged(f)
    f.runtime.scheduler.complete_unit(
        grant,
        checkpoint_database_path=f.repo.database_path,
        checkpoint_write=lambda *_: "fixture-cleanup",
        cancelled=True,
    )


@pytest.mark.parametrize("scope", ["other_task", "old_script"])
def test_combination_rejects_other_segment_artifact_scope_before_dispatch(fixture, scope):
    state = fixture
    record = state.repo.get_narration_artifact("local", "old")
    state.repo.save_narration_artifact(replace(record, id="other-source", cache_key="other-source",
        task_id="other-task" if scope == "other_task" else state.task.id,
        script_id="old-script" if scope == "old_script" else "script"))
    state.segments = [state.segments[0], replace(state.segments[1], audio_reference="narration://artifact/other-source")]
    state.repo.save_narration_segments("local", state.task.id, "script", state.segments)
    with pytest.raises(ValueError, match="合并配音产物不属于当前任务及文案版本"):
        submit(state)
    assert not state.calls
    unchanged(state)
    with state.repo._connect() as connection:
        assert connection.execute("SELECT count(*) FROM device_work_requests").fetchone()[0] == 0


def test_combination_artifact_metadata_change_while_waiting_blocks_dispatch(fixture):
    state = fixture
    record = state.repo.get_narration_artifact("local", "old")
    other = replace(record, id="other-source", cache_key="other-source")
    state.repo.save_narration_artifact(other)
    state.segments = [state.segments[0], replace(state.segments[1], audio_reference="narration://artifact/other-source")]
    state.repo.save_narration_segments("local", state.task.id, "script", state.segments)
    request = submit(state)
    state.repo.save_narration_artifact(replace(other, sample_rate=16000))
    state.resources.allowed = True
    assert not state.runtime.run_once()
    assert not state.calls
    unchanged(state)
    assert state.runtime.scheduler._get(request.id).state == "blocked"
