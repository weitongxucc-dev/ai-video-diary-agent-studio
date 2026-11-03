"""API/owner integration with explicit runner substitutes, never OS calibration."""
from dataclasses import asdict, replace
import hashlib
import io
import json
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

import app.main as main
import app.media_script_jobs as scripts
from app.creation_execution import CreationExecutionActor, CreationJobOwnerVerifier, CreationJobUnitExecutor
from app.device_runtime import OwnerRegistration, build_device_runtime
from app.graphic_sources import import_graphic_source, resolve_graphic_source
from app.host_resource_probes import HostProcessProbe, MediaScriptBudget
from app.member_permits import MemberDeviceAccess
from app.media_script import MeasuredScriptOutput
from app.task_audio import TaskMediaJobs, TaskAudio
from app.media.clip_preview import ClipPreviewExecutor
from app.creation.contracts import ArtifactRecord, JobRun, TransformRun
from app.models import utc_now
from app.conversation_repository import ConversationRepository
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state  # noqa: F401
from _media_preview_test_fixtures import ControlledResources


@pytest.fixture
def state(material_edit_state, tmp_path, monkeypatch, request):
    from assistant_turn_control_fixtures import isolate_assistant_turn_control
    isolate_assistant_turn_control(monkeypatch, request)
    state = material_edit_state
    # The API/owner tests already substitute preparation/execution. Isolate
    # only this adapter's platform inventory; do not mutate global sys or the
    # real Leaf OS-isolation guard on Linux CI.
    monkeypatch.setattr(scripts, "sys", SimpleNamespace(platform="darwin"))
    root = tmp_path / "script-runtime"
    root.mkdir()
    for name in ("python", "python_library"):
        (root / name).write_bytes(b"explicit fixture runtime")
    for name in ("stdlib", "pillow"):
        (root / name).mkdir()
    descriptor = {name: str(root / name) for name in ("python", "stdlib", "python_library", "pillow")}
    descriptor.update(version="fixture-python-pillow", digest="a" * 64)
    (root / "media-script-runtime.json").write_text(json.dumps(descriptor))
    monkeypatch.setattr(main, "settings", replace(main.settings, model_root_path=str(root),
        device_resource_mode="experiment", device_resource_experiment_id="media-script-api-fixture"))
    budget = MediaScriptBudget(4096, 4096, 8, 65536, 1, 128, 128, 16384, 65536,
        1024, 1024, 1, 10, 16 * 1024**2, 8, .1, 16 * 1024**2, 1024**2)
    # Finite experimental declarations for the substituted script runner;
    # admission below still uses ControlledResources, not host calibration.
    for name, value in {
        "DEVICE_HOST_MAX_CPU_PERCENT": 100,
        "DEVICE_HOST_MIN_AVAILABLE_MEMORY_BYTES": 1,
        "DEVICE_HOST_MIN_FREE_DISK_BYTES": 1,
        "DEVICE_HOST_MAX_DISK_IO_BYTES_PER_SECOND": 2**63,
        "DEVICE_HOST_MAX_UNIT_INPUT_BYTES": 1024**2,
    }.items():
        monkeypatch.setenv(name, str(value))
    for name, value in asdict(budget).items():
        monkeypatch.setenv("DEVICE_MEDIA_SCRIPT_" + name.upper(), str(value))
    actor = CreationExecutionActor(state.workspace_id, state.member.member_id)
    def identity(candidate):
        return "ready" if candidate == actor and main.find_member(state.members, actor.member_id) else "unauthorized"
    access = MemberDeviceAccess(state.members, identity_verifier=identity)
    monkeypatch.setattr(main, "settings", replace(main.settings, native_ocr_worker_path=None))
    media = TaskMediaJobs(state.production, facts=main._task_media_facts, verify_identity=identity,
        audio_ingress=main._audio_ingress_use_case(state.production),
        audio_edit=main._audio_edit_use_case(state.production),
        output_observation=main._output_observation_use_case(state.production),
        media_script=main._media_script_use_case(state.production),
        visual_preview=main._visual_preview_use_case(state.production),
        compound_selection=main._compound_edit_use_case(state.production),
        exact_preview=main._exact_preview_use_case(state.production),
        command_preview=main._command_preview_use_case(state.production),
        clip_preview=ClipPreviewExecutor(main._clip_preview_use_case(state.production),
            TaskAudio(state.production, main.artifact_store, main.settings.effective_temporary_root_path)))
    owner = CreationJobOwnerVerifier(state.production, task_media=media, verify_identity=identity)
    executor = CreationJobUnitExecutor(state.production, render_action=None, render_is_valid=None, task_media=media)
    runtime = build_device_runtime(database_path=state.production.database_path,
        device_lock_path=tmp_path / "script-fixture.lock", device_access=access,
        registrations={"creation_job": OwnerRegistration(owner, executor)},
        resources=ControlledResources(), processes=HostProcessProbe())
    monkeypatch.setattr(main, "_creation_runtime", lambda: runtime)
    monkeypatch.setattr(main, "_require_permit_actor", lambda request, workspace: actor)
    monkeypatch.setattr(main, "_verify_creation_identity", identity)
    monkeypatch.setattr(main, "device_runtime", runtime)
    state.calls = []
    def prepare(*, sealed, input_paths, job_directory, limits):
        job_directory.mkdir()
        (job_directory / "out").mkdir()
        return SimpleNamespace(sealed=sealed, directory=job_directory, limits=limits)
    def run(*, prepared, scheduler, grant, cancel_event):
        scheduler.assert_grant(grant)
        job = state.production.job_records.get_job_run(grant.workspace_id, grant.owner_id)
        event = state.production.execution_records.get_task_event(grant.workspace_id, f"event_{job.id}_start").payload
        assert job.status == "running" and job.task_id == state.task.id
        assert CreationExecutionActor.from_event(job.workspace_id, event) == actor
        assert event["resources"]["kind"] == "media_script"
        assert event["resources"]["script_digest"] == prepared.sealed.digest
        assert grant.input_digest != prepared.sealed.digest
        state.calls.append(job.id)
        output = prepared.sealed.requested_outputs[0]
        path = prepared.directory / "out" / output.name
        Image.new("RGBA", (16, 16), (255, 0, 0, 128)).save(path)
        content = path.read_bytes()
        return (MeasuredScriptOutput(output.name, hashlib.sha256(content).hexdigest(), len(content), 16, 16),)
    monkeypatch.setattr(scripts, "prepare_media_script", prepare)
    monkeypatch.setattr(scripts, "run_media_script", run)
    state.runtime, state.root, state.descriptor = runtime, root, descriptor
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    state.payload = {"request_id": str(uuid4()), "expected_timeline_version_id": version.id,
        "expected_timeline_sequence": version.sequence, "script_source": "from PIL import Image\n# fixture reviewed source",
        "language": "python", "parameters": {"color": "red"}, "input_refs": [], "purpose": "preview",
        "requested_outputs": [{"name": "card.png", "max_bytes": 65536, "width": 128, "height": 128}]}
    yield state
    runtime.scheduler.close()


def submit(state, **updates):
    return state.client.post(state.base_url + "/media-scripts/preview", json={**state.payload, **updates})


def test_script_job_publishes_editable_png_and_actual_definition_only_after_success(state):
    capabilities = state.client.get(state.base_url + "/assistant/media-script-capabilities")
    assert capabilities.status_code == 200 and capabilities.json()["available"]
    assert str(state.root) not in capabilities.text
    before = state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    queued = submit(state)
    assert queued.status_code == 202, queued.text
    assert queued.json()["result_kind"] == "graphic_source" and queued.json()["result"] is None
    assert state.client.get(state.base_url + "/graphics/sources").json() == []
    assert state.calls == []
    assert state.runtime.run_once()
    result = state.client.get(queued.json()["result_url"])
    assert result.status_code == 200 and result.json()["status"] == "succeeded", result.text
    source = result.json()["result"]
    assert source["applied"] is False and source["width"] == source["height"] == 16
    listed = state.client.get(state.base_url + "/graphics/sources").json()
    assert listed[0]["source_ref"] == source["source_ref"]
    image = state.client.get(state.base_url + f"/graphics/source/{source['artifact_id']}")
    assert image.status_code == 200
    with Image.open(io.BytesIO(image.content)) as png:
        assert png.size == (16, 16)
    records = state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id)
    definition = next(item for item in records if item.kind == "media_script_definition")
    content = json.loads(main.artifact_store.resolve_file(definition.logical_ref).read_bytes())
    assert content["script_source"] == state.payload["script_source"] and content["input_slots"] == []
    assert set(content["toolchain"]) == {"version", "digest"}
    assert str(state.root) not in json.dumps(content)
    transform = next(item for item in state.production.lineage_records.list_transform_runs(state.workspace_id, state.task.id) if item.transform_type == "media_script")
    assert transform.parameters["definition_ref"] == definition.logical_ref
    assert transform.parameters["owner_member_id"] == state.member.member_id
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == before
    assert submit(state).json()["job_id"] == queued.json()["job_id"]
    assert len(state.calls) == 1
    query = state.client.get(state.base_url + f"/assistant/edit-requests/{state.payload['request_id']}?phase=script")
    assert query.status_code == 200 and query.json()["submitted_input"] == state.payload
    assert submit(state, parameters={"color": "blue"}).status_code == 409


@pytest.mark.parametrize("condition", ["descriptor_missing", "descriptor_symlink", "budget_missing", "budget_partial"])
def test_unavailable_script_configuration_is_explicit_and_never_queues(state, monkeypatch, condition):
    descriptor = state.root / "media-script-runtime.json"
    if condition == "descriptor_missing":
        descriptor.unlink()
    elif condition == "descriptor_symlink":
        original = state.root / "original.json"
        descriptor.rename(original)
        descriptor.symlink_to(original)
    else:
        for name in asdict(main._script_configuration()[1]):
            monkeypatch.delenv("DEVICE_MEDIA_SCRIPT_" + name.upper())
        if condition == "budget_partial":
            monkeypatch.setenv("DEVICE_MEDIA_SCRIPT_MAX_SCRIPT_BYTES", "4096")
    capabilities = state.client.get(state.base_url + "/assistant/media-script-capabilities")
    assert capabilities.status_code == 200 and not capabilities.json()["available"]
    assert capabilities.json()["reason"]
    assert submit(state).status_code == 503
    assert state.production.job_records.list_job_runs(state.workspace_id, state.task.id) == []
    assert state.calls == []


def test_unsupported_linux_platform_is_rejected_without_unrestricted_fallback(state, monkeypatch):
    monkeypatch.setattr(scripts, "sys", SimpleNamespace(platform="linux"))
    capabilities = state.client.get(state.base_url + "/assistant/media-script-capabilities")
    assert capabilities.status_code == 200 and capabilities.json()["available"] is False
    assert "macOS" in capabilities.json()["reason"]
    assert submit(state).status_code == 503
    assert state.calls == []
    assert state.production.job_records.list_job_runs(state.workspace_id, state.task.id) == []


def test_script_current_png_input_is_frozen_and_source_change_refuses_commit(state):
    image = io.BytesIO()
    Image.new("RGBA", (16, 16), "blue").save(image, format="PNG")
    source = import_graphic_source(state.production, main.artifact_store, main.settings.effective_temporary_root_path,
        state.workspace_id, state.task.id, SimpleNamespace(filename="input.png", file=io.BytesIO(image.getvalue())))
    record = state.production.execution_records.get_artifact_record(state.workspace_id, source["artifact_id"])
    refs = [{"name": "input.png", "source_ref": record.logical_ref, "expected_artifact_id": record.id, "expected_sha256": record.sha256}]
    queued = submit(state, input_refs=refs)
    assert queued.status_code == 202, queued.text
    path = resolve_graphic_source(state.production, main.artifact_store, state.workspace_id, state.task.id, record.logical_ref)
    with path.open("ab") as changed:
        changed.write(b"changed after queue")
    assert not state.runtime.run_once()
    assert state.calls == []
    assert all(item.producer_job_id != queued.json()["job_id"] for item in state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id))


@pytest.mark.parametrize("change", ["old_version", "unknown_input", "other_member", "extra_field", "syntax"])
def test_script_scope_and_fields_are_checked_before_execution(state, change):
    updates, headers = {}, None
    if change == "old_version":
        updates["expected_timeline_version_id"] = "old"
    elif change == "unknown_input":
        updates["input_refs"] = [{"name": "input.png", "source_ref": "artifacts/another-task/input.png",
            "expected_artifact_id": "missing", "expected_sha256": "b" * 64}]
    elif change == "other_member":
        _member, token = state.members.join(state.members.create_invite_code(), "foreign script caller")
        headers = {"X-Local-Member-Token": token}
    elif change == "extra_field":
        updates["runtime_path"] = str(state.root)
    else:
        updates["script_source"] = "not valid Python !"
    response = state.client.post(state.base_url + "/media-scripts/preview", json={**state.payload, **updates}, headers=headers)
    assert response.status_code in {403, 404, 409, 422}, response.text
    assert state.calls == []


def test_published_png_revision_change_removes_editable_source(state):
    queued = submit(state)
    assert queued.status_code == 202 and state.runtime.run_once()
    source = state.client.get(queued.json()["result_url"]).json()["result"]
    path = main.artifact_store.resolve_file(source["source_ref"])
    with path.open("ab") as changed:
        changed.write(b"changed output")
    assert state.client.get(state.base_url + "/graphics/sources").json() == []
    assert state.client.get(state.base_url + f"/graphics/source/{source['artifact_id']}").status_code == 404


def test_script_source_is_adopted_through_existing_graphic_edit_and_undo(state):
    queued = submit(state)
    assert queued.status_code == 202 and state.runtime.run_once()
    source = state.client.get(queued.json()["result_url"]).json()["result"]
    before = state.client.get(state.base_url).json()
    preview = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json={
        "operation_type": "EditGraphicOverlay", "overlay_action": "add", "graphic_overlay": {
            "kind": "image", "id": "script-card", "name": "card.png", "source_ref": source["source_ref"],
            "start_seconds": 0, "end_seconds": 2, "x": .5, "y": .5, "width": .2}})
    assert preview.status_code == 200, preview.text
    confirmed = state.client.post(state.base_url + f"/edits/{preview.json()['proposal']['id']}/confirm", json={"request_id": str(uuid4())})
    assert confirmed.status_code == 200, confirmed.text
    assert state.client.get(state.base_url).json()["montage"]["graphic_overlays"][0]["source_ref"] == source["source_ref"]
    assert state.client.get(state.base_url).json()["montage"]["cues"] == before["montage"]["cues"]
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(state.base_url).json()["montage"]["graphic_overlays"] == before["montage"]["graphic_overlays"]
    assert len(state.calls) == 1


def test_shared_artifact_transform_connection_is_one_rollback_transaction(state):
    now = utc_now()
    job = state.production.job_records.create_job_run(JobRun(id="job-shared-transaction", workspace_id=state.workspace_id,
        task_id=state.task.id, kind="fixture_metadata", idempotency_key="metadata-rollback", input_digest="c" * 64))
    artifact = ArtifactRecord(id="artifact-rollback", workspace_id=state.workspace_id, task_id=state.task.id,
        kind="media_script_definition", logical_ref="artifacts/rollback-definition.json", sha256="d" * 64,
        size_bytes=2, producer_job_id=job.id, input_refs=[], created_at=now)
    transform = TransformRun(id="transform-rollback", workspace_id=state.workspace_id, task_id=state.task.id,
        job_id=job.id, transform_type="fixture_metadata", input_digest="c" * 64, input_refs=[],
        output_refs=[artifact.logical_ref], engine="fixture", parameters={}, status="succeeded", started_at=now, finished_at=now)
    with pytest.raises(RuntimeError, match="rollback"):
        with state.production._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            assert state.production.execution_records.create_artifact_record(artifact, connection=conn) == artifact
            assert state.production.lineage_records.create_transform_run(transform, connection=conn) == transform
            raise RuntimeError("rollback")
    with pytest.raises(KeyError):
        state.production.execution_records.get_artifact_record(state.workspace_id, artifact.id)
    with pytest.raises(KeyError):
        state.production.lineage_records.get_transform_run(state.workspace_id, transform.id)


def test_crash_after_graphic_record_recovers_definition_without_rerunning_python(state, monkeypatch):
    original_definition = scripts.script_definition
    queued = submit(state)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    def fail_after_graphic_record(*args):
        if any(item.producer_job_id == job_id and item.kind == "graphic_source"
               for item in state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id)):
            raise RuntimeError("crash after PNG record")
        return original_definition(*args)
    monkeypatch.setattr(scripts, "script_definition", fail_after_graphic_record)
    assert state.runtime.run_once()
    assert state.production.job_records.get_job_run(state.workspace_id, job_id).status == "failed"
    assert any(item.producer_job_id == job_id and item.kind == "graphic_source"
               for item in state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id))
    assert state.client.get(state.base_url + "/graphics/sources").json() == []
    assert len(state.calls) == 1
    monkeypatch.setattr(scripts, "script_definition", original_definition)
    retry = state.client.post(state.base_url + f"/media-jobs/{job_id}/retry")
    assert retry.status_code == 202, retry.text
    assert state.runtime.run_once()
    result = state.client.get(queued.json()["result_url"])
    assert result.json()["status"] == "succeeded", result.text
    assert len(state.calls) == 1
    assert len(state.client.get(state.base_url + "/graphics/sources").json()) == 1
    assert any(item.kind == "media_script_definition" and item.producer_job_id == job_id
               for item in state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id))


def test_unknown_started_script_without_publication_is_not_rerun(state, monkeypatch):
    def unknown_run(**kwargs):
        state.calls.append(kwargs["grant"].owner_id)
        raise RuntimeError("fixture lost execution outcome")
    monkeypatch.setattr(scripts, "run_media_script", unknown_run)
    queued = submit(state)
    assert queued.status_code == 202 and state.runtime.run_once()
    job_id = queued.json()["job_id"]
    assert state.production.job_records.get_job_run(state.workspace_id, job_id).status == "failed"
    assert state.client.post(state.base_url + f"/media-jobs/{job_id}/retry").status_code == 202
    assert state.runtime.run_once()
    assert len(state.calls) == 1
    assert state.client.get(state.base_url + "/graphics/sources").json() == []


def test_shared_job_artifact_transform_finish_rollback_is_atomic(state):
    job = JobRun(id="job-all-rollback", workspace_id=state.workspace_id, task_id=state.task.id,
        kind="fixture_metadata", idempotency_key="all-metadata-rollback", input_digest="e" * 64)
    with pytest.raises(RuntimeError, match="rollback"):
        with state.production._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            created = state.production.job_records.create_job_run(job, connection=conn)
            running = state.production.job_records.claim_job_run(state.workspace_id, created.id, connection=conn)
            assert running.status == "running"
            artifact = state.production.execution_records.create_artifact_record(ArtifactRecord(id="artifact-all-rollback", workspace_id=state.workspace_id,
                task_id=state.task.id, kind="media_script_definition", logical_ref="artifacts/all-rollback.json", sha256="f" * 64,
                size_bytes=2, producer_job_id=job.id, input_refs=[]), connection=conn)
            state.production.lineage_records.create_transform_run(TransformRun(id="transform-all-rollback", workspace_id=state.workspace_id,
                task_id=state.task.id, job_id=job.id, transform_type="fixture_metadata", input_digest=job.input_digest,
                input_refs=[], output_refs=[artifact.logical_ref], engine="fixture", parameters={}, status="succeeded",
                started_at=running.started_at, finished_at=utc_now()), connection=conn)
            finished = state.production.job_records.finish_job_run(state.workspace_id, job.id, status="succeeded",
                output_refs=[artifact.logical_ref], connection=conn)
            assert finished.status == "succeeded"
            raise RuntimeError("rollback")
    with pytest.raises(KeyError):
        state.production.job_records.get_job_run(state.workspace_id, job.id)
    with pytest.raises(KeyError):
        state.production.execution_records.get_artifact_record(state.workspace_id, "artifact-all-rollback")
    with pytest.raises(KeyError):
        state.production.lineage_records.get_transform_run(state.workspace_id, "transform-all-rollback")


def test_script_success_continuation_is_source_ready_without_applying_a_version(state, tmp_path, monkeypatch):
    conversations = ConversationRepository(state.production.database_path)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    chat = conversations.create(state.workspace_id, state.member.member_id, "task", state.task.id)
    raw = "生成一张红色卡片，先作为图形来源"
    chat = conversations.save_draft(chat["id"], state.workspace_id, state.member.member_id, raw, chat["draft_version"])
    turn, _ = conversations.create_turn_idempotent(chat["id"], state.workspace_id, state.member.member_id,
        raw, str(uuid4()), chat["draft_version"], request_context={"kind": "task", "task_id": state.task.id,
        "target_snapshot": {"timeline_version_id": state.payload["expected_timeline_version_id"],
                            "timeline_sequence": state.payload["expected_timeline_sequence"]}})
    queued = submit(state, assistant_turn_id=turn["id"])
    assert queued.status_code == 202
    conversations.finish_turn(turn["id"], "completed", "已安排图片生成", result={"kind": "task_turn", "edit_preview": False,
        "response": {"reply": "已安排图片生成", "provider": "fixture", "model": "fixture", "media_job": queued.json()}})
    assert state.runtime.run_once()
    assert main._reconcile_assistant_business_results() == 1
    updated = conversations.get_turn(turn["id"])
    assert updated["result"]["continuation"]["status"] == "succeeded"
    assert updated["result"]["response"]["media_job"]["result"]["applied"] is False
    assert "图片来源已生成" in updated["assistant_text"]
    assert updated["result"]["edit_preview"] is False
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == state.payload["expected_timeline_version_id"]


@pytest.mark.parametrize("boundary", ["definition_record", "transform_record"])
def test_partial_metadata_recovery_reuses_identical_records_without_rerunning(state, monkeypatch, boundary):
    image = io.BytesIO()
    Image.new("RGBA", (8, 8), "green").save(image, format="PNG")
    source = import_graphic_source(state.production, main.artifact_store, main.settings.effective_temporary_root_path,
        state.workspace_id, state.task.id, SimpleNamespace(filename="input.png", file=io.BytesIO(image.getvalue())))
    record = state.production.execution_records.get_artifact_record(state.workspace_id, source["artifact_id"])
    inputs = [{"name": "input.png", "source_ref": record.logical_ref, "expected_artifact_id": record.id, "expected_sha256": record.sha256}]
    method = "create_transform_run" if boundary == "definition_record" else "create_dependency_edge"
    original = getattr(state.production.lineage_records, method)
    monkeypatch.setattr(state.production.lineage_records, method, lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("partial metadata boundary")))
    queued = submit(state, input_refs=inputs)
    assert queued.status_code == 202 and state.runtime.run_once()
    job_id = queued.json()["job_id"]
    assert state.production.job_records.get_job_run(state.workspace_id, job_id).status == "failed"
    definition = next(item for item in state.production.execution_records.list_artifact_records(state.workspace_id, state.task.id)
                      if item.producer_job_id == job_id and item.kind == "media_script_definition")
    monkeypatch.setattr(state.production.lineage_records, method, original)
    assert state.client.post(state.base_url + f"/media-jobs/{job_id}/retry").status_code == 202
    assert state.runtime.run_once()
    result = state.client.get(queued.json()["result_url"])
    assert result.json()["status"] == "succeeded", result.text
    assert state.production.execution_records.get_artifact_record(state.workspace_id, definition.id) == definition
    assert len(state.calls) == 1


def submission_metadata_context(state, monkeypatch):
    """Plain source/configuration facts over receipt_state's real isolated owners."""
    from pathlib import Path
    from app.media_script import ScriptToolchain, MediaScriptError
    state.config_calls, state.expansions = [], []
    state.configuration_error = None
    root = Path(state.production.database_path).parent
    state.toolchain = ScriptToolchain(root/'declared-python',root/'declared-stdlib',root/'declared-library',
        root/'declared-pillow','declared-metadata','d'*64)
    def configure():
        state.config_calls.append('configuration')
        if state.configuration_error is not None:
            raise MediaScriptError(state.configuration_error)
        return state.toolchain, SimpleNamespace()
    monkeypatch.setattr(main,'_script_configuration',configure)
    expand = main._expand_media_script_submission_recipe
    def expand_recipe(actor, tool_id, values):
        state.expansions.append(tool_id)
        return expand(actor,tool_id,values)
    monkeypatch.setattr(main,'_expand_media_script_submission_recipe',expand_recipe)
    def raw_payload():
        return {'request_id':str(uuid4()),'expected_timeline_version_id':None,'expected_timeline_sequence':0,
            'script_source':'# declared submission only; never executed', 'parameters':{},'input_refs':[],
            'requested_outputs':[{'name':'card.png','max_bytes':4096,'width':16,'height':16}]}
    state.raw_payload = raw_payload
    state.payload = raw_payload()
    state.initial_jobs = state.production.job_records.list_job_runs(state.workspace_id, state.task.id)
    return state
