"""Bridge checked-in editing catalog entries into durable resource manifests.

The editing catalog remains a read model. This adapter gives the existing
resource governance store one deterministic manifest shape for each catalog
version without introducing another persistence owner.
"""
from __future__ import annotations

from dataclasses import asdict

from app.editing_resource_catalog import EditingResource
from app.materials.resource_governance import (
    ResourceLicense,
    ResourceManifest,
    manifest_digest,
)


_KIND_BY_TYPE = {
    "media_library": "material",
    "visual_template": "template",
    "skill": "skill",
    "sound_effect": "audio",
    "css_transition": "transition",
}
_STATE_BY_STATUS = {"connected": "connected", "catalog_only": "catalog_only", "planned": "planned", "failed": "candidate"}
_LICENSE_LAYER = {"asset": "asset", "font": "font", "model": "model", "service": "service"}


def resource_manifest_from_catalog(resource: EditingResource, *, workspace_id: str) -> ResourceManifest:
    """Convert one catalog version into the shared durable manifest contract."""
    if not workspace_id.strip():
        raise ValueError("资源 manifest 必须绑定工作区")
    provenance = [item.model_dump(mode="json") for item in resource.provenance]
    primary = provenance[0] if provenance else {}
    source_revision = {
        "repository": str(primary.get("repository", "catalog")),
        "revision": str(primary.get("revision", "unknown")),
    }
    metadata = {
        "catalog_status": resource.status,
        "category_id": resource.category_id,
        "resource_type": resource.resource_type,
        "preview": resource.preview.model_dump(mode="json"),
        "runtime_requirements": resource.runtime_requirements,
        "entrypoints": resource.entrypoints,
        "editable_via": resource.editable_via,
        "provenance": provenance,
        "adapter_status": "connected" if resource.status == "connected" else resource.status,
    }
    licenses_by_key = {}
    for item in provenance:
        layer = _LICENSE_LAYER.get(item.get("asset_license") or item.get("font_license")
                                   or item.get("model_license") or item.get("service_terms") or "asset", "asset")
        identifier = str(item.get("license_spdx") or item.get("license") or "unknown")
        key = (layer, identifier)
        existing = licenses_by_key.get(key)
        if existing is None:
            licenses_by_key[key] = ResourceLicense(
                id=f"{resource.id}:license:{len(licenses_by_key)}", layer=layer,
                identifier=identifier, source_ref=str(item.get("path", "")),
                terms_ref=str(item.get("service_terms", "")),
                metadata={key: value for key, value in item.items()
                          if key not in {"license", "license_spdx", "path", "service_terms"}},
            )
        elif item.get("path"):
            license_metadata = {**existing.metadata, "additional_source_refs": [
                *existing.metadata.get("additional_source_refs", []), item["path"]]}
            licenses_by_key[key] = ResourceLicense(
                id=existing.id, layer=existing.layer, identifier=existing.identifier,
                source_ref=existing.source_ref, terms_ref=existing.terms_ref, metadata=license_metadata,
            )
    licenses = tuple(licenses_by_key.values())
    digest = manifest_digest(
        resource_id=resource.id,
        resource_version=str(resource.version),
        source_revision=source_revision,
        metadata=metadata,
    )
    return ResourceManifest(
        id=f"catalog:{resource.id}:v{resource.version}",
        workspace_id=workspace_id,
        resource_id=resource.id,
        resource_kind=_KIND_BY_TYPE.get(resource.resource_type, "service"),
        resource_version=str(resource.version),
        state=_STATE_BY_STATUS[resource.status],
        source_revision=source_revision,
        digest=digest,
        licenses=licenses,
        metadata=metadata,
    )


def persist_catalog_resource(resource, *, workspace_id: str, governance):
    """Persist a catalog projection through the existing governance owner."""
    return governance.save_manifest(resource_manifest_from_catalog(resource, workspace_id=workspace_id))
