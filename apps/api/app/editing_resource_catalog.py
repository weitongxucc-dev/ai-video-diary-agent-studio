"""Read-only manifest for the editing resource library.

The catalog describes navigation categories and concrete resource versions. It
does not own timeline writes: callers must pass a selected resource through the
existing EditProposal/TimelineVersion editing use cases.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.creation.visual_template_catalog import visual_templates
from app.creation.openfilm_transition_catalog import IDENTITY, TEMPLATE_ID
from app.creation.fluent_sticker_catalog import sticker_preview_path

# ``failed`` is a persisted outcome for an attempted import or adapter check;
# it is intentionally distinct from ``planned`` (never attempted) and
# ``catalog_only`` (known resource without a connected write adapter).
ResourceStatus = Literal["connected", "catalog_only", "planned", "failed"]


class ResourcePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    thumbnail_ref: str | None = Field(default=None, max_length=500)


class ResourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1, max_length=500)
    revision: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=120)
    license_spdx: str | None = Field(default=None, max_length=40)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    asset_license: str | None = Field(default=None, max_length=120)
    font_license: str | None = Field(default=None, max_length=120)
    model_license: str | None = Field(default=None, max_length=120)
    service_terms: str | None = Field(default=None, max_length=500)


class EditingResourceCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    status: ResourceStatus
    entrypoints: list[str] = Field(default_factory=list, max_length=20)
    provenance_required: bool = True


class EditingResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    category_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    status: ResourceStatus
    resource_type: str = Field(min_length=1, max_length=80)
    preview: ResourcePreview
    provenance: list[ResourceProvenance] = Field(default_factory=list, max_length=20)
    runtime_requirements: dict[str, object] = Field(default_factory=dict)
    entrypoints: list[str] = Field(default_factory=list, max_length=20)
    editable_via: list[str] = Field(default_factory=lambda: ["EditProposal", "TimelineVersion"], max_length=4)


class EditingResourceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[EditingResourceCategory]
    resources: list[EditingResource]
    provenance_fields: list[str]
    write_boundary: str


_CATEGORIES: tuple[EditingResourceCategory, ...] = (
    EditingResourceCategory(id="materials", label="素材", status="connected",
        entrypoints=["find_materials", "read_material_evidence"]),
    EditingResourceCategory(id="skills", label="技能", status="connected",
        entrypoints=["read_creation_skill"]),
    EditingResourceCategory(id="stickers", label="贴纸", status="connected",
        entrypoints=["preview_typed_edit", "apply_typed_edit"]),
    EditingResourceCategory(id="filters", label="滤镜", status="planned",
        entrypoints=[]),
    EditingResourceCategory(id="effects", label="特效", status="connected",
        entrypoints=["preview_typed_edit", "apply_typed_edit"]),
    EditingResourceCategory(id="transitions", label="转场", status="connected",
        entrypoints=["preview_typed_edit", "apply_typed_edit"]),
    EditingResourceCategory(id="templates", label="模板", status="connected",
        entrypoints=["read_video_templates", "read_visual_template"]),
    EditingResourceCategory(id="subtitles_text", label="字幕/文本", status="connected",
        entrypoints=["read_narration_segments", "read_script_sections", "preview_typed_edit"]),
    EditingResourceCategory(id="voice", label="配音", status="connected",
        entrypoints=["read_voice_profiles"], provenance_required=True),
    EditingResourceCategory(id="music", label="音乐", status="connected",
        entrypoints=["preview_typed_edit", "apply_typed_edit"], provenance_required=True),
    EditingResourceCategory(id="sfx", label="音效", status="connected",
        entrypoints=["preview_typed_edit", "apply_typed_edit"], provenance_required=True),
)


def _local_provenance(*, path: str, license: str = "internal") -> ResourceProvenance:
    return ResourceProvenance(repository="曜核/AI视频日记", revision="main", path=path, license=license)


def _builtin_resources() -> list[EditingResource]:
    resources = [
        EditingResource(
            id="library:materials", category_id="materials", name="工作区素材库", version=1,
            status="connected", resource_type="media_library",
            preview=ResourcePreview(kind="library"),
            provenance=[_local_provenance(path="apps/api/app/materials")],
            runtime_requirements={"workspace_scoped": True},
            entrypoints=["find_materials", "read_material_evidence"],
        ),
        EditingResource(
            id="builtin:stickers", category_id="stickers", name="文本与贴纸叠加层", version=1,
            status="connected", resource_type="overlay", preview=ResourcePreview(kind="overlay"),
            provenance=[_local_provenance(path="apps/api/app/graphic_overlays.py")],
            runtime_requirements={"write_path": "EditProposal/TimelineVersion"},
            entrypoints=["preview_typed_edit", "apply_typed_edit"],
        ),
        EditingResource(
            id="builtin:visual-effects", category_id="effects", name="画面效果", version=1,
            status="connected", resource_type="visual_effects", preview=ResourcePreview(kind="parameterized"),
            provenance=[_local_provenance(path="apps/api/app/visual_effects.py")],
            runtime_requirements={"engine": "ffmpeg", "schema": "VisualEffects"},
            entrypoints=["preview_typed_edit", "apply_typed_edit"],
        ),
        EditingResource(
            id="builtin:dissolve-transition", category_id="transitions", name="叠化转场", version=1,
            status="connected", resource_type="transition", preview=ResourcePreview(kind="parameterized"),
            provenance=[_local_provenance(path="apps/api/app/transitions.py")],
            runtime_requirements={"engine": "ffmpeg", "frame_rate": 30},
            entrypoints=["preview_typed_edit", "apply_typed_edit"],
        ),
        EditingResource(
            id="builtin:fade-in", category_id="effects", name="淡入", version=1,
            status="connected", resource_type="visual_effect_preset", preview=ResourcePreview(kind="parameterized"),
            provenance=[_local_provenance(path="apps/api/app/visual_effects.py")],
            runtime_requirements={"engine": "ffmpeg", "operation": "VisualEffects.fade_in_seconds"},
            entrypoints=["preview_typed_edit", "apply_typed_edit"],
        ),
        EditingResource(
            id="builtin:fade-out", category_id="effects", name="淡出", version=1,
            status="connected", resource_type="visual_effect_preset", preview=ResourcePreview(kind="parameterized"),
            provenance=[_local_provenance(path="apps/api/app/visual_effects.py")],
            runtime_requirements={"engine": "ffmpeg", "operation": "VisualEffects.fade_out_seconds"},
            entrypoints=["preview_typed_edit", "apply_typed_edit"],
        ),
        EditingResource(
            id="builtin:warm-color", category_id="effects", name="暖色调", version=1,
            status="connected", resource_type="visual_effect_preset", preview=ResourcePreview(kind="parameterized"),
            provenance=[_local_provenance(path="apps/api/app/visual_effects.py")],
            runtime_requirements={"engine": "ffmpeg", "operation": "VisualEffects.temperature"},
            entrypoints=["preview_typed_edit", "apply_typed_edit"],
        ),
        EditingResource(
            id="builtin:subtitle-text", category_id="subtitles_text", name="字幕与文本", version=1,
            status="connected", resource_type="subtitle_text", preview=ResourcePreview(kind="overlay"),
            provenance=[_local_provenance(path="apps/api/app/graphic_overlays.py")],
            runtime_requirements={"write_path": "EditProposal/TimelineVersion"},
            entrypoints=["read_narration_segments", "read_script_sections", "preview_typed_edit"],
        ),
        EditingResource(
            id="builtin:voice-profiles", category_id="voice", name="版本化声音档案", version=1,
            status="connected", resource_type="voice_library", preview=ResourcePreview(kind="library"),
            provenance=[_local_provenance(path="apps/api/app/profiles/profile_routes.py")],
            runtime_requirements={"workspace_scoped": True, "authorization_required": True},
            entrypoints=["read_voice_profiles"],
        ),
        EditingResource(
            id="openfilm:sound-glass-pulse", category_id="sfx", name="玻璃脉冲", version=1,
            status="connected", resource_type="sound_effect", preview=ResourcePreview(
                kind="audio", duration_seconds=0.7,
                thumbnail_ref="/api/editing-resources/openfilm:sound-glass-pulse/preview"),
            provenance=[ResourceProvenance(
                repository="https://github.com/openfilm/library", revision="19b26b60c1286b412c01ad64971263ae0e2aef13",
                path="sound/glass-pulse/item.json", license="CC0-1.0", license_spdx="CC0-1.0",
                source_sha256="6ea217ac251cfb1b90ab7f52b7a50ecc92ac827d017df83f1273aad3757fbef2"),
                ResourceProvenance(
                    repository="https://github.com/openfilm/library", revision="19b26b60c1286b412c01ad64971263ae0e2aef13",
                    path="sound/glass-pulse/sound.m4a", license="CC0-1.0", license_spdx="CC0-1.0",
                    source_sha256="c7f8f926893726ae02093ca5d64f5006c3e020b592b5de753bf6de9acaf71a38")],
            runtime_requirements={"engine": "EditAudioTimeline", "source_ref": "/api/editing-resources/openfilm:sound-glass-pulse/preview", "manifest_path": "apps/api/app/resource_assets/openfilm/sound/glass-pulse/item.json", "tags": ["chime", "glass", "cue", "short"]},
            entrypoints=["preview_typed_edit", "apply_typed_edit"],
        ),
        EditingResource(
            id="openfilm:transition-glitch", category_id="transitions", name="信号故障", version=2,
            status="connected", resource_type="css_transition", preview=ResourcePreview(
                kind="html", width=384, height=216, duration_seconds=0.6,
                thumbnail_ref="/api/editing-resources/openfilm:transition-glitch/preview?representation=poster&version=2"),
            provenance=[ResourceProvenance(
                repository="https://github.com/openfilm/library", revision="19b26b60c1286b412c01ad64971263ae0e2aef13",
                path="transition/glitch/item.json", license="CC0-1.0", license_spdx="CC0-1.0",
                source_sha256="43050649d48d4d983216faa3c941efb31761b48a072c97386f559943f61a9475"),
                ResourceProvenance(
                    repository="https://github.com/openfilm/library", revision="19b26b60c1286b412c01ad64971263ae0e2aef13",
                    path="transition/glitch/overlay.css", license="CC0-1.0", license_spdx="CC0-1.0",
                    source_sha256="6063423ad31af44cca1fc88ed0b1bde72beb863125ebbd314265e7cb71335449")],
            runtime_requirements={"engine": "openfilm-html-v1", "adapter": "original-css-overlay",
                "template_id": TEMPLATE_ID, "template_version": 1, "operation_type": "EditGraphicOverlay",
                "duration_seconds": 0.6, "width": 1, "dependencies": [],
                "media_requirement": "已准备 HTML 动画的桌面隔离渲染器；采用不代表成片已渲染",
                "manifest_path": "apps/api/app/resource_assets/openfilm/transition/glitch/item.json"},
            entrypoints=["read_visual_template", "preview_typed_edit", "apply_typed_edit"],
        ),
    ]
    for identity, name, definition in visual_templates():
        if identity == IDENTITY:
            continue  # The original resource remains in transitions, not a duplicate catalog item.
        if identity.startswith('fluent-sticker-'):
            resource_id = 'fluent:' + identity.removeprefix('fluent-')
            resources.append(EditingResource(
                id=resource_id, category_id='stickers', name=name, version=1,
                status='connected', resource_type='svg_sticker',
                preview=ResourcePreview(kind='image', width=definition.width, height=definition.height,
                    duration_seconds=definition.duration_seconds,
                    thumbnail_ref=f'/api/editing-resources/{resource_id}/preview?version=1'),
                provenance=[ResourceProvenance.model_validate(item.model_dump() | {'license_spdx': 'MIT'})
                    for item in definition.provenance],
                runtime_requirements={'engine': definition.engine, 'adapter': 'original-svg-sticker',
                    'template_id': f'builtin_visual_{identity}', 'template_version': 1,
                    'operation_type': 'EditGraphicOverlay', 'dependencies': [],
                    'width': .22, 'x': .75, 'y': .25, 'duration_seconds': definition.duration_seconds,
                    'media_requirement': '成片渲染需要已准备 HTML 图层的桌面隔离渲染器',
                    'attribution': 'Microsoft Fluent Emoji · Copyright (c) Microsoft Corporation. · MIT',
                    'adoption': {'status': 'connected', 'write_boundary': 'EditProposal/TimelineVersion'},
                    'rollback': {'mode': 'timeline-operation-undo', 'deletes_user_data': False}},
                entrypoints=['read_video_templates', 'read_visual_template', 'preview_typed_edit', 'apply_typed_edit'],
            ))
            continue
        resources.append(EditingResource(
            id=f"template:{identity}", category_id="templates", name=name, version=1,
            status="connected", resource_type="visual_template",
            preview=ResourcePreview(kind="html", width=definition.width, height=definition.height,
                duration_seconds=definition.duration_seconds),
            provenance=[ResourceProvenance.model_validate(item.model_dump()) for item in definition.provenance],
            runtime_requirements={"engine": definition.engine, "dependencies": definition.dependencies},
            entrypoints=["read_video_templates", "read_visual_template"],
        ))
    resources.extend(_packaged_skill_resources())
    resources.append(_packaged_music_resource())
    return resources


def _packaged_music_resource() -> EditingResource:
    manifest_path = Path(__file__).resolve().parent / "resource_assets/openfilm/music/minimal-ambient-piano/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = manifest["provenance"]
    preview_ref = f"/api/editing-resources/{manifest['id']}/preview?version={manifest['version']}"
    return EditingResource(
        id=manifest["id"], category_id="music", name=manifest["name"], version=manifest["version"],
        status="connected", resource_type="music",
        preview=ResourcePreview(kind="audio", duration_seconds=manifest["duration_seconds"], thumbnail_ref=preview_ref),
        provenance=[ResourceProvenance(repository=source["repository"], revision=source["revision"],
            path=source["source_path"], license=source["license"], license_spdx=source["license"],
            source_sha256=source["source_sha256"]),
            ResourceProvenance(repository=source["repository"], revision=source["revision"],
                path=source["catalog_path"], license=source["license"], license_spdx=source["license"],
                source_sha256=source["catalog_sha256"])],
        runtime_requirements={
            "engine": "EditAudioTimeline", "runnable": True, "tags": manifest["tags"],
            "preview_ref": preview_ref,
            "manifest_path": "apps/api/app/resource_assets/openfilm/music/minimal-ambient-piano/manifest.json",
            "install_checks": {"mode": "package-time", "checks": ["source and preview SHA-256", "audio decode", "pinned catalog entry"]},
            "adoption": {"status": "requires_task_import", "requires_explicit_edit_authorization": True,
                "import_endpoint": "/api/creation-tasks/{task_id}/audio/resources",
                "import_payload": {"resource_id": manifest["id"], "version": manifest["version"]},
                "result": "读取导入 Job 原回执的 result.source_ref；预览 URL 不是可写入时间线的 source_ref。",
                "entrypoint": "apply_typed_edit", "operation_type": "EditAudioTimeline", "action": "add",
                "track_role": "music", "write_endpoint": "/api/creation-tasks/{task_id}/edits/exact",
                "write_boundary": "沿当前任务授权与 expected_timeline_version_id/expected_timeline_sequence 校验采用。"},
            "rollback": {"mode": "timeline-undo", "endpoint": "/api/creation-tasks/{task_id}/timeline/undo",
                "deletes_user_data": False, "boundary": "携带当前版本与原请求身份撤销；保留导入的任务音频和历史版本。"},
        },
        entrypoints=["preview_typed_edit", "apply_typed_edit"],
    )


def _packaged_skill_resources() -> list[EditingResource]:
    """Project the checked-in Skill manifests into the read-only catalog.

    The JSON manifest remains the single source for imported Skill version,
    provenance, license, runtime restrictions, and evaluation state. The
    catalog only exposes that record for discovery; execution still goes
    through ``read_creation_skill`` and the existing product tools.
    """
    skills_root = Path(__file__).resolve().parent.parent / "agent_runtime" / "skills"
    resources: list[EditingResource] = []
    for manifest_path in sorted(skills_root.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_packaged_skill_manifest(manifest, manifest_path)
        provenance = manifest["provenance"]
        catalog_status = manifest.get("catalog_status", "connected")
        status_reason = manifest.get("status_reason")
        repair_action = manifest.get("repair_action")
        resources.append(EditingResource(
            id=f"skill:{manifest['name']}", category_id="skills", name=manifest["name"],
            version=1, status=catalog_status, resource_type="skill",
            preview=ResourcePreview(kind="method_document"),
            provenance=[ResourceProvenance(
                repository=provenance["repository"], revision=provenance["revision"],
                path=provenance["path"], license=provenance["license"],
                license_spdx=provenance["license_spdx"],
            )],
            runtime_requirements={"manifest_schema_version": manifest["schema_version"],
                                  "manifest_version": manifest["version"], **manifest["runtime"],
                                  "adapter": manifest["entry"]["adapter"],
                                  "dependencies": {"skills": manifest["dependencies"]["skills"],
                                                    "tools": manifest["dependencies"]["tools"],
                                                    "host": manifest.get("host_dependencies", [])},
                                  "capabilities": {"inputs": manifest["inputs"], "outputs": manifest["outputs"],
                                                    "permissions": manifest["permissions"], "dependencies": manifest["dependencies"]},
                                  "install_checks": manifest["install_checks"],
                                  "adoption": manifest["adoption"],
                                  "rollback": manifest["rollback"],
                                  "evaluation": manifest["evaluation"],
                                  **({"status_reason": status_reason} if status_reason else {}),
                                  **({"repair_action": repair_action} if repair_action else {}),
                                  **({"external_capabilities": manifest["capabilities"]}
                                     if isinstance(manifest.get("capabilities"), list) else {})},
            entrypoints=["read_creation_skill"],
        ))
    return resources


def _validate_packaged_skill_manifest(manifest: object, path: Path) -> None:
    """Apply the catalog's install preflight to a packaged Skill manifest."""
    if not isinstance(manifest, dict):
        raise ValueError(f"Skill manifest 不是对象：{path}")
    required = ("schema_version", "name", "version", "entry", "inputs", "outputs", "permissions",
                "dependencies", "runtime", "install_checks", "adoption", "rollback", "provenance", "evaluation")
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError(f"Skill manifest 缺少字段：{','.join(missing)}")
    if manifest["schema_version"] != 1 or not all(isinstance(manifest[key], str) and manifest[key].strip() for key in ("name", "version")):
        raise ValueError("Skill manifest 版本或身份无效")
    entry = manifest["entry"]
    if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ("skill", "adapter")):
        raise ValueError("Skill manifest entry 无效")
    if any(not isinstance(manifest[key], list) for key in ("inputs", "outputs", "permissions")):
        raise ValueError("Skill manifest 能力声明必须是数组")
    dependencies = manifest["dependencies"]
    if (not isinstance(dependencies, dict)
            or not isinstance(dependencies.get("skills"), list)
            or not isinstance(dependencies.get("tools"), list)):
        raise ValueError("Skill manifest dependencies 无效")
    runtime = manifest["runtime"]
    if (not isinstance(runtime, dict) or runtime.get("execution_mode") != "method-only"
            or runtime.get("runnable") is not False
            or any(runtime.get(key) is not False for key in ("network", "shell", "arbitrary_code"))):
        raise ValueError("Skill manifest runtime 必须是不可直接运行的方法文档")
    install = manifest["install_checks"]
    if (not isinstance(install, dict) or install.get("mode") != "package-time"
            or any(install.get(key) is not False for key in ("network", "shell", "arbitrary_code"))
            or not isinstance(install.get("checks"), list) or not install["checks"]):
        raise ValueError("Skill manifest 安装前检查无效")
    adoption = manifest["adoption"]
    if (not isinstance(adoption, dict) or adoption.get("status") != "installed"
            or adoption.get("writes") is not False or adoption.get("requires_explicit_edit_authorization") is not True):
        raise ValueError("Skill manifest 采用边界无效")
    rollback = manifest["rollback"]
    if (not isinstance(rollback, dict) or not isinstance(rollback.get("mode"), str)
            or not isinstance(rollback.get("pre_adoption"), str) or not isinstance(rollback.get("post_adoption"), str)
            or rollback.get("deletes_user_data") is not False):
        raise ValueError("Skill manifest 回滚边界无效")
    provenance = manifest["provenance"]
    if (not isinstance(provenance, dict)
            or not all(isinstance(provenance.get(key), str) and provenance[key].strip()
                       for key in ("repository", "revision", "path", "license", "license_spdx"))):
        raise ValueError("Skill manifest provenance 无效")
    evaluation = manifest["evaluation"]
    if not isinstance(evaluation, dict) or not isinstance(evaluation.get("status"), str) or not isinstance(evaluation.get("evidence"), list):
        raise ValueError("Skill manifest 评测声明无效")
    catalog_status = manifest.get("catalog_status", "connected")
    if catalog_status not in {"connected", "catalog_only", "planned", "failed"}:
        raise ValueError("Skill manifest catalog_status 无效")
    if catalog_status != "connected" and not isinstance(manifest.get("status_reason"), str):
        raise ValueError("非 connected Skill manifest 必须说明状态原因")
    capabilities = manifest.get("capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, list) or not capabilities:
            raise ValueError("Skill manifest capabilities 无效")
        for capability in capabilities:
            if not isinstance(capability, dict) or not all(isinstance(capability.get(key), str) and capability[key].strip()
                                                           for key in ("id", "status", "stability", "label")):
                raise ValueError("Skill manifest capability 身份无效")
            if capability["status"] not in {"connected", "catalog_only", "planned", "failed"}:
                raise ValueError("Skill manifest capability status 无效")
            if not isinstance(capability.get("inputs"), list) or not isinstance(capability.get("outputs"), list):
                raise ValueError("Skill manifest capability 输入输出无效")
            if not isinstance(capability.get("dependencies"), list) or not isinstance(capability.get("risk"), str):
                raise ValueError("Skill manifest capability 依赖或风险无效")
            if capability.get("runnable") is not False:
                raise ValueError("Skill manifest capability 必须明确不可直接运行")
            if capability["status"] != "connected" and not isinstance(capability.get("unavailable_reason"), str):
                raise ValueError("非 connected capability 必须说明不可用原因")


def editing_resource_catalog() -> EditingResourceCatalog:
    """Build the current manifest from checked-in resource declarations."""
    return EditingResourceCatalog(
        categories=[item.model_copy(deep=True) for item in _CATEGORIES],
        resources=_builtin_resources(),
        provenance_fields=["repository", "revision", "path", "license", "license_spdx", "source_sha256", "asset_license",
                           "font_license", "model_license", "service_terms"],
        write_boundary="资源读取和检索是只读操作；采用资源必须经现有 EditProposal/TimelineVersion。",
    )


def resource_preview_path(resource_id: str, representation: str = "media") -> Path | None:
    """Return the checked-in preview media for a catalog resource."""
    if resource_id.startswith('fluent:'):
        return sticker_preview_path(resource_id) if representation == 'media' else None
    root = Path(__file__).resolve().parent / "resource_assets" / "openfilm"
    paths = {
        # Keep the checked-in OpenFilm m4a as the source/provenance asset. The
        # WAV is a tiny local preview derivative because Electron's bundled
        # Chromium does not decode this m4a reliably in an <audio> element.
        ("openfilm:sound-glass-pulse", "media"): root / "sound" / "glass-pulse" / "preview.wav",
        ("openfilm:music-minimal-ambient-piano", "media"): root / "music" / "minimal-ambient-piano" / "preview.wav",
        ("openfilm:transition-glitch", "media"): root / "transition" / "glitch" / "preview.mp4",
        ("openfilm:transition-glitch", "poster"): root / "transition" / "glitch" / "poster.jpg",
    }
    path = paths.get((resource_id, representation))
    return path if path is not None and path.is_file() else None


def search_editing_resources(catalog: EditingResourceCatalog, *, query: str | None = None,
                             category: str | None = None, status: ResourceStatus | None = None,
                             offset: int = 0, limit: int = 50) -> tuple[list[EditingResource], int]:
    if offset < 0 or limit < 1 or limit > 100:
        raise ValueError("资源分页范围无效")
    needle = query.strip().casefold() if query else None
    items = [item for item in catalog.resources if
        (not category or item.category_id == category) and
        (not status or item.status == status) and
        (not needle or needle in f"{item.id} {item.name} {item.resource_type}".casefold())]
    return items[offset:offset + limit], len(items)
