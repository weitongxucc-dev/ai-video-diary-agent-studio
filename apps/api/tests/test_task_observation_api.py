from dataclasses import replace

import pytest

from app.quality_inspection import QualityCheckRepository
from test_quality_inspection_api import system as system, seed_render  # noqa: F401


@pytest.fixture(autouse=True)
def observation_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.originalname not in {
        "test_observation_get_keeps_original_quality_facts_and_current_version_separate",
        "test_observation_request_rejects_arbitrary_paths_and_stale_version",
    }:
        return None
    import subprocess
    import wave
    import app.main as main
    from app.auth import SupabaseAuthContext
    from app.config import Settings
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler
    from app.creation.quality_execution import QualityCheckUnitExecutor
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("execution")
        pytest.fail("observation metadata forbids runtime, worker and media execution")

    # Autouse setup precedes the imported system fixture and its metadata seed.
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(wave, "open", forbidden)
    for name in ("start", "run_once", "wake"):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, "pick_and_grant", forbidden)
    monkeypatch.setattr(QualityCheckUnitExecutor, "execute", forbidden)
    monkeypatch.setattr(main, "_creation_runtime", forbidden)
    monkeypatch.setattr(main, "_start_device_runtime", forbidden)
    monkeypatch.setattr(main.montage_renderer, "render", forbidden)
    monkeypatch.setattr(main.narration_generator, "generate_for_creation_task", forbidden)
    settings = Settings(database_path=str(tmp_path / "studio.sqlite"),
        video_inbox_path=str(tmp_path / "inbox"), video_output_path=str(tmp_path / "output"),
        model_root_path=str(tmp_path / "models"), temporary_root_path=str(tmp_path / "temporary"),
        draft_output_path=str(tmp_path / "drafts"), jianying_draft_root_path=str(tmp_path / "jianying"),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None,
        supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "auth_context", SupabaseAuthContext(settings))

    def verify_no_execution():
        assert calls == []

    request.addfinalizer(verify_no_execution)

    def snapshot(repository):
        with repository._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            facts = {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}
        tree = {str(item.relative_to(tmp_path)): item.read_bytes() if item.is_file() else None
                for item in tmp_path.rglob('*')}
        return facts, tree

    return snapshot


def test_observation_get_keeps_original_quality_facts_and_current_version_separate(system, observation_metadata_guard):
    client, repo, _, checks, headers, other_token, _ = system
    publication = seed_render(system)
    old = checks.begin("local", "task", "v1", "artifacts/video.mp4", publication.sha256)
    checks.finish("local", "task", old, evidence={"status": "issues_found", "findings": [
        {"kind": "black_frame", "start_seconds": 0, "end_seconds": .5, "duration_seconds": .5,
         "detail": "实际黑帧", "clip_ids": ["clip-1"]}], "unavailable": []})
    before_read = observation_metadata_guard(repo)
    response = client.get("/api/creation-tasks/task/observations", headers=headers)
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts["timeline_sequence"] == 1
    assert facts["quality_history"][0]["render_ref"] == publication.sha256
    assert facts["targets"][0]["output_role"] == "composed_output"
    assert observation_metadata_guard(repo) == before_read
    current = repo.timeline_records.get_timeline_version("local", "v1")
    repo.timeline_records.create_timeline_version(replace(current, id="v2", parent_version_id="v1", sequence=2))
    before_new_reads = observation_metadata_guard(repo)
    facts = client.get("/api/creation-tasks/task/observations", headers=headers).json()
    assert facts["quality_check"]["stale"] is True and facts["quality_check"]["findings"] == []
    assert facts["quality_history"][0]["timeline_version_id"] == "v1"
    assert facts["quality_history"][0]["findings"][0]["clip_ids"] == ["clip-1"]
    assert facts["targets"][0]["output_role"] == "composed_output"
    assert client.get("/api/creation-tasks/task/observations").status_code == 403
    assert client.get("/api/creation-tasks/task/observations", headers={"X-Local-Member-Token": other_token}).status_code == 404
    assert observation_metadata_guard(repo) == before_new_reads


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


def test_observation_request_rejects_arbitrary_paths_and_stale_version(system, observation_metadata_guard):
    client, repo, _, _, headers, _, _ = system
    seed_render(system)
    before_rejects = observation_metadata_guard(repo)
    route = "/api/creation-tasks/task/observations"
    payload = {"expected_timeline_version_id": "v1", "expected_timeline_sequence": 1,
               "target": {"kind": "final", "path": "/etc/passwd"}}
    assert client.post(route, headers=headers, json=payload).status_code == 422
    payload["target"] = {"kind": "final"}
    payload["expected_timeline_sequence"] = 0
    assert client.post(route, headers=headers, json=payload).status_code == 409
    assert repo.job_records.list_job_runs("local", "task")[0].kind == "creation_task_montage_render"
    assert observation_metadata_guard(repo) == before_rejects
