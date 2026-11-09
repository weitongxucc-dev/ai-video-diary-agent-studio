"""Controlled creation_job narration edit; no model, service or media subprocess."""
from script_seed_fixtures import script_seed_payload

import io
from contextlib import contextmanager
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
from app.creation.narration_artifacts import NarrationArtifactRecord
from app.production import MaterialAsset, MaterialSegment, ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import (
    CreationPlanVoiceReference,
    CreationTask,
    EditIntent,
    EditProposal,
    JobRun,
    NarrationSegment,
    TimelineVersion,
)


def run_edit_to_boundary(state):
    """Advance the real finite generation/PCM units, with each unit's own grant."""
    progressed = False
    # One generation,one append per original input,and one final PCM header.
    for _ in range(len(state.segments) + 2):
        if not state.runtime.run_once():
            return progressed
        progressed = True
    return progressed


def assert_original_pcm_unknown(state, job_id):
    job = state.repo.job_records.get_job_run('local', job_id)
    assert job.status == 'interrupted' and job.failure_reason == 'pcm_result_unknown'
    assert isinstance(state.runtime.last_error, RecoveryBlocked)
    usage = state.repo.narration_units.creation_narration_usage('local', job_id)
    assert not usage['consumption_known'] and len(usage['unsettled']) == 1
    original = usage['unsettled'][0]
    request = state.runtime.scheduler._get(original['request_id'])
    assert request.owner_id == job_id and request.unit_key == original['unit_key']
    assert request.last_grant_generation == original['permit_generation']
    assert request.unit_key.startswith('narration_edit:pcm:')
    assert request.resources['execution_mode'] == 'cpu_pcm_wav_assembly'
    assert Path(request.resources['pcm_assembly']['receipt_path'] + '.manifest.json').is_file()
    assert request.state == 'blocked' and request.wait_reason == 'worker_interrupted'
    with state.repo._connect() as conn:
        assert tuple(conn.execute('SELECT state,request_id FROM device_permit WHERE id=1').fetchone()) == ('free', None)
        assert conn.execute('SELECT COUNT(*) FROM edit_operations WHERE workspace_id=? AND task_id=?',
            ('local', state.task.id)).fetchone()[0] == 0
    assert state.repo.edit_records.get_edit_proposal('local', state.proposal.id).status == 'proposed'
    return request


def wav_bytes(seconds=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\x01\x00" * round(24000 * seconds))
    return output.getvalue()


class Resources:
    allowed = False
    release = True

    def admit(self, request):
        assert request.resources["narration_phase"] in {"segment_edit", "merge"}
        assert request.resources["kind"] == ("creation_narration" if request.resources["narration_phase"] == "segment_edit" else "task_audio")
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


@contextmanager
def _narration_edit_state(tmp_path, monkeypatch, *, register_job=True):
    repo = ProductionRepository(
        database_path=tmp_path / "db.sqlite3", video_library_path=tmp_path / "library"
    )
    members = LocalMemberService(repo.database_path)
    member, member_token = members.join(members.create_invite_code(), "isolated narration member")
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
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=member.member_id)
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
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
            ("script", "local", task.id, "run", script_seed_payload(repo, conn, script), now),
        )
        conn.execute(
            "UPDATE creation_tasks SET payload=? WHERE id=?",
            (json.dumps(asdict(task), default=str), task.id),
        )
    audio = tmp_path / "original.wav"
    audio.write_bytes(wav_bytes())
    repo.narration_artifacts.save_narration_artifact(
        NarrationArtifactRecord(
            id="old",
            workspace_id="local",
            task_id=task.id,
            script_id="script",
            voice_profile_id=voice.id,
            cache_key="old",
            audio_path=str(audio),
            duration_seconds=2,
            sample_rate=24000,
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
            24000,
            voice.model_id,
            voice.id,
            voice.version,
            "zh-CN",
            "rules-v1",
            "ready",
        )
        for i in (1, 2)
    ]
    repo.narration_records.save_narration_segments("local", task.id, "script", segments)
    # Opaque video metadata is sufficient for source bounds and retiming. This
    # fixture never decodes video or invokes FFmpeg; real rendering is separate.
    source = repo.video_library_path / "opaque-video.mp4"
    source.write_bytes(b"metadata-only source; not a decodable video")
    asset = repo._save_asset(MaterialAsset(
        id="narration-edit-asset", workspace_id="local", file_name=source.name, path=str(source),
        size_bytes=source.stat().st_size, modified_at=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc),
        duration_seconds=24, usage_role="b_roll", review_status="approved", analysis_status="ready",
        owner_user_id=member.member_id, segments=[MaterialSegment(
            id=f"narration-edit-source-{i}", asset_id="narration-edit-asset", start_seconds=(i-1)*8,
            end_seconds=i*8, description="opaque retiming source", review_status="approved") for i in (1,2,3)]))
    source_reference = repo.workspace_media_reference_for_asset("local", asset.id)
    from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
    from app.creation.contracts import SubtitleTrack
    montage = creation_task_montage_plan_to_payload(CreationTaskMontagePlan(
        "montage", "local", task.id, "script", "合成配音草稿", "ready",
        [NarrationCue(f"cue-{i}", f"section-{i}", f"第{i}段", 2, f"n-{i}") for i in (1,2)],
        [MontageClip(f"clip-{i}", f"cue-{i}", asset.id, asset.segments[i-1].id, source_reference,
            (i-1)*8, (i-1)*8+2, (i-1)*2, i*2, 1, ["opaque retiming source"], False) for i in (1,2)],
        4, 0, narration_audio_path="narration://artifact/old"))
    repo.montage_plan_adoption.save_creation_task_montage_plan("local", task.id, "montage", montage)
    base = repo.timeline_records.create_timeline_version(
        TimelineVersion("base", "local", task.id, None, 1, {"montage": montage}, None)
    )
    original_track = SubtitleTrack("original-translated", "local", task.id, base.id, "en-US", "zh-CN",
        {}, [], "translation", None, None, None, "unavailable", "untranslated", "无英文输出", "选择模型后翻译")
    repo.subtitle_records.create_subtitle_track(original_track)
    intent = repo.edit_records.create_edit_intent(
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
    proposal = repo.edit_records.create_edit_proposal(
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
    task = repo.task_records.get_creation_task("local", task.id)
    plan = repo.plan_records.get_creation_plan("local", plan.id)
    budget = NarrationBudget(
        1000, 1, 1000000, 10, 1000000, 1, 60, 1000000, 2, 0.1, 1000000, 1000000,
        input_scope="single_call_experiment_v1"
    )
    from app.host_resource_probes import CpuAudioBudget
    audio_budget = CpuAudioBudget(2, 32*1024*1024, 10, 10, 24000, 1, 1000000, 1, 60,
        1000000, 2, .1, 1000000, 1000000)
    owner = CreationJobOwnerVerifier(
        repo,
        narration_audio_budget=audio_budget,
        narration_budget=budget,
        verify_identity=MemberDeviceAccess(members).verify_identity,
    )
    from app.creation.task_authorization import TaskAuthorizationUseCase
    authorizations = TaskAuthorizationUseCase(repo, lambda identity: MemberDeviceAccess(members).verify_identity(
        CreationExecutionActor.from_event(identity['workspace_id'], identity)))
    authorizations.ensure(task, actor)
    owner.task_authorizations = authorizations
    digest = narration_edit_digest(proposal.operation)
    job = JobRun('job', 'local', task.id, NARRATION_EDIT_KIND, 'narration-edit:proposal', digest)
    import app.main as main
    from app.creation.execution_control import CreationExecutionControl
    control = CreationExecutionControl(repo, main._new_creation_execution(repo),
        main._render_stage_use_case(repo), MemberDeviceAccess(members).verify_identity)
    event = {
        'source': control._edit_source(task, proposal, actor, None),
        **actor.identity_payload(),
        "script_id": "script",
        "plan_id": plan.id,
        "revision_digest": revision_digest(
            repo.script_records.get_creation_task_script("local", "script"), plan
        ),
        "input_digest": digest,
        "edit_proposal_id": proposal.id,
    }
    from app.creation.narration_pcm import prepare_narration_source
    if register_job:
        with prepare_narration_source(repo, task, plan, actor) as prepared:
            job = repo.creation_execution_jobs.create_creation_execution_job(job,
                start_payload=event, event_type='narration_edit_requested', source_preparation=prepared)
    resources = Resources()
    access = MemberDeviceAccess(members)
    calls = []

    import copy
    import app.main as main
    from app.artifact_store import ArtifactStore
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation.narration_stage_use_case import NarrationStageUseCase
    from app.narration import NarrationArtifact
    generator = copy.copy(main.narration_generator)
    generator.output_root = tmp_path / "generated"
    def generated(**kwargs):
        context = kwargs['grant_context']
        context.check()
        calls.append(context.grant)
        output = tmp_path / "segment-generated.wav"
        output.write_bytes(wav_bytes(segments[0].duration_seconds))
        from app.narration import _pronunciation_dictionary, _pronunciation_version
        pronunciation = _pronunciation_version(_pronunciation_dictionary(entries=kwargs['pronunciation_entries'],
            workspace_id='local', person_profile_id=kwargs['voice_identity'].person_profile_id, task_id=task.id))
        return NarrationArtifact(task_id=task.id, script_id='script', audio_path=str(output),
            duration_seconds=segments[0].duration_seconds, sample_rate=24000, model_id='synthetic-provider',
            voice_profile_id=plan.selected_voice.profile_id, cache_key='1'*64, language='zh',
            pronunciation_version=pronunciation)
    generator.generate_for_creation_task = generated
    from app.task_audio import TaskAudio
    artifact_store = ArtifactStore(tmp_path / 'artifacts')
    audio_service = TaskAudio(repo, artifact_store, tmp_path / 'temporary')
    metadata = MetadataEditUseCase(repo, artifact_store, output_root=tmp_path / 'output', audio_service=audio_service)
    stage = NarrationStageUseCase(repo, generator, metadata, budget)
    committed = []
    persist = metadata.persist_montage_edit
    def observed_persist(**kwargs):
        operation = persist(**kwargs)
        committed.append(operation)
        return operation
    monkeypatch.setattr(metadata, 'persist_montage_edit', observed_persist)
    def pcm_worker(scheduler, grant, command, **options):
        # Run the existing PCM algorithm on the original granted manifest.
        from model_workers.pcm_assembly import execute
        resources = scheduler._get(grant.request_id).resources
        assert 0 < resources['max_output_bytes'] <= audio_budget.max_output_bytes
        assert [item['reference'].removeprefix('narration://artifact/') for item in resources['inputs']] == ['1'*64, 'old']
        assert repo.task_records.get_creation_task('local', task.id).current_timeline_version_id == data.base.id
        assert repo.narration_records.get_narration_segment('local', task.id, segments[0].id).audio_reference == segments[0].audio_reference
        manifest = json.loads(Path(command[-1]).read_text())
        receipt = execute(manifest, {'generated_bytes': 0, 'work_created': False})
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt), stderr='')
    monkeypatch.setattr('app.granted_subprocess.run_granted_command', pcm_worker)

    from app.conversation_repository import ConversationRepository
    from app.creation.editing.exact_preview_use_case import validate_edit_origin
    conversations = ConversationRepository(repo.database_path)
    def verify_origin(source, actor, *, connection=None):
        validate_edit_origin(conversations, source, actor, connection=connection)
    owner.source_verifier = verify_origin
    executor = CreationJobUnitExecutor(
        repo,
        render_action=lambda *_: pytest.fail("not render"),
        render_is_valid=lambda *_: False,
        narration_stage=stage,
        source_verifier=verify_origin,
    )
    runtime = build_device_runtime(
        database_path=repo.database_path,
        device_lock_path=tmp_path / "device.lock",
        device_access=access,
        registrations={"creation_job": OwnerRegistration(owner, executor)},
        resources=resources,
        processes=Processes(),
    )
    runtime.task_authorizations = authorizations
    data = SimpleNamespace(
        repo=repo,
        actor=actor,
        member_token=member_token,
        members=members,
        task=task,
        plan=plan,
        base=base,
        proposal=proposal,
        job=job,
        segments=segments,
        montage=montage,
        asset=asset,
        original_track=original_track,
        budget=budget,
        audio_budget=audio_budget,
        stage=stage,
        owner=owner,
        resources=resources,
        executor=executor,
        runtime=runtime,
        calls=calls,
        committed=committed,
    )
    try:
        yield data
    finally:
        assert runtime.stop(1)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    with _narration_edit_state(tmp_path, monkeypatch) as state:
        yield state


def submit(f):
    key, _ = f.owner.narration_edit_facts(
        f.job, f.task, f.repo.execution_records.get_task_event("local", "event_job_start").payload, f.plan
    )
    return f.runtime.scheduler.submit(
        SubmissionIntent(
            "local", "creation_job", f.job.id, f.job.input_digest, key, SchedulePolicy()
        ),
        f.actor,
    )


def unchanged(f):
    assert (
        f.repo.task_records.get_creation_task("local", f.task.id).current_timeline_version_id
        == f.base.id
    )
    assert f.repo.narration_records.list_narration_segments("local", f.task.id, "script") == f.segments
    assert f.repo.edit_records.get_edit_proposal("local", f.proposal.id).status == "proposed"


def test_waiting_duplicate_grant_and_atomic_local_success(fixture):
    f = fixture
    request = submit(f)
    assert submit(f).id == request.id and request.work_class == "preview_dependency"
    assert not run_edit_to_boundary(f) and not f.calls
    unchanged(f)
    f.resources.allowed = True
    assert run_edit_to_boundary(f)
    assert len(f.calls) == 1 and f.calls[0].request_id == request.id
    assert (
        f.repo.task_records.get_creation_task("local", f.task.id).current_timeline_version_id
        == f.committed[0].result_version_id
    )
    assert len(f.committed) == 1 and f.committed[0].result_version_id != f.base.id
    assert f.repo.job_records.get_job_run("local", f.job.id).status == "succeeded"
    assert (
        f.repo.narration_records.list_narration_segments("local", f.task.id, "script")[1] == f.segments[1]
    )
    changed = f.repo.get_creation_task_montage_plan("local", f.task.id)
    assert changed["clips"] == f.montage["clips"]
    tracks = f.repo.subtitle_records.list_subtitle_tracks("local", f.task.id,
        timeline_version_id=f.committed[0].result_version_id)
    assert len(tracks) == 1
    assert (tracks[0].status, tracks[0].failure_reason, tracks[0].impact, tracks[0].repair_action) == (
        f.original_track.status, f.original_track.failure_reason, f.original_track.impact, f.original_track.repair_action)
    succeeded = f.repo.execution_records.get_task_event("local", "event_job_narration_edit_succeeded")
    final_request = f.runtime.scheduler._get(succeeded.payload["request_id"])
    assert final_request.owner_id == f.job.id and final_request.state == "done"
    assert final_request.unit_key.startswith("narration_edit:pcm:")
    assert f.runtime.scheduler._get(request.id).state == "done"
    assert not run_edit_to_boundary(f)
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
        task = f.repo.task_records.get_creation_task("local", f.task.id)
        task.current_timeline_version_id = "different"
        with f.repo._connect() as conn:
            conn.execute(
                "UPDATE creation_tasks SET payload=? WHERE id=?",
                (json.dumps(asdict(task), default=str), task.id),
            )
    elif mutation == "audio":
        f.repo.narration_records.save_narration_segments(
            "local",
            f.task.id,
            "script",
            [
                replace(f.segments[0], audio_reference="narration://artifact/other"),
                f.segments[1],
            ],
        )
    elif mutation == "script":
        script = f.repo.script_records.get_creation_task_script("local", "script")
        script.sections[0] = replace(script.sections[0], content="changed")
        with f.repo._connect() as conn:
            conn.execute('BEGIN')
            payload = f.repo.script_records._script_payload(conn, script)
            conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id='script'", (payload,))
    elif mutation == "voice":
        voice = f.repo.profile_records.get_versioned_voice_profile("local", "voice")
        f.repo.save_versioned_voice_profile(
            replace(voice, version="v2", use_state="excluded"),
            verified_owner_user_id=f.actor.member_id,
        )
    elif mutation == "reference":
        voice = f.repo.profile_records.get_versioned_voice_profile("local", "voice")
        asset = f.repo.get_asset("local", voice.reference.asset_id)
        Path(asset.path).write_bytes(wav_bytes() + b"changed")
    elif mutation == "artifact_scope":
        artifact = f.repo.narration_artifacts.get_narration_artifact("local", "old")
        f.repo.narration_artifacts.save_narration_artifact(replace(artifact, task_id="other-task"))
    elif mutation == "member":
        f.members.remove_member(f.actor.member_id)
    else:
        with f.repo._connect() as conn:
            conn.execute(
                "UPDATE device_work_requests SET work_class='normal_make' WHERE id=?",
                (request.id,),
            )
    f.resources.allowed = True
    assert not run_edit_to_boundary(f) and not f.calls
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

    if kind in {"provider_failure", "timeout", "cancel"}:
        def generate(**kwargs):
            context = kwargs['grant_context']
            if kind == "provider_failure":
                raise ValueError("synthetic provider failure")
            if kind == "timeout":
                replace(context, deadline_monotonic=0).check()
            f.runtime.scheduler.cancel(context.grant.request_id, f.actor)
            f.repo.job_records.finish_job_run('local', f.job.id, status='cancelled',
                failure_reason='user_cancelled', impact='原片段和版本保持', repair_action='重新预览')
            f.executor.request_cancel(context.grant.request_id)
            context.check()
        f.stage.generator.generate_for_creation_task = generate
    else:
        original = f.stage.prepare_edit_adoption
        def adoption(ws, task_id, proposal_id, candidate, resources, receipt):
            commit = original(ws, task_id, proposal_id, candidate, resources, receipt)
            if kind == "stale_after_generation" and receipt['finalized']:
                f.repo.narration_records.save_narration_segments('local', f.task.id, 'script',
                    [replace(f.segments[0], pronunciation_version='late-change'), f.segments[1]])
            if kind == "bad_checkpoint" and commit is not None:
                def bad(conn):
                    operation = commit(conn)
                    return replace(operation, result_version_id='not-saved')
                return bad
            return commit
        f.stage.prepare_edit_adoption = adoption
    f.resources.allowed = True
    before_montage = f.repo.get_creation_task_montage_plan('local', f.task.id)
    original_start = f.repo.execution_records.get_task_event('local', f'event_{f.job.id}_start')
    pcm_unknown = kind in {'bad_checkpoint', 'stale_after_generation'}
    if pcm_unknown:
        with pytest.raises(RecoveryBlocked, match='结果或消耗未知'):
            run_edit_to_boundary(f)
        assert_original_pcm_unknown(f, f.job.id)
    else:
        assert run_edit_to_boundary(f)
    assert f.repo.get_creation_task_montage_plan('local', f.task.id) == before_montage
    assert f.repo.execution_records.get_task_event('local', f'event_{f.job.id}_start') == original_start
    assert (
        f.repo.task_records.get_creation_task("local", f.task.id).current_timeline_version_id
        == f.base.id
    )
    assert f.repo.edit_records.get_edit_proposal("local", f.proposal.id).status == "proposed"
    assert f.repo.job_records.get_job_run("local", f.job.id).status == (
        "interrupted" if pcm_unknown else "cancelled" if kind == "cancel" else "failed"
    )
    if kind != "stale_after_generation":
        assert (
            f.repo.narration_records.list_narration_segments("local", f.task.id, "script") == f.segments
        )


def test_unknown_release_retains_original_permit_and_unpublished_version(fixture):
    f = fixture
    request = submit(f)
    f.resources.allowed = True
    f.resources.release = None
    with pytest.raises(RecoveryBlocked):
        run_edit_to_boundary(f)
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
        recovered.scheduler.reconcile_released()
        assert not recovered.run_once()
        unchanged(f)
        assert len(f.calls) == 1
        with f.repo._connect() as conn:
            assert conn.execute(
                "SELECT state,request_id FROM device_permit WHERE id=1"
            ).fetchone()[:] == ("free", None)
        usage = f.repo.narration_units.creation_narration_usage('local', f.job.id)
        assert not usage['consumption_known'] and len(usage['unsettled']) == 1
        assert usage['unsettled'][0]['request_id'] == request.id
    finally:
        assert recovered.stop(1)


def test_unknown_admission_and_wrong_scope_are_not_permits(fixture):
    f = fixture
    f.resources.allowed = None
    submit(f)
    assert not run_edit_to_boundary(f) and not f.calls
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
    record = state.repo.narration_artifacts.get_narration_artifact("local", "old")
    state.repo.narration_artifacts.save_narration_artifact(replace(record, id="other-source", cache_key="other-source",
        task_id="other-task" if scope == "other_task" else state.task.id,
        script_id="old-script" if scope == "old_script" else "script"))
    state.segments = [state.segments[0], replace(state.segments[1], audio_reference="narration://artifact/other-source")]
    state.repo.narration_records.save_narration_segments("local", state.task.id, "script", state.segments)
    with pytest.raises(ValueError, match="合并配音产物不属于当前任务及文案版本"):
        submit(state)
    assert not state.calls
    unchanged(state)
    with state.repo._connect() as connection:
        assert connection.execute("SELECT count(*) FROM device_work_requests").fetchone()[0] == 0


def test_combination_artifact_metadata_change_while_waiting_blocks_dispatch(fixture):
    state = fixture
    record = state.repo.narration_artifacts.get_narration_artifact("local", "old")
    other = replace(record, id="other-source", cache_key="other-source")
    state.repo.narration_artifacts.save_narration_artifact(other)
    state.segments = [state.segments[0], replace(state.segments[1], audio_reference="narration://artifact/other-source")]
    state.repo.narration_records.save_narration_segments("local", state.task.id, "script", state.segments)
    request = submit(state)
    state.repo.narration_artifacts.save_narration_artifact(replace(other, sample_rate=16000))
    state.resources.allowed = True
    assert not state.runtime.run_once()
    assert not state.calls
    unchanged(state)
    assert state.runtime.scheduler._get(request.id).state == "blocked"
