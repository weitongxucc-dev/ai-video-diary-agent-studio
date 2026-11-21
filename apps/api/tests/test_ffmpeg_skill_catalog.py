"""ffmpeg-skill is a governed capability catalog, not a media executor."""

from fastapi.testclient import TestClient

from app.editing_resource_catalog import editing_resource_catalog
from app.main import app


client = TestClient(app)


def test_ffmpeg_skill_manifest_is_catalog_only_with_explicit_capability_states():
    resource = next(item for item in editing_resource_catalog().resources if item.id == "skill:ffmpeg-skill")
    assert resource.status == "catalog_only"
    assert resource.runtime_requirements["execution_mode"] == "method-only"
    assert resource.runtime_requirements["runnable"] is False
    assert resource.runtime_requirements["manifest_version"] == "v3.12.1-adapter.2"
    assert resource.provenance[0].repository == "https://github.com/kajisho5/ffmpeg-skill"
    assert resource.provenance[0].license_spdx == "MIT"
    assert "FFmpeg 6.1+" in resource.runtime_requirements["dependencies"]["host"][0]

    capabilities = resource.runtime_requirements["external_capabilities"]
    by_id = {item["id"]: item for item in capabilities}
    assert {
        "probe", "extract_frames", "cut", "join", "audio", "fit", "export", "caption",
    } <= by_id.keys()
    assert all(item["runnable"] is False for item in capabilities)
    assert by_id["probe"]["status"] == "catalog_only"
    assert by_id["caption"]["dependencies"]
    assert "字体" in by_id["caption"]["unavailable_reason"]
    assert by_id["scenes"]["status"] == "planned"
    assert by_id["scenes"]["stability"] == "beta"
    assert all(item["source_revision"] == resource.provenance[0].revision for item in capabilities)
    assert all(item["source_license_spdx"] == "MIT" for item in capabilities)
    assert all(item["runtime_requirements"]["runnable"] is False for item in capabilities)
    contract = resource.runtime_requirements["adapter_contract"]
    assert contract["status"] == "catalog_only"
    assert set(contract["operation_map"]) == set(by_id)
    assert set(contract["lifecycle"]) >= {"submit", "progress", "logs", "resume", "cancel", "recovery", "unknown"}
    assert contract["lifecycle"]["unknown"]["host_support_status"] == "connected"


def test_ffmpeg_skill_preflight_exposes_reasons_dependencies_and_capabilities():
    response = client.get("/api/editing-resources/skill:ffmpeg-skill/preflight")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "catalog_only"
    assert body["ready"] is False
    assert body["runnable"] is False
    assert "没有把其 Python/FFmpeg 执行器" in body["status_reason"]
    assert body["repair_action"]
    assert body["adapter_contract"]["status"] == "catalog_only"
    assert body["adapter_contract"]["lifecycle"]["cancel"]["runnable"] is False
    assert body["dependencies"]["skills"] == ["editing-videos", "local-edit"]
    ids = {item["id"] for item in body["capabilities"]}
    assert {"probe", "extract_frames", "cut", "join", "audio", "fit", "export", "caption"} <= ids


def test_resource_status_query_keeps_catalog_only_and_planned_distinct():
    catalog_only = client.get("/api/editing-resource-catalog", params={"status": "catalog_only", "limit": 100})
    planned = client.get("/api/editing-resource-catalog", params={"status": "planned", "limit": 100})
    assert catalog_only.status_code == planned.status_code == 200
    assert any(item["id"] == "skill:ffmpeg-skill" for item in catalog_only.json()["resources"])
    assert all(item["status"] == "planned" for item in planned.json()["resources"])
    assert client.get("/api/editing-resource-catalog", params={"status": "failed"}).json()["resources"] == []
