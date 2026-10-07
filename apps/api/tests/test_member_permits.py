"""WIL-159：真实本机成员 + 真实整机许可 + 真实探针。

这一组只证明一件事——从「本机名册里的一行成员」到「整机队列上的一次心跳」，
中间没有假探针、没有字符串身份、没有第二条锁。没有标定预算时，这里要求它
明确报未标定，而不是悄悄放行。
"""

from __future__ import annotations

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
    creation_job_registration,
    find_member,
    host_budget_from_env,
    permit_actor,
    resolve_member_token,
    PermitIdentityUnavailable,
)
from app.production import ProductionRepository

WORKSPACE = "local"

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
    budget = cpu_render_budget_from_env(_CPU_RENDER_ENV)
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

    from app.avatar_lipsync import Authorization, PersonProfile, VoiceProfile
    from app.creation_execution import (
        CreationExecution, CreationJobOwnerVerifier, revision_digest, submit_creation_stage,
    )
    from app.device_scheduler import DeviceScheduler
    from app.host_resource_probes import NarrationBudget
    from app.production import CreationPlan, CreationPlanVoiceReference, CreationTask

    repository = _repository(tmp_path)
    members = LocalMemberService(repository.database_path)
    joined, token = members.join(members.create_invite_code(), "制作成员")
    actor = permit_actor(members.resolve_member(token), WORKSPACE)
    access = MemberDeviceAccess(members)
    reference = tmp_path / "library/reference.wav"
    reference.write_bytes(b"synthetic reference; no inference in this test")
    auth = Authorization("auth", "v1", "fixture", ("voice",), ("narration",),
        datetime(2020, 1, 1, tzinfo=timezone.utc), None, "synthetic fixture", "active", WORKSPACE)
    repository.save_person_profile(PersonProfile("person", WORKSPACE, "夹具", "v1",
        "fixture://unused-avatar", (), (), auth))
    repository.save_versioned_voice_profile(VoiceProfile("voice", WORKSPACE, "person", "v1",
        "v1", "zh", "workspace://reference.wav", "参考", "fixture-model", auth))
    plan = CreationPlan(id="plan", workspace_id=WORKSPACE, goal="测试", name="测试", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=15,
        knowledge_references=[], source_references=[],
        selected_voice=CreationPlanVoiceReference("voice", "v1", "person", "v1", "auth", "v1",
            "auth", "v1", "zh-CN", "fixture-model"), pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="测试", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="fixture")
    task = CreationTask("task", WORKSPACE, plan.id, "测试", "knowledge_explanation")
    repository.confirm_creation_plan(plan, task)
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(id="script", workspace_id=WORKSPACE, task_id=task.id, run_id="run", topic_id="topic",
        title="已确认文案", language="zh-CN", sections=[dict(id="section", title="正文", content="测试",
            visual_prompt="", evidence_reference_ids=[])], selection_status="confirmed",
        created_at=now, updated_at=now, confirmed_at=now)
    with repository._connect() as conn:
        conn.execute("INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
            ("script", WORKSPACE, task.id, "run", json.dumps(payload), now))
        task.confirmed_script_id = "script"
        task.stage = "narration"
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?",
            (json.dumps(asdict(task), default=str), task.id))
    script = repository.get_creation_task_script(WORKSPACE, "script")
    plan = repository.get_creation_plan(WORKSPACE, plan.id)
    budget = NarrationBudget(80, 1, 1024, 15, 1024, 1, 10, 1024, 8, 0.1, 1024, 1024)
    owner = CreationJobOwnerVerifier(repository, narration_budget=budget, verify_identity=access.verify_identity)
    # No model is executed; this fixture only permits the real scheduler's admission.
    resources = SimpleNamespace(admit=lambda request: True, continue_unit=lambda grant: True,
        released=lambda grant: True)
    scheduler = DeviceScheduler(repository.database_path, tmp_path / "device.lock", owners=owner,
        resources=resources, processes=HostProcessProbe())
    def forbidden(*args):
        pytest.fail("grant-only roster test must never execute a model or media stage")
    execution = CreationExecution(repository, run_narration=forbidden, run_montage_plan=forbidden,
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
