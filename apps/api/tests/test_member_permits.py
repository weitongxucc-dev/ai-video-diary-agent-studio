"""WIL-159：真实本机成员 + 真实整机许可 + 真实探针。

这一组只证明一件事——从「本机名册里的一行成员」到「整机队列上的一次心跳」，
中间没有假探针、没有字符串身份、没有第二条锁。没有标定预算时，这里要求它
明确报未标定，而不是悄悄放行。
"""

from __future__ import annotations
from script_seed_fixtures import script_seed_payload

import pytest

from app.creation_execution import CpuRenderLimits, CreationExecutionActor
from app.device_runtime import build_device_runtime
from app.device_scheduler import SessionHeartbeat
from app.host_resource_probes import HostProcessProbe, HostResourceProbe
from app.local_members import LocalMembersError, LocalMemberService
from app.member_permits import (
    HostBudgetUncalibrated,
    MemberDeviceAccess,
    cpu_render_budget_from_env,
    cpu_render_limits_from_budget,
    creation_stage_budgets_from_env,
    creation_job_registration,
    find_member,
    host_budget_from_env,
    permit_actor,
    resolve_member_token,
    PermitIdentityUnavailable,
)
from app.production import ProductionRepository

WORKSPACE = "local"


def test_absent_creation_stage_budgets_do_not_block_calibrated_cpu_config():
    config = {**_HOST_ENV, **_CPU_RENDER_ENV}
    cpu = cpu_render_budget_from_env(config)
    assert host_budget_from_env(config, cpu_render=cpu).cpu_render is cpu
    narration, montage = creation_stage_budgets_from_env(config)
    assert narration is None and montage is None
    from app.creation_execution import creation_stage_facts
    for stage in ("narration", "montage_plan"):
        with pytest.raises(ValueError, match="预算尚未标定"):
            creation_stage_facts(None, None, None, None, stage, narration, montage)


@pytest.mark.parametrize("field", ["DEVICE_NARRATION_MAX_TEXT_CHARS", "DEVICE_MONTAGE_MAX_SECTION_COUNT"])
def test_partial_creation_stage_budget_remains_an_explicit_error(field):
    with pytest.raises(HostBudgetUncalibrated, match="阶段资源预算尚未标定"):
        creation_stage_budgets_from_env({field: "256"})


def test_media_script_budget_requires_every_explicit_device_value():
    from app.host_resource_probes import MediaScriptBudget
    from app.member_permits import media_script_budget_from_env
    from dataclasses import fields
    assert media_script_budget_from_env({}) is None
    with pytest.raises(HostBudgetUncalibrated, match="隔离脚本预算尚未标定"):
        media_script_budget_from_env({"DEVICE_MEDIA_SCRIPT_MAX_INPUT_BYTES":"1024"})
    # Values are a parser fixture, never a calibrated default or executable permit.
    config = {"DEVICE_MEDIA_SCRIPT_"+item.name.upper(): "1" for item in fields(MediaScriptBudget)}
    config["DEVICE_MEDIA_SCRIPT_RUNTIME_SAMPLE_SECONDS"] = "0.1"
    result = media_script_budget_from_env(config)
    assert result.max_input_bytes == 1 and result.runtime_sample_seconds == .1
    assert host_budget_from_env(_HOST_ENV, media_script=result).media_script == result
    for name, value in [("MAX_TIMEOUT_SECONDS","nan"), ("MAX_OUTPUT_BYTES","0"),
                        ("MAX_INPUT_COUNT","bad"), ("RUNTIME_SAMPLE_SECONDS","0.3")]:
        with pytest.raises(HostBudgetUncalibrated):
            media_script_budget_from_env(config | {"DEVICE_MEDIA_SCRIPT_"+name:value})

_HOST_ENV = {
    "DEVICE_HOST_MAX_CPU_PERCENT": "60",
    "DEVICE_HOST_MIN_AVAILABLE_MEMORY_BYTES": "1073741824",
    "DEVICE_HOST_MAX_SWAP_USED_BYTES": "1073741824",
    "DEVICE_HOST_MIN_FREE_DISK_BYTES": "21474836480",
    "DEVICE_HOST_MAX_DISK_IO_BYTES_PER_SECOND": "20971520",
    "DEVICE_HOST_MAX_UNIT_INPUT_BYTES": "268435456",
}

_CPU_RENDER_ENV = {
    "DEVICE_CPU_RENDER_MAX_DURATION_SECONDS": "300",
    "DEVICE_CPU_RENDER_MAX_PIXEL_SECONDS": "200000000",
    "DEVICE_CPU_RENDER_MAX_WIDTH_PX": "1920",
    "DEVICE_CPU_RENDER_MAX_HEIGHT_PX": "1080",
    "DEVICE_CPU_RENDER_MAX_INPUT_BYTES": "268435456",
    "DEVICE_CPU_RENDER_MAX_CLIP_COUNT": "60",
    "DEVICE_CPU_RENDER_MAX_OVERLAY_COUNT": "40",
    "DEVICE_CPU_RENDER_MAX_OUTPUT_BYTES": "8000000",
    "DEVICE_CPU_RENDER_MAX_THREADS": "2",
    "DEVICE_CPU_RENDER_MAX_PROCESS_THREADS": "16",  # Synthetic test profile.
    "DEVICE_CPU_RENDER_MAX_TIMEOUT_SECONDS": "900",
    "DEVICE_CPU_RENDER_ADDITIONAL_MEMORY_BYTES": "1073741824",
    "DEVICE_CPU_RENDER_ADDITIONAL_DISK_BYTES": "10737418240",
}


def _repository(tmp_path):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    return ProductionRepository(
        database_path=tmp_path / "isolated.sqlite3", video_library_path=library,
    )


def test_account_access_uses_original_tuple_not_local_roster_and_rechecks(tmp_path):
    from uuid import uuid4
    from types import SimpleNamespace

    workspace, device, user = (str(uuid4()) for _ in range(3))
    actor = CreationExecutionActor.account(workspace, device, user)
    current = "ready"
    calls = []

    def lookup(*original):
        calls.append(original)
        return current

    members = LocalMemberService(_repository(tmp_path).database_path)
    access = MemberDeviceAccess(members, SimpleNamespace(creation_permit=lookup))
    heartbeat = SessionHeartbeat("account-session", workspace)
    assert access.verify_session(heartbeat, actor).member_id == actor.member_id
    assert access.can_view_device_summary(actor)
    assert all(item == (device, user, workspace) for item in calls)
    current = "unauthorized"
    with pytest.raises(PermissionError):
        access.verify_session(heartbeat, actor)
    assert not access.can_view_device_summary(actor)
    current = "unknown"
    with pytest.raises(PermitIdentityUnavailable):
        access.verify_session(heartbeat, actor)
    assert not access.can_view_device_summary(actor)
    local = members.whoami(None)[0]
    assert access.verify_identity(CreationExecutionActor("local", local.member_id)) == "unauthorized"
    assert not members.list_members()[0].member_id.startswith("account:")


def test_host_budget_requires_every_calibrated_value():
    with pytest.raises(HostBudgetUncalibrated, match="DEVICE_HOST_MAX_CPU_PERCENT"):
        host_budget_from_env({})
    partial = dict(_HOST_ENV)
    partial["DEVICE_HOST_MIN_FREE_DISK_BYTES"] = "  "
    with pytest.raises(HostBudgetUncalibrated, match="DEVICE_HOST_MIN_FREE_DISK_BYTES"):
        host_budget_from_env(partial)
    budget = host_budget_from_env(_HOST_ENV)
    assert budget.max_cpu_percent == 60
    assert budget.min_free_disk_bytes == 21474836480
    # 没有成片标定时，整机探针对成片单元必须诚实地说「测不了」，而不是放行。
    assert budget.cpu_render is None


def test_cpu_render_budget_is_all_or_nothing_and_feeds_the_verifier():
    assert cpu_render_limits_from_budget(None) is None
    partial = {k: v for k, v in _CPU_RENDER_ENV.items() if k != "DEVICE_CPU_RENDER_MAX_WIDTH_PX"}
    with pytest.raises(HostBudgetUncalibrated, match="DEVICE_CPU_RENDER_MAX_WIDTH_PX"):
        cpu_render_budget_from_env(partial)
    without_process_threads = {key:value for key,value in _CPU_RENDER_ENV.items() if key != "DEVICE_CPU_RENDER_MAX_PROCESS_THREADS"}
    with pytest.raises(HostBudgetUncalibrated, match="DEVICE_CPU_RENDER_MAX_PROCESS_THREADS"):
        cpu_render_budget_from_env(without_process_threads)
    budget = cpu_render_budget_from_env(_CPU_RENDER_ENV)
    assert budget.max_process_threads == 16
    assert cpu_render_limits_from_budget(budget) == CpuRenderLimits(8_000_000, 2, 900)


def test_member_identity_comes_from_the_roster_and_is_revoked_on_removal(tmp_path):
    repository = _repository(tmp_path)
    members = LocalMemberService(repository.database_path)
    owner, owner_token = members.whoami(None)
    joined, joined_token = members.join(members.create_invite_code(), "第二位")

    assert resolve_member_token(
        members=members, token=owner_token, auth_enabled=True,
    ).member_id == owner.member_id
    assert resolve_member_token(members=members, token="", auth_enabled=True) is None
    # 认证未开的本机单人态取名册里真实存在的行，不凭空造身份。
    assert resolve_member_token(
        members=members, token="", auth_enabled=False,
    ).member_id == owner.member_id

    access = MemberDeviceAccess(members)
    owner_actor = permit_actor(find_member(members, owner.member_id), WORKSPACE)
    trusted = access.verify_session(SessionHeartbeat("session-a", WORKSPACE), owner_actor)
    assert trusted.member_id == owner.member_id

    members.remove_member(joined.member_id)
    assert find_member(members, joined.member_id) is None
    with pytest.raises(PermissionError, match="已不存在"):
        access.verify_session(SessionHeartbeat("session-b", WORKSPACE), permit_actor(joined, WORKSPACE))
    assert access.can_view_device_summary(owner_actor) is True
    assert access.can_view_device_summary(None) is False
    # 身份必须和它绑定的工作区一致，不能跨空间复用。
    with pytest.raises(PermissionError, match="工作区"):
        access.verify_session(SessionHeartbeat("session-c", "workspace_b"), owner_actor)


def test_one_real_runtime_carries_creation_job_and_revokes_a_removed_member(tmp_path):
    repository = _repository(tmp_path)
    members = LocalMemberService(repository.database_path)
    owner, _owner_token = members.whoami(None)
    joined = resolve_member_token(
        members=members,
        token=members.join(members.create_invite_code(), "第二位")[1],
        auth_enabled=True,
    )

    runtime = build_device_runtime(
        database_path=repository.database_path,
        device_lock_path=tmp_path / "device.lock",
        device_access=MemberDeviceAccess(members),
        registrations={"creation_job": creation_job_registration(
            repository,
            render_action=lambda *_args: pytest.fail("没有获准单元就不该执行成片"),
            render_is_valid=lambda *_args: pytest.fail("没有获准单元就不该校验产物"),
            cpu_render_budget=cpu_render_budget_from_env(_CPU_RENDER_ENV),
        )},
        resources=HostResourceProbe(
            repository.database_path, disk_paths=(tmp_path,),
            budget=host_budget_from_env(_HOST_ENV),
        ),
        processes=HostProcessProbe(),
        activity_source_reliable=True,
        poll_interval_seconds=0.01,
    )
    # 一个注册项就够：不因为素材通道关闭就少注册交互式许可，也不另起第二条队列。
    assert set(runtime.registrations) == {"creation_job"}
    runtime.start()
    try:
        assert runtime.running
        runtime.scheduler.heartbeat_session(
            SessionHeartbeat("session-a", WORKSPACE, interaction=True),
            permit_actor(owner, WORKSPACE),
        )
        with repository._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, workspace_id, member_id, last_interaction_at_utc "
                "FROM device_session_activity",
            ).fetchall()
        assert [(row["session_id"], row["workspace_id"], row["member_id"]) for row in rows] == [
            ("session-a", WORKSPACE, owner.member_id),
        ]
        assert rows[0]["last_interaction_at_utc"] is not None
        # 同一个会话不能改挂到别人身上（auth §9）。
        with pytest.raises(PermissionError, match="绑定其他成员"):
            runtime.scheduler.heartbeat_session(
                SessionHeartbeat("session-a", WORKSPACE), permit_actor(joined, WORKSPACE),
            )

        members.remove_member(joined.member_id)
        with pytest.raises(PermissionError, match="已不存在"):
            runtime.scheduler.heartbeat_session(
                SessionHeartbeat("session-b", WORKSPACE), permit_actor(joined, WORKSPACE),
            )
    finally:
        assert runtime.stop(timeout_seconds=5.0)


@pytest.mark.parametrize("remove_at", ["before_grant", "after_grant"])
def test_real_roster_creation_grant_rechecks_removal_without_nested_writer(tmp_path, remove_at):
    """Actual identity/owner/scheduler SQL; only resource availability is a fixture."""
    import json
    from dataclasses import asdict
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.creation_execution import (
        CreationExecution, CreationJobOwnerVerifier, revision_digest, submit_creation_stage,
    )
    from app.device_scheduler import DeviceScheduler
    from app.host_resource_probes import NarrationBudget
    from app.creation.plan_contracts import CreationPlan
    from app.creation.contracts import CreationPlanPersonReference, CreationPlanVoiceReference, CreationTask
    from _media_preview_test_fixtures import register_synthetic_profiles

    repository = _repository(tmp_path)
    members = LocalMemberService(repository.database_path)
    joined, token = members.join(members.create_invite_code(), "制作成员")
    actor = permit_actor(members.resolve_member(token), WORKSPACE)
    access = MemberDeviceAccess(members)
    person, voice, _avatar_auth, _voice_path, _image_path = register_synthetic_profiles(
        repository, workspace_id=WORKSPACE, member_id=joined.member_id,
        person_id="person", voice_id="voice", person_version="v1", voice_version="v1",
        language="zh", model_id="fixture-model", person_authorization_id="person-auth",
        voice_authorization_id="voice-auth", voice_usage_scopes=("narration",),
    )
    plan = CreationPlan(id="plan", workspace_id=WORKSPACE, goal="测试", name="测试", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=15,
        knowledge_references=[], source_references=[],
        selected_person=CreationPlanPersonReference(person.id, person.version),
        selected_voice=CreationPlanVoiceReference(voice.id, voice.version, person.id, person.version,
            person.authorization.id, person.authorization.version, voice.authorization.id,
            voice.authorization.version, "zh-CN", "fixture-model"), pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="测试", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="fixture")
    task = CreationTask("task", WORKSPACE, plan.id, "测试", "knowledge_explanation", owner_user_id=joined.member_id)
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=joined.member_id)
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(id="script", workspace_id=WORKSPACE, task_id=task.id, run_id="run", topic_id="topic",
        title="已确认文案", language="zh-CN", sections=[dict(id="section", title="正文", content="测试",
            visual_prompt="", evidence_reference_ids=[])], selection_status="confirmed",
        created_at=now, updated_at=now, confirmed_at=now)
    with repository._connect() as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
            ("script", WORKSPACE, task.id, "run", script_seed_payload(repository, conn, payload), now))
        task.confirmed_script_id = "script"
        task.stage = "narration"
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?",
            (json.dumps(asdict(task), default=str), task.id))
    import _media_preview_test_fixtures as profile_fixtures
    from unittest.mock import patch
    from app.creation.contracts import CreationPlanVoiceReference
    full_wav = profile_fixtures.synthetic_wav_bytes
    with patch.object(profile_fixtures, 'synthetic_wav_bytes', lambda: full_wav(.01)):
        person, voice, *_ = profile_fixtures.register_synthetic_profiles(repository,
            workspace_id=WORKSPACE, member_id=joined.member_id,
            person_id='roster-person', voice_id='roster-voice', person_version='v1', voice_version='v1', language='zh')
    plan.selected_voice = CreationPlanVoiceReference(voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version, voice.authorization.id, voice.authorization.version,
        'zh-CN', voice.model_id)
    with repository._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?',
            (json.dumps(asdict(plan), default=str), plan.id))
    script = repository.script_records.get_creation_task_script(WORKSPACE, "script")
    plan = repository.plan_records.get_creation_plan(WORKSPACE, plan.id)
    from app.host_resource_probes import HostResourceDecision
    budget = NarrationBudget(80, 1, 1024, 15, 1024, 1, 10, 1024, 8, 0.1, 1024, 1024, input_scope="single_call_experiment_v1")
    owner = CreationJobOwnerVerifier(repository, narration_budget=budget, verify_identity=access.verify_identity)
    # No model is executed; this fixture only permits the real scheduler's admission.
    resources = SimpleNamespace(admit=lambda request: HostResourceDecision(True,dict(request.resources),{}), continue_unit=lambda grant: True,
        released=lambda grant: True)
    scheduler = DeviceScheduler(repository.database_path, tmp_path / "device.lock", owners=owner,
        resources=resources, processes=HostProcessProbe())
    def forbidden(*args):
        pytest.fail("grant-only roster test must never execute a model or media stage")
    execution = CreationExecution(repository, run_montage_plan=forbidden,
        run_render=forbidden, render_is_valid=forbidden)
    job = execution.start(WORKSPACE, task.id, script.id, revision_digest(script, plan), actor=actor)
    request_id = submit_creation_stage(execution, scheduler, job, actor)
    grant = None
    try:
        if remove_at == "before_grant":
            members.remove_member(joined.member_id)
            assert scheduler.pick_and_grant() is None
            assert scheduler._get(request_id).wait_reason == "authorization_lost"
        else:
            grant = scheduler.pick_and_grant()
            assert grant is not None and grant.request_id == request_id
            scheduler.assert_grant(grant)
            members.remove_member(joined.member_id)
            with pytest.raises(PermissionError, match="授权已失效"):
                scheduler.assert_grant(grant)
        assert access.verify_identity(actor) == "unauthorized"
        assert find_member(members, joined.member_id) is None
        with pytest.raises(LocalMembersError, match="凭据无效"):
            members.resolve_member(token)
    finally:
        if grant is not None:
            scheduler.fail_unit(grant, reason="fixture_finished", failure_write=lambda conn, current: "fixture_finished")
            scheduler.assert_released(grant)
        scheduler.close()


@pytest.fixture
def presenter_permit_case(tmp_path, monkeypatch):
    yield from _presenter_permit_case(tmp_path, monkeypatch, metadata_only=False)


@pytest.fixture
def presenter_input_stream_case(tmp_path, monkeypatch):
    yield from _presenter_permit_case(tmp_path, monkeypatch, metadata_only=True)


@pytest.fixture
def presenter_key_stream_case(tmp_path, monkeypatch):
    yield from _presenter_permit_case(tmp_path, monkeypatch, metadata_only=True, section_count=2)


def _presenter_permit_case(tmp_path, monkeypatch, *, metadata_only, section_count=1):
    """Persistent lawful inputs; resource availability and media bytes are fixtures."""
    import json
    from dataclasses import asdict
    from datetime import datetime, timezone
    from pathlib import Path
    from types import SimpleNamespace
    from app.creation_execution import CreationExecution, revision_digest
    from app.host_resource_probes import MontageBudget, PresenterBudget
    from app.creation.narration_artifacts import NarrationArtifactRecord
    from app.creation.plan_contracts import CreationPlan
    from app.creation.contracts import CreationPlanVoiceReference, CreationTask, NarrationSegment
    from app.creation.contracts import CreationPlanPersonReference
    from _media_preview_test_fixtures import register_synthetic_profiles, register_synthetic_video_asset, synthetic_wav_bytes

    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=1024**3))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    repo = _repository(tmp_path)
    members = LocalMemberService(repo.database_path)
    member, token = members.join(members.create_invite_code(), "合成人物制作成员")
    access = MemberDeviceAccess(members)
    actor = permit_actor(members.resolve_member(token), WORKSPACE)
    now = datetime.now(timezone.utc)
    person, voice, _avatar_auth, _voice_path, person_reference_path = register_synthetic_profiles(
        repo, workspace_id=WORKSPACE, member_id=member.member_id, person_id="person", voice_id="voice",
        person_version="v1", voice_version="v1", language="zh", model_id="synthetic-model",
        person_authorization_id="person-auth", voice_authorization_id="voice-auth",
    )
    person_auth = person.authorization
    if metadata_only:
        from app.materials.contracts import MaterialAsset, MaterialSegment
        path = tmp_path / 'library' / 'person.mp4'
        path.write_bytes(b'paid metadata-only person source; no codec')
        asset = MaterialAsset('person-video', WORKSPACE, path.name, str(path), path.stat().st_size,
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), duration_seconds=1,
            usage_role='b_roll', review_status='approved', analysis_status='ready', owner_user_id=member.member_id,
            segments=[MaterialSegment('person-video-segment-0', 'person-video', 0, 1,
                usage_role='b_roll', review_status='approved')])
        repo._save_asset(asset)
        with repo._connect() as conn:
            repo._claim_workspace_media_reference(conn, workspace_id=WORKSPACE,
                reference=repo.workspace_media_reference_for_asset(WORKSPACE, asset.id))
    else:
        register_synthetic_video_asset(repo, workspace_id=WORKSPACE, member_id=member.member_id,
            path=tmp_path / "library" / "person.mp4", asset_id="person-video", seconds=1)
    plan = CreationPlan(id="plan", workspace_id=WORKSPACE, goal="测试", name="测试", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=15,
        knowledge_references=[], source_references=[], selected_voice=CreationPlanVoiceReference(
            voice.id, voice.version, person.id, person.version, person.authorization.id,
            person.authorization.version, voice.authorization.id, voice.authorization.version, "zh-CN", "synthetic-model"),
        selected_person=CreationPlanPersonReference(person.id, person.version),
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint="fixture",
        presenter_share_target=20)
    assert plan.presenter_share_target == 20
    task = CreationTask("task", WORKSPACE, plan.id, "测试", "knowledge_explanation", owner_user_id=member.member_id)
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=member.member_id)
    payload = dict(id="script", workspace_id=WORKSPACE, task_id=task.id, run_id="run", topic_id="topic",
        title="已确认文案", language="zh-CN", sections=[dict(id="section", title="正文", content="合成文案",
            visual_prompt="", evidence_reference_ids=[])], selection_status="confirmed",
        created_at=now.isoformat(), updated_at=now.isoformat(), confirmed_at=now.isoformat())
    if section_count == 2:
        payload['sections'].append(dict(id='tail', title='尾段', content='中文 "quoted"\n café 😺',
            visual_prompt='', evidence_reference_ids=[]))
    audio = tmp_path / "spoken.wav"
    audio.write_bytes(synthetic_wav_bytes())
    repo.narration_artifacts.save_narration_artifact(NarrationArtifactRecord(id="narration", workspace_id=WORKSPACE,
        task_id=task.id, script_id="script", voice_profile_id="voice", cache_key="narration",
        audio_path=str(audio), duration_seconds=1, sample_rate=16000, model_id="fixture"))
    from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
    seeded = CreationTaskScript(**{key: value for key, value in payload.items()
        if key not in {'sections', 'created_at', 'updated_at', 'confirmed_at'}},
        sections=[CreationTaskScriptSection(**item) for item in payload['sections']],
        created_at=now, updated_at=now, confirmed_at=now)
    with repo._connect() as conn:
        conn.execute('BEGIN')
        encoded = repo.script_records._script_payload(conn, seeded)
        conn.execute("INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
            ("script", WORKSPACE, task.id, "run", encoded, now.isoformat()))
        task.confirmed_script_id = "script"
        task.narration_cache_key = "narration"
        task.stage = "montage"
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(task), default=str), task.id))
    repo.narration_records.save_narration_segments(WORKSPACE, task.id, "script", [NarrationSegment(
        'segment' if index == 0 else 'tail-segment', WORKSPACE, task.id, "script", section['id'], section['content'],
        "narration://artifact/narration", 1, 16000, "fixture", "voice", "v1", "zh", "v1", "ready")
        for index, section in enumerate(payload['sections'])])
    budget = PresenterBudget(1, 1024, 15, 4096, 1, 5, 1024**2*64, 32, .1, 1024**2*64, 4096)
    cpu = cpu_render_budget_from_env(_CPU_RENDER_ENV)
    def forbidden(*args):
        pytest.fail("no ungranted stage or real media work")
    execution = CreationExecution(repo, run_montage_plan=forbidden,
        run_render=forbidden, render_is_valid=forbidden)
    script = repo.script_records.get_creation_task_script(WORKSPACE, "script")
    persisted = repo.plan_records.get_creation_plan(WORKSPACE, plan.id)
    job = execution.start(WORKSPACE, task.id, script.id, revision_digest(script, persisted), actor=actor)
    registration = creation_job_registration(repo, render_action=forbidden, render_is_valid=forbidden,
        cpu_render_budget=cpu, presenter_cpu_render_budget=cpu, presenter_budget=budget,
        montage_budget=MontageBudget(1, 4, 100000, 100000, 5, 1),
        presenter_models={"fast": "fixture-avatar", "high": "fixture-high"},
        resolve_presenter_output=lambda render: Path(render.output_path) if render.output_path else None,
        verify_identity=access.verify_identity)
    case = SimpleNamespace(repo=repo, task=task, job=job, actor=actor, members=members, member=member,
        access=access, registration=registration, execution=execution, budget=budget, cpu=cpu,
        person_auth=person_auth, person_reference_path=person_reference_path,
        tmp_path=tmp_path, child_identities=[])
    yield case
    import fcntl
    processes = HostProcessProbe()
    assert processes.children_released(tuple(case.child_identities)) is True
    lock = tmp_path / "device.lock"
    lock_proof = "N/A: never held"
    if lock.exists():
        with lock.open("r") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_proof = "EX|NB reacquired"
            fcntl.flock(handle, fcntl.LOCK_UN)
    ledger_path = tmp_path / "device.lock.state"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else None
    if ledger is not None:
        assert ledger["state"] == "free"
    with repo._connect() as conn:
        has_permit = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='device_permit'").fetchone()[0]
        permit = dict(conn.execute("SELECT state,generation,request_id FROM device_permit WHERE id=1").fetchone()) if has_permit else None
        if permit is not None:
            assert permit["state"] == "free" and permit["request_id"] is None
    (tmp_path / "release-evidence.json").write_text(json.dumps({
        "children": [asdict(child) for child in case.child_identities], "groups_released": True,
        "lock": lock_proof, "ledger": ledger, "permit": permit}, indent=2))


def test_presenter_budget_absence_and_partial_configuration_do_not_borrow_cpu():
    from dataclasses import asdict
    from app.host_resource_probes import PresenterBudget
    from app.member_permits import presenter_budgets_from_env
    assert presenter_budgets_from_env(_CPU_RENDER_ENV) == (None, None)
    with pytest.raises(HostBudgetUncalibrated, match="人物专属预算"):
        presenter_budgets_from_env({"DEVICE_PRESENTER_MAX_THREADS": "1"})
    budget = PresenterBudget(1, 1024, 10, 1024, 1, 5, 1024, 8, .1, 1024, 1024)
    env = {"DEVICE_PRESENTER_" + name.upper(): str(value) for name, value in asdict(budget).items()}
    env.update({name.replace("DEVICE_CPU_RENDER_", "DEVICE_PRESENTER_CPU_RENDER_"): value
                for name, value in _CPU_RENDER_ENV.items()})
    actual, cpu = presenter_budgets_from_env(env)
    assert actual == budget and cpu == cpu_render_budget_from_env(_CPU_RENDER_ENV)


def _presenter_scheduler(case):
    from app.device_scheduler import DeviceScheduler
    from app.host_resource_probes import HostBudget, HostResourceDecision
    resources = HostResourceProbe(case.repo.database_path, disk_paths=(case.tmp_path,),
        budget=HostBudget(75, 1, 1, 1, 1024, 1024, presenter=case.budget, presenter_cpu_render=case.cpu))
    resources._sample = lambda declared, **options: HostResourceDecision(True, declared, {"fixture": {}}, None)
    processes = HostProcessProbe()
    real_identity = processes.identity
    def record_identity(pid):
        import os
        actual = real_identity(pid)
        if pid != os.getpid():
            case.child_identities.append(actual)
        return actual
    processes.identity = record_identity
    return DeviceScheduler(case.repo.database_path, case.tmp_path / "device.lock",
        owners=case.registration.owner, resources=resources, processes=processes)


def test_default_presenter_budget_required_before_queueing(presenter_permit_case):
    from app.creation_execution import submit_creation_stage
    case = presenter_permit_case
    scheduler = _presenter_scheduler(case)
    try:
        case.registration.owner.presenter_budget = None
        with pytest.raises(ValueError, match="专属预算尚未标定"):
            submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        with case.repo._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone()[0] == 0
        rejected = case.repo.job_records.get_job_run(WORKSPACE, case.job.id)
        assert rejected.status == "failed" and "专属预算尚未标定" in rejected.failure_reason
        assert rejected.finished_at is not None and rejected.output_refs == []
    finally:
        scheduler.close()


@pytest.mark.parametrize("change", ["member", "person_authorization", "audio", "cross_workspace"])
def test_presenter_grant_continuously_rechecks_original_facts_and_releases(presenter_permit_case, change):
    from dataclasses import replace
    from app.creation_execution import submit_creation_stage
    from app.device_scheduler import SubmissionIntent, SchedulePolicy
    case = presenter_permit_case
    scheduler = _presenter_scheduler(case)
    grant = None
    try:
        request_id = submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        request = scheduler._get(request_id)
        assert request.unit_key.startswith("person_render:")
        assert request.resources["kind"] == "creation_person_render"
        assert request.resources["section_count"] == 1
        if change == "cross_workspace":
            with pytest.raises(PermissionError):
                case.registration.owner.verify_submission(
                    SubmissionIntent(
                        WORKSPACE, "creation_job", case.job.id, case.job.input_digest, request.unit_key,
                        SchedulePolicy()),
                    replace(case.actor, workspace_id="other"))
            return
        grant = scheduler.pick_and_grant()
        assert grant is not None
        scheduler.assert_grant(grant)
        if change == "member":
            case.members.remove_member(case.member.member_id)
        elif change == "person_authorization":
            case.repo.save_authorization(replace(case.person_auth, version="v2", status="revoked"))
        else:
            (case.tmp_path / "spoken.wav").write_bytes(b"changed current narration")
        with pytest.raises(PermissionError):
            scheduler.assert_grant(grant)
    finally:
        if grant is not None:
            scheduler.fail_unit(grant, reason="fixture_finished", failure_write=lambda conn, current: "fixture_finished")
            scheduler.assert_released(grant)
        scheduler.close()


def _complete_presenter_fixture(case, context=None):
    """Synthetic provider output with actual repository job/artifact/transform lineage."""
    import hashlib
    import sys
    from datetime import datetime, timezone
    from app.avatar_lipsync import avatar_lipsync_cache_key
    from app.creation_execution import presenter_inputs, presenter_input_facts
    from app.creation.contracts import ArtifactRecord, JobRun, TransformRun
    from app.creation.person_renders import PersonRender
    from app.task_runtime import TaskRuntime, JobResult
    task = case.repo.task_records.get_creation_task(WORKSPACE, case.task.id)
    script = case.repo.script_records.get_creation_task_script(WORKSPACE, task.confirmed_script_id)
    plan = case.repo.plan_records.get_creation_plan(WORKSPACE, task.plan_id)
    segment, request = presenter_inputs(case.repo, task, script, plan, {"fast": "fixture-avatar"})[0]
    input_facts = presenter_input_facts(request)
    cache = avatar_lipsync_cache_key(request, input_facts=input_facts)
    render = case.repo.person_renders.create_person_render(PersonRender("person-render", WORKSPACE, task.id, "person", "v1",
        "voice", "v1", segment.id, "workspace://person.mp4", "fixture-avatar", "natural", "neutral", cache, "pending"))
    case.repo.person_renders.claim_person_render(WORKSPACE, task.id, render.id)
    output = case.tmp_path / "synthetic-person.mp4"
    if context is not None:
        context.run([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'synthetic-provider-output')",
                     str(output)], output_paths=(output,))
    else:
        output.write_bytes(b"synthetic-provider-output")
    logical_ref = "artifacts/task/person-job/person-render.mp4"
    inner = JobRun("person-job", WORKSPACE, task.id, "person_render", "person-job", cache)
    def work(job_context):
        case.repo.execution_records.create_artifact_record(ArtifactRecord("person-artifact", WORKSPACE, task.id, "person_render",
            logical_ref, hashlib.sha256(output.read_bytes()).hexdigest(), output.stat().st_size,
            job_context.job_id, [segment.audio_reference]))
        case.repo.lineage_records.create_transform_run(TransformRun("person-transform", WORKSPACE, task.id, job_context.job_id,
            "person_render", cache, [segment.audio_reference], [logical_ref], "synthetic-provider",
            {"language": segment.language, "presenter_input_facts": input_facts}, "succeeded", datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc)))
        return JobResult((logical_ref,))
    assert TaskRuntime(case.repo.job_records).run(inner, work).status == "succeeded"
    case.repo.person_renders.finish_person_render(WORKSPACE, task.id, render.id, status="ready", output_path=str(output),
        duration_seconds=1, alignment_evidence="synthetic timing; no media decoding")
    case.repo.person_renders.bind_person_render_artifact(WORKSPACE, task.id, render.id, logical_ref)
    return case.repo.person_renders.get_person_render(WORKSPACE, task.id, render.id)


def test_presenter_unit_executes_with_registered_child_and_queues_metadata_after_release(presenter_permit_case, monkeypatch):
    import json
    from app.creation_execution import submit_creation_stage
    case = presenter_permit_case
    scheduler = _presenter_scheduler(case)
    import app.main as main
    case.registration.executor.person_stage = main._person_stage_use_case(case.repo)
    monkeypatch.setattr(case.registration.executor.person_stage, 'generate_stage',
        lambda _w, _t, context: _complete_presenter_fixture(case, context))
    try:
        request_id = submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        case.registration.executor.execute(grant, scheduler)
        scheduler.assert_released(grant)
        assert scheduler._get(request_id).state == "done"
        assert case.repo.job_records.get_job_run(WORKSPACE, case.job.id).status == "queued"
        task = case.repo.task_records.get_creation_task(WORKSPACE, case.task.id)
        assert task.narration_cache_key == "narration" and task.confirmed_script_id == "script"
        with case.repo._connect() as conn:
            rows = conn.execute("SELECT unit_key,state FROM device_work_requests ORDER BY created_at").fetchall()
            permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
            assert [row['unit_key'].split(':')[0] for row in rows] == ["person_render", "montage_plan"]
            assert permit["state"] == "free"
            events = conn.execute("SELECT payload_json FROM task_events WHERE event_type='creation_execution_stage'").fetchall()
            assert any(json.loads(row[0])["stage"] == "person_render" for row in events)
    finally:
        scheduler.close()


@pytest.mark.parametrize("reason", ["cancel", "revoke"])
def test_presenter_live_child_cancel_or_revoke_proves_group_and_permit_release(presenter_permit_case, reason):
    import sys
    import threading
    import time
    from app.creation_execution import CreationStageGrantContext, submit_creation_stage
    from app.granted_subprocess import GrantedCommandCancelled
    case = presenter_permit_case
    scheduler = _presenter_scheduler(case)
    grant = None
    thread = None
    cancellation = threading.Event()
    marker = case.tmp_path / "started"
    try:
        submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        context = CreationStageGrantContext(scheduler, grant, time.monotonic()+5, 4096, 1, cancellation)
        def stop_after_exec():
            deadline = time.monotonic()+3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            if reason == "cancel":
                cancellation.set()
            else:
                case.members.remove_member(case.member.member_id)
        thread = threading.Thread(target=stop_after_exec)
        thread.start()
        with pytest.raises(GrantedCommandCancelled if reason == "cancel" else PermissionError):
            context.run([sys.executable, "-c", "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(20)", str(marker)])
        assert marker.exists()
        with case.repo._connect() as conn:
            children = __import__('json').loads(conn.execute("SELECT children_json FROM device_permit WHERE id=1").fetchone()[0])
        from app.device_scheduler import ProcessIdentity
        assert children and scheduler.processes.children_released(tuple(ProcessIdentity(**child) for child in children)) is True
    finally:
        if thread is not None:
            thread.join(timeout=4)
            assert not thread.is_alive()
        if grant is not None:
            scheduler.fail_unit(grant, reason="fixture_finished", failure_write=lambda conn, current: "fixture_finished")
            scheduler.assert_released(grant)
        scheduler.close()


def _presenter_montage_fixture(case, render):
    import json
    from dataclasses import asdict
    from datetime import datetime, timezone
    from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
    task = case.repo.task_records.get_creation_task(WORKSPACE, case.task.id)
    task.montage_plan_id = "montage"
    cue = NarrationCue("cue", "section", "合成文案", 1, narration_segment_id="segment")
    clip = MontageClip("clip", "cue", f"person:{render.id}", "segment", f"person-render://{render.id}",
        0, 1, 0, 1, 1, ["合成已确认人物镜头"], False, person_render_id=render.id)
    montage = CreationTaskMontagePlan("montage", WORKSPACE, task.id, "script", "合成文案", "ready",
        [cue], [clip], 1, 0, narration_audio_path="narration://artifact/narration", presenter_share_target=20)
    with case.repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(task), default=str), task.id))
        conn.execute("INSERT INTO creation_task_montage_plans(id,workspace_id,task_id,payload,updated_at) VALUES(?,?,?,?,?)",
            (montage.id, WORKSPACE, task.id, json.dumps(creation_task_montage_plan_to_payload(montage)), datetime.now(timezone.utc).isoformat()))
    return task


def test_presenter_composition_accounts_for_actual_person_media_and_uses_its_own_cpu_budget(presenter_permit_case):
    from dataclasses import replace
    from app.creation_execution import CpuRenderLimits, cpu_render_facts, submit_creation_stage
    case = presenter_permit_case
    task = _presenter_montage_fixture(case, _complete_presenter_fixture(case))
    plan = case.repo.plan_records.get_creation_plan(WORKSPACE, task.plan_id)
    with case.repo._connect() as conn:
        with pytest.raises(ValueError, match="人物组合.*专属预算"):
            cpu_render_facts(case.repo, conn, task, plan, CpuRenderLimits(99999999, 8, 999))
        key, resources = case.registration.owner.render_facts(conn, task, plan)
    assert key.startswith("render:") and resources["kind"] == "creation_presenter_render"
    assert resources["input_bytes"] == (case.tmp_path / "spoken.wav").stat().st_size + (case.tmp_path / "synthetic-person.mp4").stat().st_size
    assert resources["threads"] == case.cpu.max_threads
    scheduler = _presenter_scheduler(case)
    grant = None
    try:
        submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None and grant.unit_key == key
        scheduler.assert_grant(grant)
        # Cached person media never evades the original continuous authorization check.
        case.repo.save_authorization(replace(case.person_auth, version="v2", status="revoked"))
        with pytest.raises(PermissionError):
            scheduler.assert_grant(grant)
        with case.repo._connect() as conn, pytest.raises(ValueError, match="authorization_revoked"):
            case.registration.owner.render_facts(conn, task, plan)
    finally:
        if grant is not None:
            scheduler.fail_unit(grant, reason="fixture_finished", failure_write=lambda conn, current: "fixture_finished")
            scheduler.assert_released(grant)
        scheduler.close()


def test_actual_person_stage_consumer_publishes_under_same_grant(presenter_permit_case, monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from app.artifact_store import ArtifactStore
    from app.avatar_lipsync import AvatarAndLipSyncResult
    from app.creation_execution import submit_creation_stage
    from app.schemas import CreationTaskPersonRenderRequest
    from app.video_jobs import VideoJobRunner
    case = presenter_permit_case
    store = ArtifactStore(case.tmp_path / "output")
    class SyntheticService:
        def __init__(self, root, provider):
            self.root = Path(root)
        def render(self, request, *, output_cache_key, grant_context, **_kwargs):
            assert current_status()["current_stage"] == "person_render"
            output = self.root / "synthetic-provider.mp4"
            grant_context.run([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'synthetic-person')",
                               str(output)], output_paths=(output,))
            return AvatarAndLipSyncResult("succeeded", output_cache_key, str(output), 1,
                "synthetic timing; no decoding", None, None, None)
    def unexpected(**details):
        pytest.fail(f"unexpected consumer failure: {details}")
    import app.main as main
    import app.creation.person_stage_use_case as person_module
    from dataclasses import replace
    from app.creation.person_stage_use_case import PersonStageRejected
    from app.creation.execution_control import CreationExecutionControl
    monkeypatch.setattr(main, 'local_members_service', case.members)
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_output_path=str(case.tmp_path / 'output'), temporary_root_path=str(case.tmp_path / 'temporary')))
    provider = SimpleNamespace(ready=True, model_id='fixture-avatar')
    monkeypatch.setattr(main, 'latentsync_provider', provider)
    monkeypatch.setattr(main, 'avatar_lipsync_provider', provider)
    monkeypatch.setattr(main, 'video_job_runner', VideoJobRunner(case.repo.job_records))
    monkeypatch.setattr(person_module, 'AvatarAndLipSyncService', SyntheticService)
    stage = main._person_stage_use_case(case.repo)
    control = CreationExecutionControl(case.repo, case.execution, main._render_stage_use_case(case.repo),
        case.registration.owner.verify_identity)
    def current_status():
        return control.status_summary(WORKSPACE, case.task.id)
    case.registration.owner.resolve_presenter_output = stage.metadata.picture_renderer.person_path
    case.registration.executor.person_stage = stage
    scheduler = _presenter_scheduler(case)
    try:
        # No-Grant direct product consumer rejects before any job/person row is created.
        with pytest.raises(PersonStageRejected) as retired:
            stage.generate_record(WORKSPACE, case.task.id,
                CreationTaskPersonRenderRequest(narration_segment_id="segment", person_profile_id="person", person_version="v1"))
        assert retired.value.status_code == 409
        assert case.repo.person_renders.list_person_renders(WORKSPACE, case.task.id) == []
        request_id = submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        assert current_status()["current_stage"] == "person_render"
        grant = scheduler.pick_and_grant()
        assert grant is not None
        case.registration.executor.execute(grant, scheduler)
        scheduler.assert_released(grant)
        assert scheduler._get(request_id).state == "done"
        assert current_status()["current_stage"] == "montage_plan"
        rendered = case.repo.person_renders.list_person_renders(WORKSPACE, case.task.id)
        assert len(rendered) == 1 and rendered[0].status == "ready"
        assert store.resolve_file(rendered[0].artifact_ref).read_bytes() == b"synthetic-person"
        assert case.repo.job_records.get_job_run(WORKSPACE, case.job.id).status == "queued"
        events = case.repo.execution_records.list_task_events(WORKSPACE, case.task.id)
        assert any(event.payload.get("stage") == "person_render" and event.payload.get("status") == "running" for event in events)
    finally:
        scheduler.close()


@pytest.mark.parametrize("source", ["spoken.wav", "person_reference", "legacy_transform"])
def test_ready_presenter_cache_rejects_changed_audio_or_visual_under_same_reference(presenter_permit_case, source):
    case = presenter_permit_case
    import os
    task = _presenter_montage_fixture(case, _complete_presenter_fixture(case))
    plan = case.repo.plan_records.get_creation_plan(WORKSPACE, task.plan_id)
    assert case.registration.owner.person_valid(task)
    with case.repo._connect() as conn:
        case.registration.owner.render_facts(conn, task, plan)
    if source == "legacy_transform":
        with case.repo._connect() as conn:
            conn.execute("UPDATE transform_runs SET parameters_json=? WHERE id=?",
                         ('{"language":"zh"}', "person-transform"))
    elif source == "person_reference":
        from PIL import Image
        original = case.person_reference_path
        stat = original.stat()
        Image.new("RGB", (16, 16), (220, 40, 80)).save(original, format="PNG")
        os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        original = case.tmp_path / source
        stat = original.stat()
        original.write_bytes(b"x" * stat.st_size)
        os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert not case.registration.owner.person_valid(task)
    with case.repo._connect() as conn, pytest.raises(ValueError, match="人物镜头版本、授权或输出已失效"):
        case.registration.owner.render_facts(conn, task, plan)


def test_presenter_row_admission_preserves_original_json_key_and_resources(presenter_key_stream_case, monkeypatch):
    import hashlib
    import json
    from dataclasses import asdict, replace
    from app.creation_execution import presenter_inputs, presenter_input_facts, presenter_stage_facts
    case = presenter_key_stream_case
    repo = case.repo
    script = repo.script_records.get_creation_task_script(WORKSPACE, 'script')
    plan = replace(repo.plan_records.get_creation_plan(WORKSPACE, case.task.plan_id), presenter_share_target=100)
    inputs = presenter_inputs(repo, case.task, script, plan, {'fast': 'fixture-avatar'})
    files = {fact['path']: fact for _, request in inputs for fact in presenter_input_facts(request)}
    facts = [files[path] for path in sorted(files)]
    resources = {'kind': 'creation_person_render', 'execution_mode': 'isolated_avatar_lipsync',
        'section_count': len(inputs), 'input_bytes': sum(item['size_bytes'] for item in facts),
        'duration_seconds': sum(segment.duration_seconds for segment, _ in inputs),
        'max_output_bytes': case.budget.max_output_bytes, 'threads': case.budget.max_threads,
        'timeout_seconds': case.budget.max_timeout_seconds}
    original = {'inputs': [[asdict(segment), asdict(request)] for segment, request in inputs],
        'files': facts, 'resources': resources}
    expected = 'person_render:' + hashlib.sha256(json.dumps(original, sort_keys=True, default=str).encode()).hexdigest()
    def forbidden(*args, **kwargs):
        pytest.fail('person admission must not hydrate whole script/segment inputs')
    for name in ('get_creation_task_script', '_creation_task_script_from_payload', 'list_narration_segments'):
        monkeypatch.setattr(repo.script_records if name in ('get_creation_task_script', '_creation_task_script_from_payload') else repo.narration_records if name in ('list_narration_segments',) else repo, name, forbidden)
    monkeypatch.setattr(repo.narration_records, '_decode_script', forbidden)
    with repo.execution_inputs.creation_execution_read() as conn:
        snapshot = repo.execution_inputs.read_creation_execution_snapshot(WORKSPACE, case.task.id, connection=conn)
        actual, measured = presenter_stage_facts(repo, snapshot.task, snapshot.script_header, plan,
            case.budget, {'fast': 'fixture-avatar'}, connection=conn)
        assert actual == expected and measured == resources and conn.in_transaction
        assert measured['section_count'] == 2


def _stream_person_executor(case, monkeypatch, boundary):
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace
    from app.artifact_store import ArtifactStore
    from app.avatar_lipsync import AvatarAndLipSyncResult
    from app.creation.person_stage_use_case import PersonStageUseCase
    from app.creation_execution import CreationJobUnitExecutor
    from app.video_jobs import VideoJobRunner
    import app.creation.person_stage_use_case as module
    active = {}
    read = case.repo.execution_inputs.creation_execution_read
    @contextmanager
    def tracked(connection=None):
        with read(connection) as conn:
            active[id(conn)] = active.get(id(conn), 0) + 1
            try:
                yield conn
            finally:
                active[id(conn)] -= 1
                if not active[id(conn)]:
                    del active[id(conn)]
    monkeypatch.setattr(case.repo.execution_inputs, 'creation_execution_read', tracked)
    store = ArtifactStore(case.tmp_path/'stream-output')
    metadata = SimpleNamespace(artifact_store=store, picture_renderer=SimpleNamespace(
        person_path=lambda render: Path(render.output_path) if render.output_path and Path(render.output_path).is_file() else None))
    provider = SimpleNamespace(ready=True, model_id='fixture-avatar')
    class PaidProvider:
        def __init__(self, root, provider):
            self.root = Path(root)
        def render(self, request, *, output_cache_key, grant_context, **kwargs):
            assert not active, 'source read transaction must exit before the provider boundary'
            grant_context.check()
            boundary.append(grant_context.grant.request_id)
            output = self.root/'paid-person.mp4'
            output.write_bytes(b'paid synthetic person output; no model or codec')
            return AvatarAndLipSyncResult('succeeded', output_cache_key, str(output), 1,
                'fixture alignment; no model or decoding', None, None, None)
    monkeypatch.setattr(module, 'AvatarAndLipSyncService', PaidProvider)
    root = case.tmp_path/'stream-output'
    root.mkdir(exist_ok=True)
    stage = PersonStageUseCase(case.repo, metadata, SimpleNamespace(provider=lambda quality: provider),
        VideoJobRunner(case.repo.job_records), SimpleNamespace(video_output_path=root))
    def forbidden(*args, **kwargs):
        pytest.fail('person stream must not hydrate whole input DTOs or execute a worker')
    for name in ('get_creation_task_script', '_creation_task_script_from_payload', 'list_narration_segments'):
        monkeypatch.setattr(case.repo.script_records if name in ('get_creation_task_script', '_creation_task_script_from_payload') else case.repo.narration_records if name in ('list_narration_segments',) else case.repo, name, forbidden)
    monkeypatch.setattr(case.repo.narration_records, '_decode_script', forbidden)
    monkeypatch.setattr('app.granted_subprocess.run_granted_command', forbidden)
    return CreationJobUnitExecutor(case.repo, render_action=forbidden, render_is_valid=lambda *args: False,
        person_stage=stage)


def test_presenter_row_owner_executes_after_reader_release_and_reuses_original_request(presenter_input_stream_case, monkeypatch):
    from app.creation_execution import submit_creation_stage
    case = presenter_input_stream_case
    boundary = []
    executor = _stream_person_executor(case, monkeypatch, boundary)
    scheduler = _presenter_scheduler(case)
    grant = None
    try:
        request_id = submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        assert submit_creation_stage(case.execution, scheduler, case.job, case.actor) == request_id
        original = scheduler._get(request_id)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        executor.execute(grant, scheduler)
        assert boundary == [request_id]
        assert scheduler._get(request_id).resources == original.resources
        assert scheduler._get(request_id).state == 'done'
        assert case.repo.job_records.get_job_run(WORKSPACE, case.job.id).status == 'queued'
        assert len(case.repo.person_renders.list_person_renders(WORKSPACE, case.task.id)) == 1
        scheduler.assert_released(grant)
    finally:
        if grant is not None and scheduler._get(grant.request_id).state in {'granted', 'yield_requested'}:
            scheduler.fail_unit(grant, reason='fixture_finished', failure_write=lambda conn, current: 'fixture_finished')
        scheduler.close()


@pytest.mark.parametrize('change', ['authorization', 'tail'])
def test_presenter_no_next_owner_rechecks_original_request_before_checkpoint(presenter_input_stream_case, monkeypatch, change):
    import json
    from types import SimpleNamespace
    import app.creation_execution as module
    case = presenter_input_stream_case
    boundary = []
    executor = _stream_person_executor(case, monkeypatch, boundary)
    scheduler = _presenter_scheduler(case)
    grant = None
    try:
        request_id = module.submit_creation_stage(case.execution, scheduler, case.job, case.actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        # A preexisting review gate makes the real next-unit selection return None.
        _presenter_montage_fixture(case, SimpleNamespace(id='pending-review-person'))
        with case.repo._connect() as conn:
            row = conn.execute('SELECT payload FROM creation_task_montage_plans WHERE id=?', ('montage',)).fetchone()
            pending = json.loads(row['payload'])
            pending['status'] = 'waiting_for_user'
            pending['clips'][0]['needs_confirmation'] = True
            conn.execute('UPDATE creation_task_montage_plans SET payload=? WHERE id=?', (json.dumps(pending), 'montage'))
        next_units = []
        select_next = module.next_creation_intent
        def observed(*args, **kwargs):
            result = select_next(*args, **kwargs)
            next_units.append(result)
            return result
        monkeypatch.setattr(module, 'next_creation_intent', observed)
        released = scheduler._released_for_commit
        injected = []
        def changed(conn, current):
            result = released(conn, current)
            if not injected:
                injected.append(True)
                if change == 'authorization':
                    monkeypatch.setattr(case.registration.owner, 'verify_identity', lambda _: 'unauthorized')
                else:
                    conn.execute("UPDATE creation_task_script_sections SET section_json=json_set(section_json,'$.content','changed tail') WHERE script_id='script' AND section_id='section'")
            return result
        monkeypatch.setattr(scheduler, '_released_for_commit', changed)
        executor.execute(grant, scheduler)
        assert next_units == [None] and injected and boundary == [request_id]
        assert scheduler._get(request_id).state != 'done'
        assert case.repo.job_records.get_job_run(WORKSPACE, case.job.id).status == 'interrupted'
        rendered = case.repo.person_renders.list_person_renders(WORKSPACE, case.task.id)
        assert len(rendered) == 1 and rendered[0].status == 'ready'
        assert not [event for event in case.repo.execution_records.list_task_events(WORKSPACE, case.task.id)
            if event.job_id == case.job.id and event.event_type == 'creation_execution_stage'
            and event.payload.get('stage') == 'person_render' and event.payload.get('status') == 'completed']
    finally:
        if grant is not None and scheduler._get(grant.request_id).state in {'granted', 'yield_requested'}:
            scheduler.fail_unit(grant, reason='fixture_finished', failure_write=lambda conn, current: 'fixture_finished')
        scheduler.close()


def test_presenter_cpu_row_inputs_keep_actual_source_accounting_and_cache_revalidation(presenter_input_stream_case, monkeypatch):
    case = presenter_input_stream_case
    render = _complete_presenter_fixture(case)
    task = _presenter_montage_fixture(case, render)
    plan = case.repo.plan_records.get_creation_plan(WORKSPACE, task.plan_id)
    def forbidden(*args, **kwargs):
        pytest.fail('person CPU admission must not load full input DTOs')
    for name in ('get_creation_task_script', '_creation_task_script_from_payload', 'list_narration_segments'):
        monkeypatch.setattr(case.repo.script_records if name in ('get_creation_task_script', '_creation_task_script_from_payload') else case.repo.narration_records if name in ('list_narration_segments',) else case.repo, name, forbidden)
    monkeypatch.setattr(case.repo.narration_records, '_decode_script', forbidden)
    with case.repo._connect() as idle:
        assert not idle.in_transaction
        key, resources = case.registration.owner.render_facts(idle, task, plan)
        assert key.startswith('render:') and resources['kind'] == 'creation_presenter_render'
        assert resources['input_bytes'] == (case.tmp_path/'spoken.wav').stat().st_size + (case.tmp_path/'synthetic-person.mp4').stat().st_size
        assert resources['threads'] == case.cpu.max_threads
        assert not idle.in_transaction and idle.execute('SELECT 1').fetchone()[0] == 1
    source = case.tmp_path/'spoken.wav'
    source.write_bytes(source.read_bytes()+b'changed')
    with case.repo.execution_inputs.creation_execution_read() as conn, pytest.raises(ValueError, match='失效'):
        case.registration.owner.render_facts(conn, task, plan)
