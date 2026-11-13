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
    assert statuses["sfx"] == "catalog_only"
    assert statuses["filters"] == "planned"
    skills, total = search_editing_resources(catalog, category="skills", limit=100)
    assert total == len(skills) >= 1
    assert all(item.provenance and item.provenance[0].revision for item in skills)
    assert all(item.entrypoints == ["read_creation_skill"] for item in skills)
    assert all(item.category_id in statuses for item in catalog.resources)
    assert all(item.version >= 1 for item in catalog.resources)
    assert all(item.editable_via == ["EditProposal", "TimelineVersion"] for item in catalog.resources)


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

    skills = client.get("/api/editing-resource-catalog", params={"category": "skills"})
    assert skills.status_code == 200
    assert skills.json()["resources"][0]["id"] == "skill:cut-as-code-video-cut"
    assert skills.json()["resources"][0]["entrypoints"] == ["read_creation_skill"]
    assert skills.json()["resources"][0]["runtime_requirements"]["manifest_version"] == "2026.11.13-adapter.1"

    voices = client.get("/api/editing-resource-catalog", params={"category": "voice"})
    assert voices.status_code == 200
    assert voices.json()["resources"][0]["id"] == "builtin:voice-profiles"


def test_resource_detail_returns_404_for_unknown_version():
    response = client.get("/api/editing-resources/template:missing", params={"version": 2})
    assert response.status_code == 404
