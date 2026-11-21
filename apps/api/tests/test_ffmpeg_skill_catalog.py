"""ffmpeg-skill is a governed capability catalog, not a media executor."""

from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.editing_resource_catalog import EditingResource, EditingResourceCatalog, ResourcePreview, editing_resource_catalog
from app.editing_resource_routes import build_editing_resource_router
from app.main import app


client = TestClient(app)


def test_ffmpeg_skill_manifest_is_catalog_only_with_explicit_capability_states():
    resource = next(item for item in editing_resource_catalog().resources if item.id == "skill:ffmpeg-skill")
    assert resource.status == "catalog_only"
    assert resource.runtime_requirements["execution_mode"] == "method-only"
    assert resource.runtime_requirements["runnable"] is False
    assert resource.runtime_requirements["manifest_version"] == "v3.12.1-adapter.1"
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
    assert all(by_id[item]["status"] == "catalog_only" for item in ("graphics", "silence", "loudness"))
    assert by_id["caption"]["dependencies"]
    assert "字体" in by_id["caption"]["unavailable_reason"]
    assert by_id["scenes"]["status"] == "planned"


def test_ffmpeg_skill_preflight_exposes_reasons_dependencies_and_capabilities():
    response = client.get("/api/editing-resources/skill:ffmpeg-skill/preflight")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "catalog_only"
    assert body["ready"] is False
    assert body["runnable"] is False
    assert "没有把其 Python/FFmpeg 执行器" in body["status_reason"]
    assert body["repair_action"]
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


def test_preflight_never_promotes_a_non_connected_resource_to_runnable():
    fake = EditingResource(
        id="skill:contradictory", category_id="skills", name="contradictory", version=1,
        status="catalog_only", resource_type="skill", preview=ResourcePreview(kind="method_document"),
        runtime_requirements={"runnable": True}, entrypoints=["read_creation_skill"],
    )
    catalog = EditingResourceCatalog(
        categories=[], resources=[fake], provenance_fields=[], write_boundary="read-only",
    )
    local_app = FastAPI()
    local_app.include_router(build_editing_resource_router(lambda: catalog))
    response = TestClient(local_app).get("/api/editing-resources/skill:contradictory/preflight")
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["runnable"] is False
