from fastapi.testclient import TestClient

from app.editing_resource_catalog import editing_resource_catalog, search_editing_resources
from app.main import app


client = TestClient(app)


def test_manifest_keeps_navigation_categories_separate_from_resource_items():
    catalog = editing_resource_catalog()
    assert [item.id for item in catalog.categories] == [
        "materials", "skills", "stickers", "filters", "effects", "transitions",
        "templates", "subtitles_text", "voice", "music", "sfx",
    ]
    statuses = {item.id: item.status for item in catalog.categories}
    assert statuses["templates"] == "connected"
    assert statuses["voice"] == "connected"
    assert statuses["music"] == "catalog_only"
    assert statuses["sfx"] == "connected"
    assert statuses["filters"] == "planned"
    skills, total = search_editing_resources(catalog, category="skills", limit=100)
    assert total == len(skills) >= 1
    assert {item.name for item in skills} >= {"cut-as-code-video-cut", "openfilm-replicate", "editing-videos"}
    assert all(item.provenance and item.provenance[0].revision for item in skills)
    assert all(item.entrypoints == ["read_creation_skill"] for item in skills)
    assert all(item.category_id in statuses for item in catalog.resources)
    assert all(item.version >= 1 for item in catalog.resources)
    assert all(item.editable_via == ["EditProposal", "TimelineVersion"] for item in catalog.resources)
    sfx, sfx_total = search_editing_resources(catalog, category="sfx", limit=100)
    assert sfx_total == len(sfx) == 1
    assert sfx[0].id == "openfilm:sound-glass-pulse"
    assert sfx[0].status == "connected"
    assert sfx[0].provenance[0].revision == "19b26b60c1286b412c01ad64971263ae0e2aef13"


def test_template_items_expose_real_preview_and_provenance():
    catalog = editing_resource_catalog()
    templates, total = search_editing_resources(catalog, category="templates", limit=100)
    assert total == len(templates) > 0
    assert all(item.preview.width and item.preview.height for item in templates)
    assert all(item.preview.duration_seconds for item in templates)
    assert all(item.provenance and item.provenance[0].revision for item in templates)
    assert all(item.runtime_requirements["engine"] == "openfilm-html-v1" for item in templates)


def test_resource_catalog_route_supports_filtering_and_reports_read_only_boundary():
    response = client.get("/api/editing-resource-catalog", params={"category": "templates", "limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] > 2
    assert len(body["resources"]) == 2
    assert body["truncated"] is True
    assert body["next_offset"] == 2
    assert all(item["category_id"] == "templates" for item in body["resources"])
    assert "EditProposal/TimelineVersion" in body["write_boundary"]
    assert "source_sha256" in body["provenance_fields"]
    assert "license_spdx" in body["provenance_fields"]

    skills = client.get("/api/editing-resource-catalog", params={"category": "skills"})
    assert skills.status_code == 200
    assert skills.json()["resources"][0]["id"] == "skill:cut-as-code-video-cut"
    assert skills.json()["resources"][0]["entrypoints"] == ["read_creation_skill"]
    assert skills.json()["resources"][0]["runtime_requirements"]["manifest_version"] == "2026.11.13-adapter.1"
    assert skills.json()["resources"][0]["runtime_requirements"]["execution_mode"] == "method-only"
    assert skills.json()["resources"][0]["runtime_requirements"]["runnable"] is False
    assert skills.json()["resources"][0]["provenance"][0]["license_spdx"] == "MIT"
    assert skills.json()["resources"][0]["runtime_requirements"]["capabilities"]["permissions"]
    assert skills.json()["resources"][0]["runtime_requirements"]["install_checks"]["mode"] == "package-time"
    assert skills.json()["resources"][0]["runtime_requirements"]["adoption"]["status"] == "installed"
    assert skills.json()["resources"][0]["runtime_requirements"]["rollback"]["deletes_user_data"] is False

    preflight = client.get("/api/editing-resources/skill:cut-as-code-video-cut/preflight")
    assert preflight.status_code == 200
    assert preflight.json()["status"] == "connected"
    assert preflight.json()["ready"] is True
    assert preflight.json()["runnable"] is False
    assert preflight.json()["adoption"]["requires_explicit_edit_authorization"] is True
    assert "EditProposal/TimelineVersion" in preflight.json()["write_boundary"]

    voices = client.get("/api/editing-resource-catalog", params={"category": "voice"})
    assert voices.status_code == 200
    assert voices.json()["resources"][0]["id"] == "builtin:voice-profiles"

    sfx_preview = client.get("/api/editing-resources/openfilm:sound-glass-pulse/preview")
    assert sfx_preview.status_code == 200
    assert sfx_preview.headers["content-type"].startswith("audio/mp4")
    transition_preview = client.get("/api/editing-resources/openfilm:transition-glitch/preview", params={"version": 2})
    assert transition_preview.status_code == 200
    assert transition_preview.headers["content-type"].startswith("video/mp4")
    transition_poster = client.get("/api/editing-resources/openfilm:transition-glitch/preview", params={"representation": "poster", "version": 2})
    assert transition_poster.status_code == 200
    assert transition_poster.headers["content-type"].startswith("image/jpeg")


def test_resource_detail_returns_404_for_unknown_version():
    response = client.get("/api/editing-resources/template:missing", params={"version": 2})
    assert response.status_code == 404


def test_failed_is_a_supported_import_outcome_without_promoting_the_resource():
    catalog = editing_resource_catalog()
    assert "failed" in ("connected", "catalog_only", "planned", "failed")
    response = client.get("/api/editing-resource-catalog", params={"status": "failed"})
    assert response.status_code == 200
    assert response.json()["resources"] == []
