"""Read a host-owned resource profile without sampling or granting resources.

Profiles live beside the desktop device identity, not in a workspace. Limits
remain the existing execution contracts. A bound measurement record is evidence
that was recorded, never proof of production capacity or current admission.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
from pathlib import Path
import re
from collections.abc import Callable, Mapping
from typing import get_args, get_type_hints

from app.host_memory import MemoryPolicy
from app.host_resource_probes import (
    HostBudget, CpuRenderBudget, CpuAudioBudget, NarrationBudget, MontageBudget,
    PresenterBudget, SceneWindowBudget, MediaScriptBudget, NativeCommandBudget,
    ObservationBudget, QualityBudget, CoverBudget, MaterialVisualQueryBudget,
    RuntimeCpuPolicy, PacketCopyBudget,
)


PROFILE_FILENAME = "device-resource-profile.json"
_BUDGET_TYPES = {
    "cpu_render": CpuRenderBudget, "cpu_audio": CpuAudioBudget,
    "narration": NarrationBudget, "montage": MontageBudget,
    "presenter": PresenterBudget, "presenter_cpu_render": CpuRenderBudget,
    "scene_window": SceneWindowBudget, "media_script": MediaScriptBudget,
    "native_command": NativeCommandBudget, "observation": ObservationBudget,
    "quality": QualityBudget, "cover": CoverBudget,
    "material_visual_query": MaterialVisualQueryBudget, "clip_preview": CpuRenderBudget,
    "packet_copy": PacketCopyBudget,
}


class DeviceProfileUnavailable(RuntimeError):
    """The normal device profile needs preparation or a valid replacement."""


@dataclass(frozen=True)
class DeviceProfileIdentity:
    device_id: str
    platform: str
    architecture: str
    physical_memory_bytes: int

    def __post_init__(self):
        if (not all(isinstance(value, str) and value.strip() for value in
                    (self.device_id, self.platform, self.architecture))
                or type(self.physical_memory_bytes) is not int or self.physical_memory_bytes <= 0):
            raise ValueError("Device profile needs a real device/platform/memory identity")


@dataclass(frozen=True)
class ResolvedDeviceProfile:
    budget: HostBudget
    profile_id: str
    revision: int | None
    digest: str
    source_kind: str
    evidence: dict[str, dict]
    unavailable: dict[str, str]


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _object(value, allowed, *, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise ValueError("Resource profile has missing or unsupported fields")
    return value


def _json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate resource profile field")
        result[name] = value
    return result


def _nonfinite(_value):
    raise ValueError("Resource profile cannot contain non-finite numbers")


def _read_json(raw):
    return json.loads(raw, object_pairs_hook=_json_object, parse_constant=_nonfinite)


def _budget(kind, payload):
    values = dict(_object(payload, (field.name for field in fields(kind))))
    # JSON has no dataclass decoder. Preserve declared primitive types before the
    # existing constructor validates ranges and relationships (bool is not int).
    annotations = get_type_hints(kind)
    for name, value in values.items():
        expected = annotations[name]
        alternatives = get_args(expected)
        if alternatives and type(None) in alternatives:
            if value is None:
                continue
            expected = next(item for item in alternatives if item is not type(None))
        if (expected is int and type(value) is not int
                or expected is float and type(value) not in (int, float)
                or expected is str and not isinstance(value, str)):
            raise ValueError(f"Resource profile field {name} has an invalid type")
    if values.get("running_cpu_policy") is not None:
        values["running_cpu_policy"] = _budget(RuntimeCpuPolicy, values["running_cpu_policy"])
    if kind is SceneWindowBudget and "allowed_source_codecs" in values:
        if not isinstance(values["allowed_source_codecs"], list):
            raise ValueError("Resource profile codecs must be an array")
        values["allowed_source_codecs"] = tuple(values["allowed_source_codecs"])
    return kind(**values)


def host_budget_from_payload(payload):
    """Decode an already sealed host declaration using its existing contracts."""
    values = dict(payload)
    for name, kind in _BUDGET_TYPES.items():
        if values.get(name) is not None:
            values[name] = _budget(kind, values[name])
    if values.get('memory_policy') is not None:
        values['memory_policy'] = _budget(MemoryPolicy, values['memory_policy'])
    return _budget(HostBudget, values)


def _measurement(profile_path, reference, *, device, backend_identity, budget_payload):
    """Bind a local receipt to exactly these facts; do not infer wider validity."""
    _object(reference, ("record_id", "sha256"), required=("record_id", "sha256"))
    record_id, expected = reference["record_id"], reference["sha256"]
    if (not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", record_id)
            or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise ValueError("Resource measurement reference is invalid")
    receipt_path = profile_path.parent / "resource-profile-evidence" / f"{record_id}.json"
    raw = receipt_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Resource measurement receipt changed")
    receipt = _read_json(raw)
    _object(receipt, ("format_version", "device", "backend_identity", "budget_digest", "method",
                      "input_range", "observations"),
        required=("format_version", "device", "backend_identity", "budget_digest", "method",
                  "input_range", "observations"))
    if (type(receipt["format_version"]) is not int or receipt["format_version"] != 1
            or receipt["device"] != asdict(device)
            or receipt["backend_identity"] != backend_identity
            or receipt["budget_digest"] != _digest(budget_payload)
            or not isinstance(receipt["method"], str) or not receipt["method"].strip()
            or not isinstance(receipt["input_range"], dict) or not receipt["input_range"]
            or not isinstance(receipt["observations"], dict) or not receipt["observations"]):
        raise ValueError("Resource measurement does not bind the selected device/backend/budget")
    if budget_payload.get('input_scope') == 'single_call_prepared_v1':
        observation = receipt['observations']
        if (budget_payload['preparation_backend_identity'] != backend_identity
                or budget_payload['preparation_receipt_sha256'] != _digest(observation)
                or budget_payload['preparation_shape_digest'] != receipt['input_range'].get('shape_digest')
                or observation.get('backends', {}).get('narration') != backend_identity
                or observation.get('inputs', {}).get('shape_digest') != budget_payload['preparation_shape_digest']
                or not observation.get('request_id') or type(observation.get('generation')) is not int
                or not observation.get('model_observed_resources')):
            raise ValueError('Prepared narration does not bind its original model measurement')
    return {"status": "recorded", "reference": record_id, "sha256": expected,
            "verification_status": "unknown"}


def load_device_resource_profile(path: str | Path, *, device: DeviceProfileIdentity,
                                 backend_identities: Mapping[str, str] | Callable[[str, object], str | None]) -> ResolvedDeviceProfile:
    """Resolve a normal host-owned profile; never read environment overrides.

    The desktop supplies ``userData/device-resource-profile.json``. CLI callers
    must explicitly locate that same device configuration owner. A changed or
    absent backend disables only its operation, while a changed device requires
    a new applicable profile. The caller still performs live resource admission.
    """
    path = Path(path)
    if not path.is_absolute():
        raise DeviceProfileUnavailable("Device resource profile path must be absolute")
    try:
        document = _read_json(path.read_bytes())
        _object(document, ("format_version", "profile_id", "revision", "device", "budget", "backends", "evidence"),
            required=("format_version", "profile_id", "revision", "device", "budget", "backends"))
        if (type(document["format_version"]) is not int or document["format_version"] != 1
                or type(document["revision"]) is not int or document["revision"] < 0
                or not isinstance(document["profile_id"], str) or not document["profile_id"].strip()):
            raise ValueError("Resource profile identity or version is invalid")
        identity = DeviceProfileIdentity(**_object(document["device"],
            (field.name for field in fields(DeviceProfileIdentity))))
        if identity != device:
            raise ValueError("Resource profile belongs to a different device configuration")
        payload = dict(_object(document["budget"], (field.name for field in fields(HostBudget))))
        backends = _object(document["backends"], _BUDGET_TYPES)
        evidence_refs = _object(document.get("evidence", {}), {"host", *_BUDGET_TYPES})
        evidence, unavailable = {}, {}
        values = {key: value for key, value in payload.items() if key not in _BUDGET_TYPES}
        if values.get("memory_policy") is not None:
            values["memory_policy"] = _budget(MemoryPolicy, values["memory_policy"])
        # A retired swap ceiling remains optional diagnostic data, not a gate.
        values.setdefault("max_swap_used_bytes", None)
        host_payload = {key: value for key, value in payload.items() if key not in _BUDGET_TYPES}
        evidence["host"] = (_measurement(path, evidence_refs["host"], device=device,
            backend_identity=None, budget_payload=host_payload) if "host" in evidence_refs else
            {"status": "declared", "verification_status": "unknown"})
        for name, kind in _BUDGET_TYPES.items():
            if payload.get(name) is None:
                if name in backends or name in evidence_refs:
                    raise ValueError("Resource profile has metadata without its operation budget")
                continue
            configured_identity = backends.get(name)
            if not isinstance(configured_identity, str) or not configured_identity.strip():
                raise ValueError("Operation budget needs its actual backend identity")
            operation_budget = _budget(kind, payload[name])
            if name == "packet_copy" and operation_budget.backend_identity != configured_identity:
                raise ValueError("Packet-copy policy and configured backend identity disagree")
            if name == 'narration' and operation_budget.input_scope == 'single_call_preparation_v1':
                unavailable[name] = 'model_preparation_not_completed'
                continue
            if (name == 'narration' and operation_budget.input_scope == 'single_call_prepared_v1'
                    and name not in evidence_refs):
                unavailable[name] = 'model_preparation_receipt_missing'
                continue
            current_identity = (backend_identities(name, operation_budget) if callable(backend_identities)
                                else backend_identities.get(name))
            if configured_identity != current_identity:
                unavailable[name] = "backend_identity_changed_or_unavailable"
                continue
            values[name] = operation_budget
            evidence[name] = (_measurement(path, evidence_refs[name], device=device,
                backend_identity=configured_identity, budget_payload=payload[name]) if name in evidence_refs else
                {"status": "declared", "verification_status": "unknown"})
        budget = _budget(HostBudget, values)
        return ResolvedDeviceProfile(budget, document["profile_id"], document["revision"],
            _digest(document), "device_profile", evidence, unavailable)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        # This failure can reach an API response; do not expose device paths.
        detail = type(exc).__name__ if isinstance(exc, OSError) else str(exc)
        raise DeviceProfileUnavailable(f"Device resource profile unavailable: {detail}") from exc


def resolve_experimental_budget(budget: HostBudget, *, experiment_id: str) -> ResolvedDeviceProfile:
    """An explicit developer call, never an implicit fallback for normal setup."""
    if not isinstance(budget, HostBudget) or not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("Experimental resource configuration needs its budget and experiment identity")
    names = ("host", *(name for name in _BUDGET_TYPES if getattr(budget, name) is not None))
    return ResolvedDeviceProfile(budget, experiment_id, None, _digest(asdict(budget)), "explicit_experiment",
        {name: {"status": "declared", "verification_status": "unknown"} for name in names}, {})
