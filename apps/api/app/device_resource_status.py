"""Public setup facts from the current host's existing resource profile."""
import hashlib
import json
from pathlib import Path
import sys

from app.device_resource_configuration import resolve_device_resources
from app.device_resource_profile import DeviceProfileUnavailable


def preparation_actor_scope(actor, environment):
    identity = {'host_device_id': environment.get('DEVICE_ID', ''),
        'workspace_id': actor.workspace_id, **actor.identity_payload()}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resource_preparation_status(settings, environment, model_catalog, actor):
    configured = bool(settings.device_resource_mode == 'normal'
        and settings.device_resource_profile_path and environment.get('DEVICE_ID'))
    status = {'actor_scope': preparation_actor_scope(actor, environment), 'platform': sys.platform,
        'cpu_preparation_supported': sys.platform == 'darwin', 'preparation_configured': configured,
        'profile_status': 'unprepared', 'revision': None, 'available_operations': []}
    if not settings.device_resource_profile_path or not Path(settings.device_resource_profile_path).is_file():
        return status
    if settings.device_resource_mode != 'normal':
        return {**status, 'profile_status': 'invalid'}
    try:
        profile = resolve_device_resources(settings, environment, model_catalog=model_catalog)
    except DeviceProfileUnavailable:
        return {**status, 'profile_status': 'invalid'}
    return {**status, 'profile_status': 'available', 'revision': profile.revision,
        'available_operations': sorted(name for name in profile.evidence
            if name != 'host' and getattr(profile.budget, name) is not None)}
