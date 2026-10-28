from dataclasses import replace

from app.quality_inspection import QualityCheckRepository
from test_quality_inspection_api import system as system, seed_render  # noqa: F401


def test_observation_get_keeps_original_quality_facts_and_current_version_separate(system):
    client, repo, _, checks, headers, other_token, _ = system
    publication = seed_render(system)
    old = checks.begin("local", "task", "v1", "artifacts/video.mp4", publication.sha256)
    checks.finish("local", "task", old, evidence={"status": "issues_found", "findings": [
        {"kind": "black_frame", "start_seconds": 0, "end_seconds": .5, "duration_seconds": .5,
         "detail": "实际黑帧", "clip_ids": ["clip-1"]}], "unavailable": []})
    response = client.get("/api/creation-tasks/task/observations", headers=headers)
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts["timeline_sequence"] == 1
    assert facts["quality_history"][0]["render_ref"] == publication.sha256
    assert facts["targets"][0]["output_role"] == "composed_output"
    current = repo.timeline_records.get_timeline_version("local", "v1")
    repo.timeline_records.create_timeline_version(replace(current, id="v2", parent_version_id="v1", sequence=2))
    facts = client.get("/api/creation-tasks/task/observations", headers=headers).json()
    assert facts["quality_check"]["stale"] is True and facts["quality_check"]["findings"] == []
    assert facts["quality_history"][0]["timeline_version_id"] == "v1"
    assert facts["quality_history"][0]["findings"][0]["clip_ids"] == ["clip-1"]
    assert facts["targets"][0]["output_role"] == "composed_output"
    assert client.get("/api/creation-tasks/task/observations").status_code == 401
    assert client.get("/api/creation-tasks/task/observations", headers={"X-Local-Member-Token": other_token}).status_code == 404


def test_late_quality_completion_retains_each_original_binding(system):
    _, repo, _, checks, _, _, _ = system
    publication = seed_render(system)
    old = checks.begin("local", "task", "v1", "artifacts/video.mp4", publication.sha256)
    new = checks.begin("local", "task", "v2", "artifacts/new.mp4", "b" * 64)
    checks.finish("local", "task", old, evidence={"status": "clean", "findings": [], "unavailable": []})
    assert checks.get("local", "task")["check_id"] == new
    history = QualityCheckRepository(repo.database_path).history("local", "task")
    previous = next(item for item in history if item["check_id"] == old)
    assert previous["timeline_version_id"] == "v1" and previous["result_status"] == "clean"


def test_history_migration_preserves_existing_current_check(system):
    _, repo, _, checks, _, _, _ = system
    check_id = checks.begin("local", "task", "old-version", "old.mp4", "a" * 64)
    checks.finish("local", "task", check_id, evidence={"status": "clean", "findings": [], "unavailable": []})
    previous = checks.get("local", "task")
    with repo._connect() as conn:
        conn.execute("DROP TABLE creation_task_quality_history")  # Prior schema, isolated DB only.
    upgraded = QualityCheckRepository(repo.database_path)
    assert upgraded.get("local", "task") == previous
    assert upgraded.history("local", "task") == [previous]


def test_observation_request_rejects_arbitrary_paths_and_stale_version(system):
    client, repo, _, _, headers, _, _ = system
    seed_render(system)
    route = "/api/creation-tasks/task/observations"
    payload = {"expected_timeline_version_id": "v1", "expected_timeline_sequence": 1,
               "target": {"kind": "final", "path": "/etc/passwd"}}
    assert client.post(route, headers=headers, json=payload).status_code == 422
    payload["target"] = {"kind": "final"}
    payload["expected_timeline_sequence"] = 0
    assert client.post(route, headers=headers, json=payload).status_code == 409
    assert repo.job_records.list_job_runs("local", "task")[0].kind == "creation_task_montage_render"
