"""Public setup facts from the current host's existing resource profile."""
import hashlib
import json
import sys
from pathlib import Path

from app.device_paths import device_lock_path
from app.device_resource_configuration import resolve_device_resources
from app.device_resource_profile import DeviceProfileUnavailable
from app.device_scheduler import production_device_blocker

PREPARATION_ENDPOINT = '/api/device/resource-preparations'


def preparation_error_detail(error: object) -> dict[str, object] | None:
    """Project a device-profile blocker into an actionable public HTTP detail.

    The original message remains available as ``cause``.  This projection only
    applies to the media boundary, where callers need to know which existing
    preparation and status endpoints can recover the request.
    """
    cause = str(error)
    if '设备资源档' not in cause and '整机许可预算' not in cause:
        return None
    profile_blocked = '设备资源档' in cause
    return {
        'code': 'device_profile_unprepared' if profile_blocked else 'host_budget_unavailable',
        'message': ('设备资源档尚未准备，媒体任务未创建。' if profile_blocked
                    else '整机许可预算尚未就绪，媒体任务未创建。'),
        'cause': cause,
        'status_endpoint': PREPARATION_ENDPOINT,
        'prepare_endpoint': PREPARATION_ENDPOINT,
        'repair_action': ('先读取设备准备状态，提交对应设备准备请求并完成 finalize；CLI 需使用同一 DEVICE_ID 和 DEVICE_RESOURCE_PROFILE_PATH。'
                          if profile_blocked else '先读取设备准备状态并完成整机许可准备，再重新提交媒体任务。'),
    }


def preparation_actor_scope(actor, environment):
    identity = {'host_device_id': environment.get('DEVICE_ID', ''),
        'workspace_id': actor.workspace_id, **actor.identity_payload()}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resource_preparation_status(settings, environment, model_catalog, actor, *, runtime_error=None):
    configured = bool(settings.device_resource_mode == 'normal'
        and settings.device_resource_profile_path and environment.get('DEVICE_ID'))
    status = {'actor_scope': preparation_actor_scope(actor, environment), 'platform': sys.platform,
        'cpu_preparation_supported': sys.platform == 'darwin', 'preparation_configured': configured,
        'production_blocker': production_device_blocker(Path(settings.database_path), device_lock_path(), runtime_error),
        'profile_status': 'unprepared', 'revision': None, 'available_operations': [],
        'media_execution_ready': False, 'status_endpoint': PREPARATION_ENDPOINT,
        'prepare_endpoint': PREPARATION_ENDPOINT,
        'message': '设备资源档尚未准备，媒体任务暂不可提交。',
        'repair_action': '先读取设备准备状态，提交设备准备请求并完成 finalize。'}
    if not settings.device_resource_profile_path or not Path(settings.device_resource_profile_path).is_file():
        return status
    if settings.device_resource_mode != 'normal':
        return {**status, 'profile_status': 'invalid'}
    try:
        profile = resolve_device_resources(settings, environment, model_catalog=model_catalog)
    except DeviceProfileUnavailable:
        return {**status, 'profile_status': 'invalid',
            'message': '设备资源档无法核验，媒体任务暂不可提交。',
            'repair_action': '重新提交设备准备请求生成当前设备档，再完成 finalize。'}
    available_operations = sorted(name for name in profile.evidence
        if name != 'host' and getattr(profile.budget, name) is not None)
    blocker = status['production_blocker']
    ready = 'cpu_render' in available_operations and blocker is None
    return {**status, 'profile_status': 'available', 'revision': profile.revision,
        'available_operations': available_operations, 'media_execution_ready': ready,
        'message': '设备资源档已就绪，可以提交媒体任务。' if ready
            else '设备资源档已读取，但整机许可尚未就绪，媒体任务暂不可提交。',
        'repair_action': None if ready else '先收束当前设备许可或完成资源准备，再重新提交媒体任务。'}
