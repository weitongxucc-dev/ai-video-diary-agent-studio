from hashlib import sha256

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
    assert statuses["music"] == "connected"
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
    assert all(item.runtime_requirements["operation_type"] == "EditGraphicOverlay" for item in templates)

    card = next(item for item in templates if item.id == "template:lower-corporate")
    template_id = card.runtime_requirements["template_id"]
    version = card.runtime_requirements["template_version"]
    assert (template_id, version) == ("builtin_visual_lower-corporate", 1)
    response = client.get(f"/api/video-templates/{template_id}", params={"version": version})
    assert response.status_code == 200
    template = response.json()
    assert (template["id"], template["version"], template["name"]) == (template_id, version, card.name)
    visual = template["settings"]["visual"]
    assert (visual["width"], visual["height"], visual["duration_seconds"]) == (
        card.preview.width, card.preview.height, card.preview.duration_seconds,
    ) == (1200, 300, 4)
    source = card.provenance[0]
    assert visual["provenance"][0]["path"] == source.path
    assert sha256(visual["source"].encode()).hexdigest() == source.source_sha256


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
    assert sfx_preview.headers["content-type"].startswith("audio/wav")
    assert sfx_preview.headers["content-disposition"].startswith("inline;")
    transition = client.get("/api/editing-resources/openfilm:transition-glitch", params={"version": 2}).json()
    assert transition["status"] == "connected"
    assert transition["preview"]["kind"] == "html"
    assert transition["runtime_requirements"]["engine"] == "openfilm-html-v1"
    assert transition["runtime_requirements"]["template_id"] == "builtin_visual_openfilm-transition-glitch"
    assert transition["runtime_requirements"]["operation_type"] == "EditGraphicOverlay"
    assert "read_visual_template" in transition["entrypoints"]
    preflight = client.get("/api/editing-resources/openfilm:transition-glitch/preflight", params={"version": 2}).json()
    assert preflight["ready"] and preflight["runnable"]
    assert "EditProposal/TimelineVersion" in preflight["write_boundary"]
    transition_poster = client.get("/api/editing-resources/openfilm:transition-glitch/preview", params={"representation": "poster", "version": 2})
    assert transition_poster.status_code == 200
    assert transition_poster.headers["content-type"].startswith("image/jpeg")


def test_glass_pulse_declares_task_import_before_effect_adoption():
    resource_id = "openfilm:sound-glass-pulse"
    resource_url = f"/api/editing-resources/{resource_id}"
    detail = client.get(resource_url, params={"version": 1})
    preflight = client.get(f"{resource_url}/preflight", params={"version": 1})
    assert detail.status_code == preflight.status_code == 200
    resource = detail.json()
    readiness = preflight.json()
    requirements = resource["runtime_requirements"]
    assert requirements["preview_ref"] == resource["preview"]["thumbnail_ref"] == f"{resource_url}/preview"
    assert "source_ref" not in requirements
    assert readiness["status"] == resource["status"] == "connected"
    assert readiness["ready"] and readiness["runnable"]

    adoption = readiness["adoption"]
    assert adoption == requirements["adoption"]
    assert adoption["status"] == "requires_task_import"
    assert adoption["requires_explicit_edit_authorization"] is True
    assert adoption["import_payload"] == {"resource_id": resource["id"], "version": resource["version"]}
    assert (resource["id"], resource["version"]) == (resource_id, 1)
    assert "result.source_ref" in adoption["result"]
    assert (adoption["operation_type"], adoption["action"], adoption["track_role"]) == ("EditAudioTimeline", "add", "effect")
    assert adoption["import_endpoint"].format(task_id="fixture") == app.url_path_for(
        "import_creation_task_audio_resource", task_id="fixture")
    assert adoption["write_endpoint"].format(task_id="fixture") == app.url_path_for(
        "apply_creation_task_exact_edit", task_id="fixture")


def test_resource_detail_returns_404_for_unknown_version():
    response = client.get("/api/editing-resources/template:missing", params={"version": 2})
    assert response.status_code == 404


def test_failed_is_a_supported_import_outcome_without_promoting_the_resource():
    editing_resource_catalog()
    assert "failed" in ("connected", "catalog_only", "planned", "failed")
    response = client.get("/api/editing-resource-catalog", params={"status": "failed"})
    assert response.status_code == 200
    assert response.json()["resources"] == []
