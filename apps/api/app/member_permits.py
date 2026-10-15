"""真实本机成员到整机许可的接入（WIL-159）。

这一层只做一件事：把「谁在请求整机许可」这件事从字符串换成服务端可核验的
成员身份，并把它接到设备许可的既有入口上。

四条规矩：

1. 身份只来自 ``local_members`` 的成员行。客户端永远不能自报身份；路由读
   ``X-Local-Member-Token``，这里只负责把令牌解析成 ``member_id`` 并在每次
   校验时**重新读一次名册**——被移除的成员在下一次心跳/队列/取消时立刻失效，
   不靠旧会话继续操作（auth-workspace-boundary §9）。
2. 不保存原始令牌，也不伪造 ``VerifiedSession``。``VerifiedSession`` 只在成员
   行当下仍然存在时才由服务端签发。
3. 交互式的 ``creation_job`` 注册进**同一个**整机运行时：和 ``material_asset``
   共用一个锁、一张队列、一个驱动线程，不另起第二套许可状态。
4. 整机预算与探针是真实来源（``host_resource_probes``）。没有标定预算就明确
   报「尚未标定」，不造默认值、不造一个永远返回 None 的假探针，也不改动既有
   资源门禁。
"""

from __future__ import annotations

import os
import json
from dataclasses import fields
from pathlib import Path
from typing import Callable, Mapping

from pydantic import BaseModel, Field

from app.creation_execution import (
    CreationExecutionActor,
    CreationJobOwnerVerifier,
    CreationJobUnitExecutor,
    CpuRenderLimits,
)
from app.device_runtime import OwnerRegistration
from app.device_scheduler import SessionHeartbeat, VerifiedSession
from app.host_resource_probes import (
    CpuRenderBudget,
    CpuAudioBudget,
    MediaScriptBudget,
    ObservationBudget,
    HostBudget,
    HostProcessProbe,
    HostResourceProbe,
    NarrationBudget,
    MontageBudget,
    PresenterBudget,
)
from app.local_members import LocalMember, LocalMemberService
from app.device_accounts import DeviceAccountService
from app.repository import LEGACY_WORKSPACE_ID


class PermitIdentityUnavailable(RuntimeError):
    """这次调用没有可核验的本机成员身份，不签发任何持久许可。"""


class HostBudgetUncalibrated(RuntimeError):
    """整机许可预算没有真实标定值；不猜默认值，直接说明不可用。"""


def find_member(members: LocalMemberService, member_id: str) -> LocalMember | None:
    """按 ``member_id`` 取当前仍然存在的成员行。

    名册上限五人（auth §9），逐行比对比新造一张索引更实在；成员被移除后这里
    返回 ``None``，调用方据此判定身份已失效。
    """
    for member in members.list_members():
        if member.member_id == member_id:
            return member
    return None


def resolve_member_token(
    *,
    members: LocalMemberService,
    token: str,
    auth_enabled: bool,
) -> LocalMember | None:
    """把本次请求的本地成员令牌解析成真实成员行。

    带令牌：无效令牌是凭据失败（抛 ``LocalMembersError``），不降级成匿名。
    不带令牌且认证已开：没有可核验身份，返回 ``None``——不凭空造一个成员。
    不带令牌且认证未开（本机单人的兼容态）：取名册里真实存在的主账号行。
    它是一条可被移除、可被改角色的数据库记录，比一个字符串身份更严。
    """
    if token:
        member = members.resolve_member(token)
        assert member is not None  # 带有效令牌必然解析出成员
        return member
    if auth_enabled:
        return None
    return members.whoami(None)[0]


def permit_actor(member: LocalMember | None, workspace_id: str) -> CreationExecutionActor | None:
    """成员行 → 设备许可使用的可信身份；没有成员就不签发身份。"""
    if member is None:
        return None
    return CreationExecutionActor(workspace_id, member.member_id)


class MemberDeviceAccess:
    """把 ``DeviceAccess`` 接到本机成员名册上。

    每次校验都重新读名册：成员被移除后，旧 ``session_id`` 和旧令牌都不能
    继续拿到 ``VerifiedSession``，也不能再读整机摘要。
    """

    def __init__(self, members: LocalMemberService,
                 accounts: DeviceAccountService | None = None,
                 identity_verifier: Callable[[CreationExecutionActor], str] | None = None) -> None:
        self._members = members
        self._accounts = accounts
        self._identity_verifier = identity_verifier

    def verify_identity(self, actor: CreationExecutionActor) -> str:
        if not isinstance(actor, CreationExecutionActor) or not actor.member_id:
            return "unauthorized"
        if self._identity_verifier is not None:
            return self._identity_verifier(actor)
        if actor.device_id is not None:
            if self._accounts is None:
                return "unknown"
            return self._accounts.creation_permit(actor.device_id, actor.user_id, actor.workspace_id)
        if actor.workspace_id != LEGACY_WORKSPACE_ID or self._accounts is not None:
            return "unauthorized"
        return "ready" if find_member(self._members, actor.member_id) is not None else "unauthorized"

    def verify_session(self, heartbeat: SessionHeartbeat, actor: object) -> VerifiedSession:
        verdict = self.verify_identity(actor)
        if verdict == "unknown":
            raise PermitIdentityUnavailable("设备成员许可事实源不可用")
        if verdict != "ready":
            raise PermissionError("成员已不存在或工作区许可身份失效")
        if heartbeat.workspace_id != actor.workspace_id:
            raise PermissionError("会话工作区与许可身份不匹配")
        if not heartbeat.session_id:
            raise PermissionError("会话标识不能为空")
        return VerifiedSession(
            session_id=heartbeat.session_id,
            workspace_id=heartbeat.workspace_id,
            member_id=actor.member_id,
        )

    def can_view_device_summary(self, actor: object) -> bool:
        return self.verify_identity(actor) == "ready"


def cpu_render_limits_from_budget(
    budget: CpuRenderBudget | None,
) -> CpuRenderLimits | None:
    """成片预算里验证者真正用到的三项；没有标定就返回 ``None``（不放行）。"""
    if budget is None:
        return None
    return CpuRenderLimits(
        max_output_bytes=budget.max_output_bytes,
        threads=budget.max_threads,
        timeout_seconds=int(budget.max_timeout_seconds),
    )


def creation_job_registration(repository, *, render_action: Callable[[str, str, object], object], render_is_valid: Callable[[str, str, object], bool], cpu_render_budget: CpuRenderBudget | None=None, verify_identity: Callable[[CreationExecutionActor], str] | None=None, narration_budget: NarrationBudget | None=None, montage_budget: MontageBudget | None=None, montage_stage=None, presenter_budget: PresenterBudget | None=None, presenter_cpu_render_budget: CpuRenderBudget | None=None, presenter_models: dict | None=None, resolve_presenter_output=None, task_media=None, graphic_source_facts=None, audio_mix_facts=None, narration_stage=None, person_stage=None) -> OwnerRegistration:
    """交互式制作作业的注册项：既有验证者 + 既有单元执行器。

    ``cpu_render_budget`` 为 ``None`` 表示这台机器还没有标定 CPU 成片预算；
    成片阶段会因此拒绝准入，而不是被放行。
    """
    return OwnerRegistration(CreationJobOwnerVerifier(repository, cpu_render_limits=cpu_render_limits_from_budget(cpu_render_budget), verify_identity=verify_identity, narration_budget=narration_budget, montage_budget=montage_budget, presenter_budget=presenter_budget, presenter_cpu_render_limits=cpu_render_limits_from_budget(presenter_cpu_render_budget), presenter_models=presenter_models, resolve_presenter_output=resolve_presenter_output, task_media=task_media, graphic_source_facts=graphic_source_facts, audio_mix_facts=audio_mix_facts), CreationJobUnitExecutor(repository, render_action=render_action, render_is_valid=render_is_valid, montage_stage=montage_stage, task_media=task_media, narration_stage=narration_stage, person_stage=person_stage))


#: 整机预算的标定项。这些值必须由这台设备的标定结果显式给出，没有内置默认值。
def creation_stage_budgets_from_env(env: Mapping[str, str] | None = None):
    """An uncalibrated stage stays unavailable without blocking other stages."""
    environment = os.environ if env is None else env
    budgets = []
    for budget_type, prefix in ((NarrationBudget, "DEVICE_NARRATION_"), (MontageBudget, "DEVICE_MONTAGE_")):
        if not any(environment.get(prefix + item.name.upper(), "").strip() for item in fields(budget_type)):
            budgets.append(None)
            continue
        values = {}
        for item in fields(budget_type):
            name = prefix + item.name.upper()
            raw = environment.get(name)
            if not raw:
                raise HostBudgetUncalibrated(f"阶段资源预算尚未标定：请配置 {name}")
            try:
                values[item.name] = float(raw) if item.name in {
                    "max_duration_seconds", "max_timeout_seconds", "runtime_sample_seconds"} else int(raw)
            except ValueError:
                raise HostBudgetUncalibrated(f"阶段标定值无效：{name}") from None
        try:
            budgets.append(budget_type(**values))
        except ValueError as exc:
            raise HostBudgetUncalibrated(str(exc)) from None
    return tuple(budgets)


def presenter_budgets_from_env(env: Mapping[str, str] | None = None):
    """Absent person calibration stays absent; partial calibration is an error."""
    environment = os.environ if env is None else env
    budgets = []
    for kind, prefix in ((PresenterBudget, "DEVICE_PRESENTER_"),
                         (CpuRenderBudget, "DEVICE_PRESENTER_CPU_RENDER_")):
        names = {item.name: prefix + item.name.upper() for item in fields(kind)}
        if not any(environment.get(name, "").strip() for name in names.values()):
            budgets.append(None)
            continue
        missing = [name for name in names.values() if not environment.get(name, "").strip()]
        if missing:
            raise HostBudgetUncalibrated("人物专属预算尚未标定：缺少 " + "、".join(missing))
        try:
            values = {field: (float(environment[name]) if field in {
                "max_duration_seconds", "max_timeout_seconds", "runtime_sample_seconds", "max_pixel_seconds"}
                else int(environment[name])) for field, name in names.items()}
            budgets.append(kind(**values))
        except ValueError as exc:
            raise HostBudgetUncalibrated(f"人物专属标定值无效：{exc}") from exc
    return tuple(budgets)


_HOST_BUDGET_ENV = {
    "max_cpu_percent": "DEVICE_HOST_MAX_CPU_PERCENT",
    "min_available_memory_bytes": "DEVICE_HOST_MIN_AVAILABLE_MEMORY_BYTES",
    "max_swap_used_bytes": "DEVICE_HOST_MAX_SWAP_USED_BYTES",
    "min_free_disk_bytes": "DEVICE_HOST_MIN_FREE_DISK_BYTES",
    "max_disk_io_bytes_per_second": "DEVICE_HOST_MAX_DISK_IO_BYTES_PER_SECOND",
    "max_unit_input_bytes": "DEVICE_HOST_MAX_UNIT_INPUT_BYTES",
}

_INT_HOST_BUDGET_FIELDS = frozenset({
    "min_available_memory_bytes", "max_swap_used_bytes", "min_free_disk_bytes",
    "max_disk_io_bytes_per_second", "max_unit_input_bytes",
})


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise HostBudgetUncalibrated(f"整机许可预算尚未标定：缺少环境变量 {name}")
    return value


def _positive_int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise HostBudgetUncalibrated(f"整机许可预算尚未标定：{name} 不是整数") from exc
    if value <= 0:
        raise HostBudgetUncalibrated(f"整机许可预算尚未标定：{name} 必须为正整数")
    return value


def host_budget_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    cpu_render: CpuRenderBudget | None = None,
    media_script: MediaScriptBudget | None = None,
) -> HostBudget:
    """从显式标定值读出整机预算。

    缺任何一项都直接报未标定，不做兜底默认——猜出来的阈值会让整机许可在
    没有标定的情况下也放行重活，那比不放行更危险。
    """
    source = os.environ if environ is None else environ
    raw = {field: source.get(env_name, "").strip() for field, env_name in _HOST_BUDGET_ENV.items()}
    missing = [env for env, value in zip(_HOST_BUDGET_ENV.values(), raw.values()) if not value]
    if missing:
        raise HostBudgetUncalibrated(
            "整机许可预算尚未标定：缺少 " + "、".join(missing)
        )
    try:
        max_cpu_percent = float(raw["max_cpu_percent"])
    except ValueError as exc:
        raise HostBudgetUncalibrated(
            f"整机许可预算尚未标定：{_HOST_BUDGET_ENV['max_cpu_percent']} 不是数值"
        ) from exc
    if not 0 < max_cpu_percent <= 100:
        raise HostBudgetUncalibrated("整机许可预算尚未标定：CPU 上限必须在 0 与 100 之间")
    numbers = {
        field: _positive_int(_HOST_BUDGET_ENV[field], value)
        for field, value in raw.items()
        if field in _INT_HOST_BUDGET_FIELDS
    }
    return HostBudget(max_cpu_percent=max_cpu_percent, cpu_render=cpu_render, media_script=media_script, **numbers)


#: CPU 成片预算的标定项。与整机预算一样，全部来自这台设备的标定结果。
_CPU_RENDER_ENV = {
    "max_duration_seconds": "DEVICE_CPU_RENDER_MAX_DURATION_SECONDS",
    "max_pixel_seconds": "DEVICE_CPU_RENDER_MAX_PIXEL_SECONDS",
    "max_width_px": "DEVICE_CPU_RENDER_MAX_WIDTH_PX",
    "max_height_px": "DEVICE_CPU_RENDER_MAX_HEIGHT_PX",
    "max_input_bytes": "DEVICE_CPU_RENDER_MAX_INPUT_BYTES",
    "max_clip_count": "DEVICE_CPU_RENDER_MAX_CLIP_COUNT",
    "max_overlay_count": "DEVICE_CPU_RENDER_MAX_OVERLAY_COUNT",
    "max_output_bytes": "DEVICE_CPU_RENDER_MAX_OUTPUT_BYTES",
    "max_threads": "DEVICE_CPU_RENDER_MAX_THREADS",
    "max_timeout_seconds": "DEVICE_CPU_RENDER_MAX_TIMEOUT_SECONDS",
    "additional_memory_bytes": "DEVICE_CPU_RENDER_ADDITIONAL_MEMORY_BYTES",
    "additional_disk_bytes": "DEVICE_CPU_RENDER_ADDITIONAL_DISK_BYTES",
    "max_process_threads": "DEVICE_CPU_RENDER_MAX_PROCESS_THREADS",
}

_FLOAT_CPU_RENDER_FIELDS = frozenset({
    "max_duration_seconds", "max_pixel_seconds", "max_timeout_seconds",
})


def cpu_render_budget_from_env(
    environ: Mapping[str, str] | None = None,
) -> CpuRenderBudget | None:
    """读 CPU 成片预算；一项没标定就整体当作未标定，不做部分兜底。

    部分兜底会让验证者拿到一半真实、一半猜出来的成片上限，比直接拒绝更难
    排查，所以缺任何一项都返回 ``None``，让成片阶段在主机门禁上被拒绝。
    """
    source = os.environ if environ is None else environ
    values: dict[str, float | int] = {}
    missing: list[str] = []
    for field, env_name in _CPU_RENDER_ENV.items():
        raw = source.get(env_name, "").strip()
        if not raw:
            missing.append(env_name)
            continue
        try:
            values[field] = float(raw) if field in _FLOAT_CPU_RENDER_FIELDS else int(raw)
        except ValueError as exc:
            raise HostBudgetUncalibrated(
                f"CPU 成片预算尚未标定：{env_name} 不是数值"
            ) from exc
    if missing:
        raise HostBudgetUncalibrated("CPU 成片预算尚未标定：缺少 " + "、".join(missing))
    try:
        return CpuRenderBudget(**values)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HostBudgetUncalibrated(f"CPU 成片预算无效：{exc}") from exc


def clip_preview_budget_from_env(environ: Mapping[str, str] | None = None) -> CpuRenderBudget | None:
    """Independent measured profile; never borrow the full-movie allowance."""
    source = os.environ if environ is None else environ
    names = {item.name: "DEVICE_CLIP_PREVIEW_" + item.name.upper() for item in fields(CpuRenderBudget)}
    if not any(source.get(name, "").strip() for name in names.values()):
        return None
    missing = [name for name in names.values() if not source.get(name, "").strip()]
    if missing:
        raise HostBudgetUncalibrated("短镜头预览预算尚未标定：缺少 " + "、".join(missing))
    try:
        values = {field: float(source[name]) if field in _FLOAT_CPU_RENDER_FIELDS else int(source[name])
            for field, name in names.items()}
        return CpuRenderBudget(**values)
    except ValueError as exc:
        raise HostBudgetUncalibrated(f"短镜头预览预算无效：{exc}") from exc


def build_host_probes(
    *,
    database_path: str | Path,
    disk_paths: tuple[str | Path, ...],
    budget: HostBudget,
) -> tuple[HostResourceProbe, HostProcessProbe]:
    """真实整机探针：资源采样 + POSIX 进程身份，不提供任何替代实现。"""
    return (
        HostResourceProbe(database_path, disk_paths=disk_paths, budget=budget),
        HostProcessProbe(),
    )


class DeviceHeartbeatRequest(BaseModel):
    """客户端上报的会话活动；身份与工作区一律由服务端判定，不接受自报。"""

    session_id: str = Field(min_length=1, max_length=200)
    playing: bool = False
    recording: bool = False
    editing: bool = False
    interaction: bool = False


def cpu_audio_budget_from_env(env: Mapping[str, str] | None = None):
    environment = os.environ if env is None else env
    names = {item.name: "DEVICE_CPU_AUDIO_" + item.name.upper() for item in fields(CpuAudioBudget)}
    if not any(environment.get(name, "").strip() for name in names.values()):
        return None
    missing = [name for name in names.values() if not environment.get(name, "").strip()]
    if missing:
        raise HostBudgetUncalibrated("CPU 音频预算尚未标定：缺少 " + "、".join(missing))
    try:
        return CpuAudioBudget(**{field: float(environment[name]) if field.endswith("seconds")
            else int(environment[name]) for field, name in names.items()})
    except ValueError as exc:
        raise HostBudgetUncalibrated(str(exc)) from None


def media_script_budget_from_env(env: Mapping[str, str] | None = None) -> MediaScriptBudget | None:
    environment = os.environ if env is None else env
    names = {item.name: "DEVICE_MEDIA_SCRIPT_" + item.name.upper() for item in fields(MediaScriptBudget)}
    if not any(environment.get(name, "").strip() for name in names.values()):
        return None
    missing = [name for name in names.values() if not environment.get(name, "").strip()]
    if missing:
        raise HostBudgetUncalibrated("隔离脚本预算尚未标定：缺少 " + "、".join(missing))
    try:
        return MediaScriptBudget(**{field: float(environment[name]) if field.endswith("seconds")
            else int(environment[name]) for field, name in names.items()})
    except ValueError as exc:
        raise HostBudgetUncalibrated(str(exc)) from None


def observation_budget_from_env(env: Mapping[str, str] | None = None) -> ObservationBudget | None:
    environment = os.environ if env is None else env
    raw = environment.get("TASK_OUTPUT_OBSERVATION_BUDGET_JSON", "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {item.name for item in fields(ObservationBudget)}:
            raise ValueError("观察预算必须包含全部显式校准字段")
        return ObservationBudget(**payload)
    except (TypeError, ValueError) as exc:
        raise HostBudgetUncalibrated(f"本地输出观察预算无效：{exc}") from None
