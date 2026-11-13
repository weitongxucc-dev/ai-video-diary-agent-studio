from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.materials.resource_governance import (
    ResourceGovernanceStore,
    ResourceLicense,
    ResourceManifest,
    ResourceManifestConflict,
    ResourceUsageRecord,
    manifest_digest,
)
from app.production import ProductionRepository


def make_store(tmp_path: Path) -> ResourceGovernanceStore:
    repo = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    return repo.resource_governance


def manifest(state: str = "connected") -> ResourceManifest:
    source_revision = {"repository": "https://example.invalid/resource", "revision": "abc123"}
    metadata = {"path": "templates/title.html", "sha256": "f" * 64}
    return ResourceManifest(
        id="manifest-title-v1", workspace_id="ws-resource", resource_id="title", resource_kind="template",
        resource_version="1", state=state, source_revision=source_revision,
        digest=manifest_digest(resource_id="title", resource_version="1", source_revision=source_revision, metadata=metadata),
        metadata=metadata,
        licenses=(
            ResourceLicense("license-asset", "asset", "MIT", source_ref="LICENSE", terms_ref="notice://title"),
            ResourceLicense("license-font", "font", "OFL-1.1", source_ref="font/OFL.txt"),
            ResourceLicense("license-model", "model", "provider-model-terms", terms_ref="terms://model"),
            ResourceLicense("license-service", "service", "provider-api-terms", terms_ref="terms://service"),
        ),
    )


def test_manifest_locks_version_and_keeps_layered_licenses(tmp_path: Path):
    store = make_store(tmp_path)
    saved = store.save_manifest(manifest())
    assert [item.layer for item in saved.licenses] == ["asset", "font", "model", "service"]
    assert store.get_manifest("ws-resource", saved.id).digest == saved.digest
    changed_metadata = {**saved.metadata, "sha256": "e" * 64}
    changed_digest = manifest_digest(resource_id=saved.resource_id, resource_version=saved.resource_version,
                                     source_revision=saved.source_revision, metadata=changed_metadata)
    with pytest.raises(ResourceManifestConflict):
        store.save_manifest(saved.__class__(**{**saved.__dict__, "metadata": changed_metadata, "digest": changed_digest}))


@pytest.mark.parametrize("state", ["candidate", "catalog_only", "planned"])
def test_non_connected_states_cannot_be_adopted(tmp_path: Path, state: str):
    store = make_store(tmp_path)
    item = manifest(state)
    store.save_manifest(item)
    with pytest.raises(ValueError, match="不能进入任务"):
        store.record_usage(ResourceUsageRecord(
            "usage-adopt", item.workspace_id, item.id, item.resource_version, "adopted",
            task_id="task-1",
        ))
    candidate = store.record_usage(ResourceUsageRecord(
        "usage-candidate", item.workspace_id, item.id, item.resource_version, "candidate",
    ))
    assert candidate.stage == "candidate"


@pytest.mark.parametrize(
    ("stage", "kwargs", "message"),
    [
        ("task_input", {}, "task_id"),
        ("timeline", {"task_id": "task-1"}, "timeline_version_id"),
        ("render", {"task_id": "task-1", "timeline_version_id": "timeline-1"}, "artifact_ref"),
        ("output_observation", {"task_id": "task-1", "timeline_version_id": "timeline-1", "artifact_ref": "artifact-1"}, "observation_ref"),
    ],
)
def test_executable_usage_requires_its_owner_binding(tmp_path: Path, stage: str, kwargs: dict[str, str], message: str):
    store = make_store(tmp_path)
    item = manifest()
    store.save_manifest(item)
    with pytest.raises(ValueError, match=message):
        store.record_usage(ResourceUsageRecord(
            f"usage-missing-{stage}", item.workspace_id, item.id, item.resource_version, stage, **kwargs,
        ))


def test_task_timeline_render_and_observation_provenance_round_trip(tmp_path: Path):
    store = make_store(tmp_path)
    item = manifest()
    store.save_manifest(item)
    for stage, kwargs in (
        ("adopted", {"task_id": "task-1"}),
        ("task_input", {"task_id": "task-1"}),
        ("timeline", {"task_id": "task-1", "timeline_version_id": "timeline-2"}),
        ("render", {"task_id": "task-1", "timeline_version_id": "timeline-2", "artifact_ref": "artifacts/task-1/final.mp4"}),
        ("output_observation", {"task_id": "task-1", "timeline_version_id": "timeline-2", "artifact_ref": "artifacts/task-1/final.mp4", "observation_ref": "observation-1"}),
    ):
        record = ResourceUsageRecord(
            f"usage-{stage}", item.workspace_id, item.id, item.resource_version, stage, **kwargs,
        )
        store.record_usage(record)
        store.record_usage(record)
    rows = store.provenance_for_task("ws-resource", "task-1", timeline_version_id="timeline-2")
    assert [row["usage"]["stage"] for row in rows] == ["adopted", "task_input", "timeline", "render", "output_observation"]
    assert rows[-1]["manifest"]["licenses"][-1]["layer"] == "service"


def test_material_consumer_edges_keep_deidentified_trace_fields(tmp_path: Path):
    store = make_store(tmp_path)
    asset = SimpleNamespace(
        id="asset-one", file_name="one.mp4", media_type="video",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    adopted = store.record_material_usage(
        workspace_id="ws-resource", asset=asset, segment_id="segment-one", stage="adopted",
        task_id="task-one", trace={"assistant_turn_id": "turn-one", "request_id": "request-one", "secret": "/private/path"},
    )
    rendered = store.record_material_usage(
        workspace_id="ws-resource", asset=asset, segment_id="segment-one", stage="render",
        task_id="task-one", timeline_version_id="timeline-one", artifact_ref="artifact-one",
        trace={"job_id": "job-one"},
    )
    observed = store.record_material_usage(
        workspace_id="ws-resource", asset=asset, segment_id="segment-one", stage="output_observation",
        task_id="task-one", timeline_version_id="timeline-one", artifact_ref="artifact-one",
        observation_ref="observation-one", trace={"job_id": "job-one", "output_id": "artifact-one"},
    )
    rows = store.provenance_for_task("ws-resource", "task-one", timeline_version_id="timeline-one")
    assert [item["usage"]["stage"] for item in rows] == ["adopted", "render", "output_observation"]
    assert rows[0]["usage"]["metadata"]["assistant_turn_id"] == "turn-one"
    assert rows[0]["usage"]["metadata"]["request_id"] == "request-one"
    assert "secret" not in rows[0]["usage"]["metadata"]
    assert rows[1]["usage"]["metadata"]["job_id"] == "job-one"
    assert rows[2]["usage"]["metadata"]["output_id"] == "artifact-one"
    assert {adopted.id, rendered.id, observed.id} == {item["usage"]["id"] for item in rows}


@pytest.mark.parametrize("outcome", ["failed", "unknown", "blocked"])
def test_failure_and_unknown_outcomes_round_trip_without_promoting_resource(tmp_path: Path, outcome: str):
    store = make_store(tmp_path)
    asset = SimpleNamespace(
        id="asset-outcome", file_name="outcome.mp4", media_type="video",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    record = store.record_material_usage(
        workspace_id="ws-resource", asset=asset, segment_id="segment-outcome",
        stage="output_observation", task_id="task-outcome", timeline_version_id="timeline-outcome",
        artifact_ref="artifact-outcome", observation_ref="observation-outcome",
    )
    updated = store.update_usage_outcome("ws-resource", record.id, outcome=outcome, reason="fixture outcome")
    assert updated.metadata["outcome"] == outcome
    assert store.get_manifest("ws-resource", record.manifest_id).state == "connected"

