import pytest

from app.editing_resource_catalog import editing_resource_catalog
from app.materials.resource_catalog_bridge import resource_manifest_from_catalog, persist_catalog_resource
from app.materials.resource_governance import ResourceManifestConflict, ResourceUsageRecord
from app.production import ProductionRepository


def test_catalog_version_and_adapter_status_round_trip_through_shared_governance(tmp_path):
    repo = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    catalog = editing_resource_catalog()
    connected = next(item for item in catalog.resources if item.id == "openfilm:sound-glass-pulse")
    planned = next(item for item in catalog.resources if item.id == "openfilm:transition-glitch")
    saved = persist_catalog_resource(connected, workspace_id="workspace-a", governance=repo.resource_governance)
    assert saved.resource_version == "1"
    assert saved.state == "connected"
    assert saved.metadata["catalog_status"] == "connected"
    assert saved.metadata["adapter_status"] == "connected"
    assert saved.source_revision["revision"] == connected.provenance[0].revision
    assert saved.licenses[0].identifier == "CC0-1.0"
    planned_saved = persist_catalog_resource(planned, workspace_id="workspace-a", governance=repo.resource_governance)
    assert planned_saved.state == "catalog_only"
    assert planned_saved.metadata["catalog_status"] == "catalog_only"
    assert repo.resource_governance.get_manifest("workspace-a", saved.id).digest == saved.digest


def test_catalog_only_and_planned_statuses_cannot_become_executable_usage(tmp_path):
    repo = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    catalog = editing_resource_catalog()
    music = next(item for item in catalog.resources if item.category_id == "music") if any(
        item.category_id == "music" for item in catalog.resources) else None
    transition = next(item for item in catalog.resources if item.id == "openfilm:transition-glitch")
    assert music is None or music.status == "catalog_only"
    saved = persist_catalog_resource(transition, workspace_id="workspace-a", governance=repo.resource_governance)
    assert saved.state == "catalog_only"
    with pytest.raises(ValueError, match="不能进入任务"):
        repo.resource_governance.record_usage(ResourceUsageRecord(
            "usage-catalog-only", "workspace-a", saved.id, saved.resource_version, "adopted", task_id="task"))


def test_catalog_manifest_workspace_isolation_and_version_lock(tmp_path):
    repo = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    resource = next(item for item in editing_resource_catalog().resources if item.id == "openfilm:sound-glass-pulse")
    saved = persist_catalog_resource(resource, workspace_id="workspace-a", governance=repo.resource_governance)
    with pytest.raises(KeyError):
        repo.resource_governance.get_manifest("workspace-b", saved.id)
    changed = resource.model_copy(update={"runtime_requirements": {**resource.runtime_requirements, "changed": True}})
    with pytest.raises(ResourceManifestConflict):
        persist_catalog_resource(changed, workspace_id="workspace-a", governance=repo.resource_governance)
