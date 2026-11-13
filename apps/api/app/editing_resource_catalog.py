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

ResourceStatus = Literal["connected", "catalog_only", "planned"]


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
    EditingResourceCategory(id="music", label="音乐", status="catalog_only",
        entrypoints=[], provenance_required=True),
    EditingResourceCategory(id="sfx", label="音效", status="catalog_only",
        entrypoints=[], provenance_required=True),
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
    ]
    for identity, name, definition in visual_templates():
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
    return resources


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
        resources.append(EditingResource(
            id=f"skill:{manifest['name']}", category_id="skills", name=manifest["name"],
            version=1, status="connected", resource_type="skill",
            preview=ResourcePreview(kind="method_document"),
            provenance=[ResourceProvenance(
                repository=provenance["repository"], revision=provenance["revision"],
                path=provenance["path"], license=provenance["license"],
                license_spdx=provenance["license_spdx"],
            )],
            runtime_requirements={"manifest_schema_version": manifest["schema_version"],
                                  "manifest_version": manifest["version"], **manifest["runtime"],
                                  "adapter": manifest["entry"]["adapter"],
                                  "capabilities": {"inputs": manifest["inputs"], "outputs": manifest["outputs"],
                                                    "permissions": manifest["permissions"], "dependencies": manifest["dependencies"]},
                                  "install_checks": manifest["install_checks"],
                                  "adoption": manifest["adoption"],
                                  "rollback": manifest["rollback"],
                                  "evaluation": manifest["evaluation"]},
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
    if provenance["license_spdx"] not in {"MIT", "Apache-2.0", "CC0-1.0"}:
        raise ValueError("Skill manifest 许可不在可导入范围")
    evaluation = manifest["evaluation"]
    if not isinstance(evaluation, dict) or not isinstance(evaluation.get("status"), str) or not isinstance(evaluation.get("evidence"), list):
        raise ValueError("Skill manifest 评测声明无效")


def editing_resource_catalog() -> EditingResourceCatalog:
    """Build the current manifest from checked-in resource declarations."""
    return EditingResourceCatalog(
        categories=[item.model_copy(deep=True) for item in _CATEGORIES],
        resources=_builtin_resources(),
        provenance_fields=["repository", "revision", "path", "license", "license_spdx", "source_sha256", "asset_license",
                           "font_license", "model_license", "service_terms"],
        write_boundary="资源读取和检索是只读操作；采用资源必须经现有 EditProposal/TimelineVersion。",
    )


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
