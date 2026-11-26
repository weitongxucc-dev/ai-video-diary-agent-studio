"""Saved canvas and device-profile facts for the production preview."""
from pathlib import Path
from stat import S_ISREG

from app.canvas import canvas_size
from app.device_resource_configuration import backend_identity, current_device_identity
from app.device_resource_profile import DeviceProfileUnavailable, load_device_resource_profile
from app.montage import creation_task_montage_plan_from_payload


def render_target(repository, task, plan, *, connection):
    """Use the same saved montage as the renderer when one has been adopted."""
    planned_size = canvas_size(plan.aspect_ratio)
    if not task.montage_plan_id:
        return planned_size, None, None
    try:
        payload = repository.get_creation_task_montage_plan(
            task.workspace_id, task.id, connection=connection)
        montage = creation_task_montage_plan_from_payload(payload)
    except (KeyError, ValueError):
        return planned_size, None, None
    if (montage.id != task.montage_plan_id or montage.workspace_id != task.workspace_id
            or montage.task_id != task.id or montage.script_id != task.confirmed_script_id):
        return planned_size, None, None
    size = canvas_size(montage.aspect_ratio)
    presenter = bool(montage.presenter_share_target or any(clip.person_render_id for clip in montage.clips))
    if (montage.status not in {"ready", "rendering", "rendered", "failed"}
            or not montage.clips or any(clip.needs_confirmation for clip in montage.clips)
            or presenter != bool(plan.presenter_share_target) or (presenter and plan.source_media is not None)):
        return size, None, montage
    return size, "presenter_cpu_render" if presenter else "cpu_render", montage


def source_audio_bytes(repository, task, source, *, audio, actor, connection):
    """Stat the registered original through the same source reader used at sealing."""
    if actor is None or actor.workspace_id != task.workspace_id or task.owner_user_id != (actor.user_id or actor.member_id):
        return None
    try:
        asset = repository.get_asset(task.workspace_id, source["asset_id"], connection=connection)
        if asset.updated_at.isoformat() != source["asset_updated_at"]:
            return None
        path = audio.source(task.workspace_id, task.id, source["source_ref"], actor=actor, connection=connection)
        observed = path.stat()
        if (S_ISREG(observed.st_mode) and observed.st_size > 0
                and repository.material_source_files.material_copy_revision(observed) == source["file_signature"]):
            return observed.st_size
    except (KeyError, ValueError, OSError):
        return None
    return None


def saved_render_capacity(settings, environment, operation):
    """Verify the applicable saved limit without resolving other adapters or live capacity."""
    if settings.device_resource_mode != "normal":
        return {"status": "unknown"}
    location = settings.device_resource_profile_path
    if not location:
        return {"status": "missing"}
    path = Path(location)
    try:
        path.stat()
        device = current_device_identity(environment)
        profile = load_device_resource_profile(path, device=device, backend_identities=lambda name, budget:
            backend_identity(name, budget, settings=settings) if name == operation else None)
    except FileNotFoundError:
        return {"status": "missing"}
    except DeviceProfileUnavailable as exc:
        cause = exc.__cause__
        if isinstance(cause, FileNotFoundError) and cause.filename == str(path):
            return {"status": "missing"}
        if isinstance(cause, ValueError) and str(cause) == "Resource profile belongs to a different device configuration":
            return {"status": "stale"}
        return {"status": "unknown"}
    except OSError:
        return {"status": "unknown"}
    if profile.unavailable.get(operation) == "backend_identity_changed_or_unavailable":
        return {"status": "stale"}
    if profile.evidence.get(operation, {}).get("status") != "recorded":
        return {"status": "unknown"}
    budget = getattr(profile.budget, operation)
    if operation == "cpu_audio":
        return {"status": "verified", "max_input_bytes": budget.max_input_bytes}
    return {"status": "verified", "max_width_px": budget.max_width_px, "max_height_px": budget.max_height_px}
