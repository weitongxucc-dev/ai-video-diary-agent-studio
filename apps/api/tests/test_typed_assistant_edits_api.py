"""Typed edits through isolated domain/API state; no live model or service."""
import json
from dataclasses import replace
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from app.schemas import CreationTaskExactEditRequest
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state, _granted_domain_preview_adapter  # noqa: F401
from test_linked_timeline_reorder import _build_linked_state


@pytest.fixture(autouse=True)
def legacy_edit_reentry_metadata_guard(request, monkeypatch, tmp_path):
    targets = {'test_metadata_patch_routes_consume_use_case_without_route_reentry',
               'test_metadata_patch_preserves_no_change_and_missing_target_responses',
               'test_metadata_http_save_replay_input_conflict_and_lost_receipt'}
    if request.node.originalname not in targets:
        yield
        return
    import sqlite3
    import subprocess
    import wave
    import httpx
    from PIL import Image
    from types import SimpleNamespace
    from app.auth import SupabaseAuthContext
    from app.config import Settings
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler
    from app.production import ProductionRepository
    from app.source_repository import SourceRepository
    from app.sources import ObsidianVault

    violations, notifications = [], []

    def forbidden(*args, **kwargs):
        violations.append('execution-or-media')
        pytest.fail('metadata edits must not execute media, network or runtime')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    for name in ('open', 'new'):
        monkeypatch.setattr(Image, name, forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn',
                 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: notifications.append('notified'))
    settings = Settings(database_path=str(tmp_path / 'editor.sqlite3'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    monkeypatch.setattr(main, 'vault', ObsidianVault(None))
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    provider = SimpleNamespace(ready=False, model_id='metadata-only', message='isolated',
                               render=forbidden, generate=forbidden)
    monkeypatch.setattr(main, 'latentsync_provider', provider)
    monkeypatch.setattr(main, 'avatar_lipsync_provider', provider)
    monkeypatch.setattr(main, 'video_job_runner', SimpleNamespace(run=forbidden))
    state = request.getfixturevalue('metadata_http_state')
    monkeypatch.setattr(main, 'conversation_repo', ConversationRepository(state.repo.database_path))
    monkeypatch.setattr(main, 'source_repository', SourceRepository(state.repo.database_path))

    def snapshot():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                        for name in tables}
        return databases, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                           for path in tmp_path.rglob('*')}

    before = snapshot()
    yield
    reopened = ProductionRepository(state.repo.database_path, state.repo.video_library_path)
    current = reopened.task_records.get_creation_task('ws', state.task.id)
    assert reopened.timeline_records.get_timeline_version('ws', state.base.id).snapshot == state.base.snapshot
    assert current.narration_cache_key == state.narration.cache_key
    jobs = reopened.job_records.list_job_runs('ws', state.task.id)
    if request.node.originalname == 'test_metadata_patch_routes_consume_use_case_without_route_reentry':
        assert len(jobs) == 1 and jobs[0].kind == 'subtitle_cue_update'
        assert jobs[0].status == 'succeeded' and jobs[0].output_refs
        assert all(reference.endswith('/subtitles.srt') for reference in jobs[0].output_refs)
        saved = reopened.get_creation_task_montage_plan('ws', state.task.id)
        assert saved['clips'][0]['locked'] is True
        assert saved['cues'][0]['text'] == '只更新这句字幕'
        assert saved['narration_audio_path'] == state.plan.narration_audio_path
        versions = reopened.timeline_records.list_timeline_versions('ws', state.task.id)
        assert len(versions) == 3 and current.current_timeline_version_id == versions[-1].id
        assert len(reopened.edit_records.list_edit_operations('ws', state.task.id)) == 2
        assert len(notifications) == 2
    elif request.node.originalname == 'test_metadata_patch_preserves_no_change_and_missing_target_responses':
        assert jobs == []
        assert snapshot() == before
    else:
        assert jobs == []
        assert len(reopened.edit_records.list_edit_operations('ws', state.task.id)) == 1
    assert violations == []
    state.client.close()


@pytest.fixture(autouse=True)
def task_capability_metadata_guard(request, monkeypatch, tmp_path):
    targets = {'test_execution_capability_read_is_authorized_and_does_not_start_runtime',
               'test_capabilities_are_real_schema_and_exclude_unmigrated_actions'}
    if request.node.originalname not in targets:
        return None
    import sqlite3
    from types import SimpleNamespace
    import httpx
    from PIL import Image
    from app.auth import SupabaseAuthContext
    from app.config import Settings
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler
    from app import host_resource_probes
    from app.source_repository import SourceRepository
    from app.sources import ObsidianVault

    execution = request.node.originalname == 'test_execution_capability_read_is_authorized_and_does_not_start_runtime'
    # capability_metadata_state already installs this exact guard in metadata_http_state.
    # Only the execution branch needs its missing seed-before invocation.
    if execution:
        from test_production_foundation import edit_adoption_no_execution
        edit_adoption_no_execution(monkeypatch)
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('capability-execution-or-probe')
        pytest.fail('capability metadata forbids runtime, grants, host sampling, network and media')

    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'wake', forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    monkeypatch.setattr(host_resource_probes, 'sample_host_capacity', forbidden)
    monkeypatch.setattr(host_resource_probes.HostResourceProbe, '_sample', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('_device_operation_budget', '_build_device_components',
                 '_wake_assistant_continuations', '_workspace_settings_for'):
        monkeypatch.setattr(main, name, forbidden)
    settings = Settings(database_path=str(tmp_path / 'editor.sqlite3'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    monkeypatch.setattr(main, 'conversation_repo', None)
    monkeypatch.setattr(main, 'source_repository', None)
    monkeypatch.setattr(main, 'artifact_store', None)
    monkeypatch.setattr(main, 'vault', ObsidianVault(None))
    # The original capability fixture installs its guard before its metadata seed.
    state = request.getfixturevalue('metadata_http_state' if execution else 'capability_metadata_state')
    monkeypatch.setattr(main, 'conversation_repo', ConversationRepository(state.repo.database_path))
    monkeypatch.setattr(main, 'source_repository', SourceRepository(state.repo.database_path))
    other, other_token = state.members.join(state.members.create_invite_code(), 'Other capability reader')
    assert other.member_id != state.member.member_id
    assert state.repo.task_records.get_creation_task('ws', state.task.id).visibility == 'private'

    def snapshot():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                        for name in tables}
        return databases, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                           for path in tmp_path.rglob('*')}

    before = snapshot()  # All schema, editorial declarations and member writes are preparation.

    def assert_unchanged():
        suffix = '/assistant/execution-capabilities' if execution else '/assistant/edit-capabilities'
        invalid = state.client.get(state.url + suffix, headers={'X-Local-Member-Token': 'invalid-token'})
        assert invalid.status_code == 401
        private = state.client.get(state.url + suffix, headers={'X-Local-Member-Token': other_token})
        assert private.status_code == 404 and private.json()['detail'] == 'creation task not found'
        assert snapshot() == before
        assert state.repo.job_records.list_job_runs('ws', state.task.id) == []
        assert violations == []

    return SimpleNamespace(assert_unchanged=assert_unchanged)


@pytest.fixture(autouse=True)
def typed_settings(request, monkeypatch):
    if "material_edit_state" in request.fixturenames:
        material_edit_state = request.getfixturevalue("material_edit_state")
        monkeypatch.setattr(main, "settings", replace(main.settings,
            video_inbox_path=str(material_edit_state.production.video_library_path)))


def bound(state, **action):
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    return {"request_id": str(uuid4()), "expected_timeline_version_id": version.id,
            "expected_timeline_sequence": version.sequence, **action}


def replace_action(state):
    return {"operation_type": "ReplaceClip", "clip_id": state.clip.id,
            "asset_id": state.asset.id, "segment_id": state.asset.segments[0].id}


@pytest.fixture
def metadata_http_state(tmp_path, monkeypatch, request):
    from fastapi.testclient import TestClient
    from test_metadata_edit_use_case import metadata_state
    if {"workspace_metadata_state", "scope_metadata_state", "capability_metadata_state"}.intersection(request.fixturenames):
        from test_production_foundation import edit_adoption_no_execution
        forbidden = edit_adoption_no_execution(monkeypatch)
        monkeypatch.setattr(main, "_lipsync_provider", forbidden)
        if {"scope_metadata_state", "capability_metadata_state"}.intersection(request.fixturenames):
            from app.local_portrait import LocalPortrait
            from app.subtitle_tracks import OpenAICompatibleSubtitleTranslationProvider, UnavailableSubtitleTranslationProvider
            monkeypatch.setattr(LocalPortrait, "process", forbidden)
            monkeypatch.setattr(OpenAICompatibleSubtitleTranslationProvider, "translate", forbidden)
            monkeypatch.setattr(UnavailableSubtitleTranslationProvider, "translate", forbidden)
    state = metadata_state(tmp_path)
    monkeypatch.setattr(main, "production_repo", state.repo)
    monkeypatch.setattr(main, "artifact_store", state.store)
    monkeypatch.setattr(main, "local_members_service", state.members)
    monkeypatch.setattr(main, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "ws"))
    monkeypatch.setattr(main, "settings", replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(tmp_path / "outputs")))
    state.client = TestClient(main.app, headers={"X-Local-Member-Token": state.token})
    state.url = f"/api/creation-tasks/{state.task.id}"
    return state


def metadata_payload(state):
    return {"operation_type": "ChangeCrop", "clip_id": "clip", "fit_mode": "fit", "crop_x": .3, "crop_y": .7,
        "request_id": str(uuid4()), "expected_timeline_version_id": state.base.id, "expected_timeline_sequence": 1}



@pytest.fixture
def scope_metadata_state(metadata_http_state):
    return metadata_http_state


def test_task_turn_scope_uses_current_metadata_refs_and_original_stage_gates(scope_metadata_state):
    """Declared preexisting rows/path flags, not media generation or output qualification."""
    from dataclasses import asdict
    from app.creation.contracts import CoverAsset, NarrationSegment
    from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
    from app.creation.script_windows import write_script_sections
    from app.graphic_overlays import TextOverlay
    from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
    state = scope_metadata_state
    task = replace(state.task, confirmed_script_id="fixed-script", montage_plan_id="montage")
    # Artifact lookup requires an existing file. Seed opaque bytes only, never a WAV/decoder.
    narration_reference = state.narration.audio_path
    assert state.repo.get_narration_artifact("ws", task.narration_cache_key) is None
    opaque_audio = state.case.output_root / "declared-narration.opaque"
    opaque_audio.parent.mkdir(parents=True, exist_ok=True)
    opaque_audio.write_bytes(b"declared metadata existence only; never decode")
    state.narration = replace(state.narration, audio_path=str(opaque_audio))
    state.repo.save_narration_artifact(state.narration)
    assert state.repo.get_narration_artifact("ws", task.narration_cache_key) is not None
    script = CreationTaskScript("fixed-script", "ws", task.id, "declared-run", "declared-topic", "已确认稿", "zh-CN",
        [CreationTaskScriptSection("section", "段落", "原配音", "素材画面", []),
         CreationTaskScriptSection("pending-section", "待配音", "待配音文字", "素材画面", [])],
        selection_status="confirmed")
    header = asdict(script)
    sections = header.pop("sections")
    header["_sections_storage"] = "rows"
    with state.repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(task), default=str), task.id))
        conn.execute("INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)",
            (script.id, "ws", task.id, script.run_id, json.dumps(header, default=str), script.updated_at.isoformat()))
        write_script_sections(conn, "ws", script.id, sections)
    ready = NarrationSegment("fixed-segment", "ws", task.id, script.id, "section", "原配音",
        narration_reference, 4, 16000, "declared-model", "declared-voice", "v1", "zh", "declared", "ready")
    pending = replace(ready, id="pending-segment", source_section_id="pending-section", text="待配音文字",
        audio_reference=None, duration_seconds=None, sample_rate=None, status="pending")
    state.repo.narration_records.save_narration_segments("ws", task.id, script.id, [ready, pending])
    montage = creation_task_montage_plan_from_payload(state.repo.get_creation_task_montage_plan("ws", task.id))
    montage.graphic_overlays = [TextOverlay(id="declared-overlay", text="声明标题", start_seconds=0, end_seconds=4).model_dump()]
    state.repo.save_creation_task_montage_plan("ws", task.id, montage.id, creation_task_montage_plan_to_payload(montage))
    unavailable = CoverAsset("unavailable-cover", "ws", task.id, state.base.id,
        "artifacts/task/declared-output.mp4", 0, "声明的不可用封面", {}, {}, "video_frame", "declared-engine",
        None, None, "unavailable", failure_reason="未执行", impact="没有生成封面", repair_action="先处理原条件")
    state.repo.cover_records.create_cover_asset(unavailable)
    task = state.repo.task_records.get_creation_task("ws", task.id)

    def scope(stage, refs=(), playhead=None):
        payload = main.CreationTaskAssistantTurnRequest(conversation_id=uuid4(), message="只核当前选区资格",
            client_request_id=uuid4(), expected_draft_version=0, context_stage=stage,
            selected_object_refs=list(refs), playhead_ms=playhead)
        return main._assistant_turn_scope("ws", task, payload)

    # No-selection review keeps the original early return even before a rendered path exists.
    assert scope("review") == ("review", [])
    graph = main._effective_audio_timeline(montage)
    selected = ["clip:clip", "overlay:declared-overlay", "audio:" + graph.tracks[0].id,
                "audio:" + graph.tracks[0].clips[0].id, "narration:fixed-segment", "clip:clip"]
    assert scope("montage", selected, 4000) == ("montage", selected)
    assert scope("narration", ["narration:fixed-segment"], 999999) == ("narration", ["narration:fixed-segment"])
    with pytest.raises(main.HTTPException) as rejected:
        scope("narration", ["narration:pending-segment"])
    assert rejected.value.status_code == 422
    with pytest.raises(main.HTTPException) as rejected:
        scope("montage", ["clip:foreign"], 4001)
    assert rejected.value.status_code == 422 and rejected.value.detail == "播放位置超出当前任务的时间线"
    with pytest.raises(main.HTTPException) as rejected:
        scope("montage", ["clip:foreign"], 4000)
    assert rejected.value.status_code == 422 and rejected.value.detail == "选中对象不属于当前任务或阶段"
    with pytest.raises(main.HTTPException) as rejected:
        scope("review", ["clip:clip"])
    assert rejected.value.status_code == 409 and rejected.value.detail == "当前任务没有可查看的成片"
    subtitles = ["clip:clip", "subtitle-track:base-zh", "cue:cue", "cover:unavailable-cover"]
    assert scope("subtitle_cover", subtitles, 4000) == ("subtitle_cover", subtitles)
    # This policy observes the stored rendered/path flags; it does not qualify an actual output file.
    montage.status, montage.output_path = "rendered", "artifacts/task/declared-output.mp4"
    state.repo.save_creation_task_montage_plan("ws", task.id, montage.id, creation_task_montage_plan_to_payload(montage))
    assert scope("review", selected, 4000) == ("review", selected)
    assert scope("subtitle_cover", ["cover:" + task.id]) == ("subtitle_cover", ["cover:" + task.id])

def test_execution_capability_read_is_authorized_and_does_not_start_runtime(metadata_http_state, monkeypatch, task_capability_metadata_guard):
    state = metadata_http_state
    monkeypatch.setattr(main, "device_runtime", None)

    def forbidden(*args, **kwargs):
        pytest.fail("capability read must not start or assemble execution")

    monkeypatch.setattr(main, "_creation_runtime", forbidden)
    monkeypatch.setattr(main, "_build_device_components", forbidden)
    monkeypatch.setattr(main, "_start_device_runtime", forbidden)
    response = state.client.get(state.url + "/assistant/execution-capabilities")
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts["admission"] == "not_evaluated"
    assert facts["whole_task_support"] == "not_inferred"
    assert all(not unit["configured"] for unit in facts["capabilities"])
    assert state.client.get(state.url + "/assistant/execution-capabilities",
                            headers={"x-test-workspace": "outside"}).status_code == 404
    assert state.client.get("/api/creation-tasks/missing/assistant/execution-capabilities").status_code == 404
    task_capability_metadata_guard.assert_unchanged()


def _workspace_row_script(state):
    from app.creation.script_windows import write_script_sections
    version = '2026-10-24T00:00:00+00:00'
    script_id = 'script-window-http'
    sections = [{'id': f'section-{i}', 'title': str(i), 'content': f'content {i}',
                 'visual_prompt': 'visual', 'evidence_reference_ids': []} for i in range(1, 46)]
    header = {'id': script_id, 'workspace_id': state.task.workspace_id, 'task_id': state.task.id,
              'run_id': '', 'topic_id': '', 'title': 'Window read', 'language': 'en-US',
              'selection_status': 'draft', 'created_at': version, 'updated_at': version,
              'confirmed_at': None, '_sections_storage': 'rows'}
    with state.repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('INSERT INTO creation_task_scripts '
            '(id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)',
            (script_id, state.task.workspace_id, state.task.id, '', json.dumps(header), version))
        write_script_sections(connection, state.task.workspace_id, script_id, sections)

    return version, script_id, sections


@pytest.fixture
def workspace_metadata_state(metadata_http_state):
    return metadata_http_state


def test_task_workspace_active_headers_do_not_hydrate_body(workspace_metadata_state, monkeypatch):
    state = workspace_metadata_state
    _workspace_row_script(state)

    def forbidden(*args, **kwargs):
        pytest.fail("workspace headers must not hydrate a full script or execute media")

    monkeypatch.setattr(state.repo.script_records, "get_creation_task_script", forbidden)
    monkeypatch.setattr(state.repo.script_records, "list_creation_task_scripts", forbidden)
    response = state.client.get(state.url)
    assert response.status_code == 200, response.text
    assert response.json()["scripts_scope"] == "active"
    summary = response.json()["scripts"][0]
    assert summary["sections"] is None and summary["sections_loaded"] is False
    assert summary["sections_total_count"] == 45


def test_task_workspace_facts_keep_owner_voice_and_latest_provider_result(workspace_metadata_state, monkeypatch):
    from types import SimpleNamespace
    from app.creation.contracts import TaskEvent
    state = workspace_metadata_state
    trace = []
    provider = SimpleNamespace(ready=True, model_id="declared-model", provider_name="declared-provider",
                               message="声明的可用能力；本代表不执行翻译")
    monkeypatch.setattr(main, "subtitle_translation_provider_from_settings", lambda _: provider)
    monkeypatch.setattr(main, "_portrait_processor", lambda _: SimpleNamespace(available=False))
    original_events = state.repo.execution_records.list_task_events
    original_evidence = state.repo.topic_records.list_topic_evidence_references
    original_headers = state.repo.script_records.active_creation_task_script_headers
    original_plan_dto = main._creation_plan_preview_response

    def events(*args):
        trace.append("events")
        return original_events(*args)

    def evidence(*args):
        trace.append("evidence")
        return original_evidence(*args)

    def headers(*args):
        trace.append("headers")
        return original_headers(*args)

    def plan_dto(plan):
        trace.append("plan_dto")
        return original_plan_dto(plan)

    monkeypatch.setattr(state.repo.execution_records, "list_task_events", events)
    monkeypatch.setattr(state.repo.topic_records, "list_topic_evidence_references", evidence)
    monkeypatch.setattr(state.repo.script_records, "active_creation_task_script_headers", headers)
    monkeypatch.setattr(main, "_creation_plan_preview_response", plan_dto)
    # Declared profile facts at the finite profile owner ports, not model/profile-registration evidence.
    voices = [SimpleNamespace(id=name, version="v1", person_profile_id="person", person_version="v1",
                              display_name="备用名称", language="zh", model_id="declared-voice")
              for name in ("hidden", "valid", "missing", "wrong-language")]
    monkeypatch.setattr(state.repo, "list_versioned_voice_profiles", lambda _: voices)

    def readable(voice, owner):
        assert owner == state.task.owner_user_id
        trace.append("voices")
        return voice.id != "hidden"

    def person(*_):
        raise KeyError("absent historical person")

    def reference(ws, profile_id, version, *, viewer_user_id):
        assert (ws, version, viewer_user_id) == ("ws", "v1", state.task.owner_user_id)
        assert profile_id != "hidden"
        if profile_id == "missing":
            raise OSError("unavailable declared reference")
        return SimpleNamespace(person_profile_id="person", language="en" if profile_id == "wrong-language" else "zh")

    monkeypatch.setattr(state.repo, "profile_readable", readable)
    monkeypatch.setattr(state.repo, "get_person_profile", person)
    monkeypatch.setattr(state.repo, "creation_plan_voice_reference", reference)
    monkeypatch.setattr(state.repo, "list_applicable_pronunciation_entries", lambda **_: [])

    def returned(event_id, status, *, model="declared-model"):
        state.repo.execution_records.append_task_event(TaskEvent(event_id, "ws", state.task.id, None,
            "subtitle_translation_returned", {"model_id": model, "provider_name": provider.provider_name,
            "status": status, "failure_reason": "声明的旧失败", "repair_action": "原修复建议"}))

    returned("translation-failed", "failed")
    response = state.client.get(state.url)
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts["selected_topic"] is None and facts["topic_evidence"] == []
    assert "evidence" in trace
    assert trace.index("plan_dto") < trace.index("headers") < trace.index("voices")
    assert facts["person_render_capability"]["available"] is False
    assert facts["person_render_capability"]["message"] == "0%人物，本任务不需要口型能力。"
    assert [(v["profile_id"], v["runnable_reason"]) for v in facts["available_voices"]] == [
        ("valid", None), ("missing", "voice_reference_unavailable"), ("wrong-language", "voice_language_unavailable")]
    assert all(v["person_display_name"] == "备用名称" for v in facts["available_voices"])
    assert facts["subtitle_translation_capability"]["available"] is True
    assert facts["subtitle_translation_capability"]["message"] == "上次翻译未完成：声明的旧失败"
    assert facts["subtitle_translation_capability"]["repair_action"] == "原修复建议"
    # The actual TaskBusiness factory retains the same function-value consumer;
    # calling its read port does not invoke a turn, driver or runtime.
    business = main._task_business(generation_case=object())
    assert business.workspace_facts("ws", state.task).model_dump(mode="json") == facts
    assert state.client.get(state.url, headers={"x-test-workspace": "outside"}).status_code == 404
    returned("translation-succeeded", "succeeded")
    returned("translation-other-provider", "failed", model="other-model")
    current = state.client.get(state.url)
    assert current.status_code == 200, current.text
    assert current.json()["subtitle_translation_capability"]["message"] == provider.message
    assert current.json()["subtitle_translation_capability"]["repair_action"] is None
    before = trace.count("events")
    monkeypatch.setattr(main, "subtitle_translation_provider_from_settings", lambda _: None)
    absent = state.client.get(state.url)
    assert absent.status_code == 200, absent.text
    assert trace.count("events") > before
    assert absent.json()["subtitle_translation_capability"]["available"] is False


def test_script_section_http_reads_complete_versioned_windows_without_full_hydration(metadata_http_state, monkeypatch):
    from app.creation.script_windows import write_script_sections
    state = metadata_http_state
    version, script_id, sections = _workspace_row_script(state)

    def forbidden(*args, **kwargs):
        pytest.fail('section reads must not hydrate the full script or start execution')

    monkeypatch.setattr(state.repo.script_records, 'get_creation_task_script', forbidden)
    monkeypatch.setattr(state.repo.script_records, 'list_creation_task_scripts', forbidden)
    monkeypatch.setattr(main, '_start_device_runtime', forbidden)
    for read_url in (state.url, state.url + '/scripts'):
        initial = state.client.get(read_url)
        assert initial.status_code == 200, initial.text
        assert initial.json()['scripts_scope'] == 'active'
        summary = initial.json()['scripts'][0]
        assert summary['sections'] is None and summary['sections_loaded'] is False
        assert summary['sections_total_count'] == 45
    from types import SimpleNamespace
    plan = state.repo.plan_records.get_creation_plan(state.task.workspace_id, state.task.plan_id)
    monkeypatch.setattr(main.ScriptContextUseCase, 'read_inputs', lambda *_:
        (state.task, plan, SimpleNamespace(title='Topic', angle='', audience='', outline=[]), [], []))
    context = main._script_context_use_case().read_context(state.task.workspace_id, state.task.id, state.member.member_id, local_diagnostic=False)
    assert context['draft']['sections'] is None and context['draft']['sections_loaded'] is False
    assert context['draft']['sections_total_count'] == 45
    url = state.url + f'/scripts/{script_id}/sections'
    received, cursor = [], 0
    while cursor is not None:
        response = state.client.get(url, params={'after_ordinal': cursor, 'expected_updated_at': version})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['total_count'] == 45 and page['updated_at'] == version
        received.extend(item['section'] for item in page['items'])
        cursor = page['next_after_ordinal']
    assert received == sections
    assert state.client.get(url, params={'after_ordinal': 20}).status_code == 422
    assert state.client.get(url, params={'limit': 21}).status_code == 422
    assert state.client.get(url, params={'expected_updated_at': 'invalid'}).status_code == 422
    with state.repo._connect() as connection:
        connection.execute('UPDATE creation_task_scripts SET updated_at=? WHERE id=?',
                           ('2026-10-24T00:00:01+00:00', script_id))
    assert state.client.get(url, params={'after_ordinal': 20, 'expected_updated_at': version}).status_code == 409
    assert state.client.get(url, headers={'x-test-workspace': 'outside'}).status_code == 404
    assert state.client.get(state.url + '/scripts/missing/sections').status_code == 404
    with state.repo._connect() as connection:
        connection.execute('UPDATE creation_task_scripts SET task_id=? WHERE id=?', ('other-task', script_id))
    assert state.client.get(url).status_code == 404


def test_task_budget_http_preserves_scope_meaning_and_owner_boundary(metadata_http_state, monkeypatch):
    from app.creation.contracts import JobRun
    from app.creation_execution import CreationExecutionActor
    state = metadata_http_state
    # This fixture uses "ws" as its isolated local workspace; retain real roster/token verification.
    monkeypatch.setattr(main, 'LEGACY_WORKSPACE_ID', 'ws')
    actor = CreationExecutionActor('ws', state.member.member_id)

    def forbidden(*args, **kwargs):
        pytest.fail('budget reads must not start execution')

    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main, '_start_device_runtime', forbidden)
    url = state.url + '/budget'
    empty = state.client.get(url)
    assert empty.status_code == 200, empty.text
    assert empty.json()['scopes'] == []
    assert empty.json()['authorization_coverage'] == 'scope_unbound'
    assert empty.json()['status'] == 'authorization_scope_unbound'
    job = state.repo.job_records.create_job_run(JobRun(id='job-http-budget-scope', workspace_id='ws',
        task_id=state.task.id, kind='creation_task_execution', idempotency_key='http-budget-scope',
        input_digest='1' * 64))
    command = dict(scope_id='http-goal', expected_revision=-1, request_id='original-http-test',
        source_ref='isolated-test:original', target_task_ids=[state.task.id],
        limits={'execution_seconds': None, 'generated_bytes': None}, policy_kind='local_goal',
        authorization_guard=lambda connection: None)
    state.repo.authorize_execution_budget_scope('ws', state.task.id, actor, **command)
    state.repo.bind_execution_budget_scope('ws', job.id, actor, scope_id='http-goal', expected_revision=0)
    response = state.client.get(url)
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts['authorization_coverage'] == 'tts_pcm_only'
    assert facts['scopes'][0]['remaining'] == {'execution_seconds': None, 'generated_bytes': None}
    assert facts['scopes'][0]['usage']['reserved_bytes'] == 0
    assert facts['actual_cost']['amount'] is None
    state.repo.authorize_execution_budget_scope('ws', state.task.id, actor,
        **{**command, 'request_id': 'revoke-http-test', 'expected_revision': 0, 'revoked': True})
    revoked = state.client.get(url)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()['scopes'][0]['status'] == 'revoked'
    assert state.client.get(url, headers={'x-test-workspace': 'outside'}).status_code == 403
    assert state.client.get('/api/creation-tasks/missing/budget').status_code == 404
    from dataclasses import asdict
    other_owner = state.repo.task_records.get_creation_task('ws', state.task.id)
    other_owner.owner_user_id = 'different-owner'
    with state.repo._connect() as connection:
        connection.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
                           (json.dumps(asdict(other_owner), default=str), 'ws', state.task.id))
    assert state.client.get(url).status_code == 403


def test_script_response_distinguishes_unloaded_body_from_empty_or_partial_script():
    from pydantic import ValidationError
    from app.schemas import CreationTaskScriptResponse
    metadata = dict(id='script', task_id='task', run_id='', topic_id='', title='Draft', language='en-US',
        selection_status='draft', created_at='2026-10-24T00:00:00Z',
        updated_at='2026-10-24T00:00:00Z', confirmed_at=None)
    header = CreationTaskScriptResponse.model_validate({**metadata, 'sections': None,
        'sections_loaded': False, 'sections_total_count': 25})
    assert header.sections is None and header.sections_total_count == 25
    empty = CreationTaskScriptResponse.model_validate({**metadata, 'sections': []})
    assert empty.sections_loaded and empty.sections_total_count == 0
    for fields in ({'sections': None}, {'sections': None, 'sections_loaded': False},
                   {'sections': [], 'sections_loaded': False, 'sections_total_count': 25},
                   {'sections': [], 'sections_total_count': 25}):
        with pytest.raises(ValidationError):
            CreationTaskScriptResponse.model_validate({**metadata, **fields})


def test_metadata_http_save_replay_input_conflict_and_lost_receipt(metadata_http_state, monkeypatch):
    state = metadata_http_state
    wakes = []
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: wakes.append('notified'))
    payload = metadata_payload(state)
    response = state.client.post(state.url + "/edits/exact", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert len(wakes) == 1
    assert state.client.post(state.url + "/edits/exact", json=payload).json() == saved
    assert len(wakes) == 1
    assert state.client.post(state.url + "/edits/exact", json={**payload, "crop_x": .9}).status_code == 409
    with state.repo._connect() as conn:
        conn.execute("DELETE FROM task_events WHERE event_type='edit_request_result'")
    recovered = state.client.post(state.url + "/edits/exact", json=payload)
    assert recovered.status_code == 200, recovered.text
    assert len(wakes) == 2
    assert recovered.json()["operation"] == saved["operation"]
    assert state.repo.task_records.get_creation_task('ws', state.task.id).narration_cache_key == state.narration.cache_key
    assert state.repo.timeline_records.get_timeline_version('ws', state.base.id).snapshot == state.base.snapshot
    assert len(state.repo.edit_records.list_edit_operations('ws', state.task.id)) == 1


def test_minimal_graphic_http_save_replay_and_undo_preserve_existing_metadata(tmp_path, monkeypatch):
    from test_timeline_restore_application import restore_http_state
    state = restore_http_state(tmp_path, monkeypatch)
    url = state.base_url
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    cropped = state.client.post(url + '/edits/exact', json=bound(state,
        operation_type='ChangeCrop', clip_id='clip', fit_mode='fit', crop_x=.3, crop_y=.7))
    assert cropped.status_code == 200, cropped.text
    before = state.repo.task_records.get_creation_task('ws', state.task.id)
    base = state.repo.timeline_records.get_timeline_version('ws', before.current_timeline_version_id)
    original = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    payload = {'operation_type': 'EditGraphicOverlay', 'overlay_action': 'add',
        'graphic_overlay': {'kind': 'text', 'text': '公开素材演示', 'start_seconds': 0, 'end_seconds': 3},
        'request_id': str(uuid4()), 'expected_timeline_version_id': base.id,
        'expected_timeline_sequence': base.sequence}
    response = state.client.post(url + '/edits/exact', json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    version = state.repo.timeline_records.get_timeline_version('ws', task.current_timeline_version_id)
    montage = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    layer, = montage['graphic_overlays']
    assert layer['id'].startswith('overlay_') and layer['text'] == '公开素材演示'
    assert layer['x'] == .5 and layer['y'] == .15 and layer['font_size'] == 64
    assert montage['clips'] == original['clips']
    assert montage['cues'] == original['cues'] and montage['audio_timeline'] == original['audio_timeline']
    assert montage['narration_audio_path'] == original['narration_audio_path']
    assert task.narration_cache_key == before.narration_cache_key
    versions = state.repo.timeline_records.list_timeline_versions('ws', state.task.id)
    operations = state.repo.edit_records.list_edit_operations('ws', state.task.id)
    with monkeypatch.context() as replay:
        replay.setattr(CreationTaskExactEditRequest, 'action_values',
            lambda self: pytest.fail('recorded replay must not normalize or save another graphic'))
        repeated = state.client.post(url + '/edits/exact', json=payload)
    assert repeated.status_code == 200 and repeated.json() == saved
    assert state.repo.timeline_records.list_timeline_versions('ws', state.task.id) == versions
    assert state.repo.edit_records.list_edit_operations('ws', state.task.id) == operations
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id)['graphic_overlays'] == [layer]
    journal = state.client.get(url + f"/assistant/edit-requests/{payload['request_id']}?phase=apply")
    assert journal.status_code == 200, journal.text
    assert journal.json()['submitted_input'] == payload
    assert journal.json()['result'] == saved
    stale = state.client.post(url + '/timeline/undo', json={
        'expected_timeline_version_id': base.id, 'expected_timeline_sequence': base.sequence})
    assert stale.status_code == 409
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == version.id
    undone = state.client.post(url + '/timeline/undo', json={
        'expected_timeline_version_id': version.id, 'expected_timeline_sequence': version.sequence})
    assert undone.status_code == 200, undone.text
    restored = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert restored['graphic_overlays'] == original['graphic_overlays'] == []
    assert restored['clips'] == original['clips'] and restored['audio_timeline'] == original['audio_timeline']
    after_undo = state.repo.timeline_records.list_timeline_versions('ws', state.task.id)
    replayed = state.client.post(url + '/edits/exact', json=payload)
    assert replayed.status_code == 200 and replayed.json() == saved
    assert state.repo.timeline_records.list_timeline_versions('ws', state.task.id) == after_undo
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id)['graphic_overlays'] == []


def test_metadata_patch_preserves_no_change_and_missing_target_responses(metadata_http_state):
    state = metadata_http_state
    unchanged = state.client.patch(state.url + '/montage/cues/cue', json={'text': '原字幕'})
    assert unchanged.status_code == 409, unchanged.text
    assert unchanged.json()['detail'] == {'error': 'edit_no_change', 'reason': '字幕文字、时轴和样式没有变化。',
        'impact': '没有创建新的时间线版本。', 'repair_action': '修改文字、断行、强调词或字幕样式后再预览。', 'retryable': False}
    missing = state.client.patch(state.url + '/montage/cues/missing', json={'text': '新字幕'})
    assert missing.status_code == 404, missing.text
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.edit_records.list_edit_operations('ws', state.task.id) == []


def test_metadata_patch_routes_consume_use_case_without_route_reentry(metadata_http_state, monkeypatch):
    state = metadata_http_state
    def forbidden(*args, **kwargs):
        pytest.fail("metadata PATCH must not invoke another HTTP route")
    canonical_guard_routes = [route for route in main.app.routes if route.name == 'apply_creation_task_exact_edit' and route.path == '/api/creation-tasks/{task_id}/edits/exact' and ('POST' in route.methods)]
    assert len(canonical_guard_routes) == 1
    monkeypatch.setattr(canonical_guard_routes[0], 'endpoint', forbidden)
    monkeypatch.setattr(canonical_guard_routes[0].dependant, 'call', forbidden)
    clip = state.client.patch(state.url + "/montage/clips/clip", json={"locked": True})
    assert clip.status_code == 200, clip.text
    subtitle = state.client.patch(state.url + "/montage/cues/cue", json={"text": "只更新这句字幕"})
    assert subtitle.status_code == 200, subtitle.text
    current = state.repo.task_records.get_creation_task('ws', state.task.id)
    plan = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert plan['clips'][0]['locked'] is True
    assert plan['cues'][0]['text'] == "只更新这句字幕"
    assert plan['narration_audio_path'] == state.plan.narration_audio_path
    assert current.narration_cache_key == state.narration.cache_key
    assert len(state.repo.timeline_records.list_timeline_versions('ws', state.task.id)) == 3


def test_metadata_http_refuses_stale_or_foreign_inputs(metadata_http_state):
    state = metadata_http_state
    payload = metadata_payload(state)
    assert state.client.post(state.url + "/edits/exact", json={**payload, "expected_timeline_sequence": 2}).status_code == 409
    assert state.client.post(state.url + "/edits/exact", headers={"x-test-workspace": "foreign"}, json=metadata_payload(state)).status_code == 404
    assert state.client.post("/api/creation-tasks/foreign/edits/exact", json=metadata_payload(state)).status_code == 404
    _, token = state.members.join(state.members.create_invite_code(), "other metadata member")
    denied = state.client.post(state.url + "/edits/exact", headers={"X-Local-Member-Token": token}, json=metadata_payload(state))
    assert denied.status_code in {403, 404}, denied.text
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.edit_records.list_edit_operations('ws', state.task.id) == []


@pytest.fixture
def capability_metadata_state(metadata_http_state):
    metadata_http_state.base_url = metadata_http_state.url
    return metadata_http_state


def test_capabilities_are_real_schema_and_exclude_unmigrated_actions(capability_metadata_state, monkeypatch, task_capability_metadata_guard):
    state = capability_metadata_state
    monkeypatch.setattr(main, "device_runtime", None)
    response = state.client.get(state.base_url + "/assistant/edit-capabilities")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["schema"] == CreationTaskExactEditRequest.model_json_schema()
    assert {"ReplaceClip", "TrimClip", "ChangeClipSpeed", "ChangeCrop", "UpdateSubtitleCue"} <= set(value["supported_operations"])
    assert not {"RegenerateNarrationSegment", "ChangePersonSegment", "RegeneratePersonRender"} & set(value["supported_operations"])
    assert "保留" in value["units"]["playback_rate"]
    assert "source_span=timeline_span*rate" in value["units"]["playback_rate"]
    assert "2.5*0.7=1.75" in value["units"]["playback_rate"]
    assert "ChangeLinkedSpeed：相对当前速度倍率" in value["units"]["playback_rate"]
    assert "source_span=timeline_span*rate" in value["schema"]["properties"]["playback_rate"]["description"]
    assert state.client.get("/api/creation-tasks/missing/assistant/edit-capabilities").status_code == 404
    direct = {item["operation_type"]: item for item in value["direct_apply"]["operations"]}
    assert "GenerateCoverFromFrame" not in direct
    assert direct["UpdateSubtitleCue"]["parameter_fields"] == ["cue_id", "text"]
    assert direct["UpdateSubtitleCue"]["required_fields"] == ["cue_id", "text"]
    assert direct["ConfigureSubtitleTrack"]["parameter_fields"] == ["subtitle_display"]
    assert direct["ConfigureSubtitleTrack"]["required_fields"] == ["subtitle_display"]
    assert all(item["requires_target_validation"] for item in value["operations"])
    assert value["timeline_version_id"] == state.base.id and value["timeline_sequence"] == state.base.sequence
    # Same actual factory read port, without invoking an Agent turn or asserting write authority.
    business = main._task_business(generation_case=object())
    assert business.edit_capabilities(state.task) == value
    task_capability_metadata_guard.assert_unchanged()


def test_typed_replace_replay_lost_receipt_and_undo_keep_narration(material_edit_state):
    state = material_edit_state
    before = state.client.get(state.base_url).json()
    original_audio = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["narration_audio_path"]
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).json() == preview.json()
    proposal = preview.json()["proposal"]
    result_url = state.base_url + f"/edits/{proposal['id']}/result"
    binding = state.client.get(result_url).json()
    apply = {"request_id": str(uuid4()), "expected_base_version_id": binding["base_version_id"],
             "expected_input_digest": binding["input_digest"]}
    url = state.base_url + f"/edits/{proposal['id']}/confirm"
    confirmed = state.client.post(url, json=apply)
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(state.base_url).json()
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["narration_audio_path"] == original_audio
    assert after["montage"]["cues"] == before["montage"]["cues"]
    assert after["task"]["timeline_sequence"] == before["task"]["timeline_sequence"] + 1
    assert state.client.post(url, json=apply).json() == confirmed.json()
    # Simulate receipt loss after the actual operation commit. Recover from
    # existing proposal/operation/version lineage, without a second apply.
    with state.production._connect() as conn:
        conn.execute("DELETE FROM task_events WHERE event_type='edit_request_result' AND json_extract(payload_json,'$.response.operation.proposal_id')=?", (proposal["id"],))
    recovered = state.client.post(url, json=apply)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["operation"] == confirmed.json()["operation"]
    assert state.client.get(result_url).json()["result"]["operation"] == confirmed.json()["operation"]
    assert len([op for op in state.production.edit_records.list_edit_operations(state.workspace_id, state.task.id) if op.proposal_id == proposal["id"]]) == 1
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(state.base_url).json()["montage"]["clips"] == before["montage"]["clips"]
    assert state.client.post(url, json=apply).json()["operation"] == confirmed.json()["operation"]
    changed = {**payload, "source_start_seconds": 5}
    assert state.client.post(state.base_url + "/edits/exact/preview", json=changed).status_code == 409


@pytest.mark.parametrize("action", [
    {"operation_type": "TrimClip", "source_start_seconds": 9},
    {"operation_type": "ChangeClipSpeed", "playback_rate": 0.7},
    {"operation_type": "ChangeCrop", "fit_mode": "fill", "crop_x": 0.2, "crop_y": 0.5},
])
def test_exact_typed_actions_use_existing_domain_preview(material_edit_state, action):
    state = material_edit_state
    request = bound(state, clip_id=state.clip.id, **action)
    response = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["operation"]["operation_type"] == action["operation_type"]
    intent = state.production.edit_records.get_edit_intent(state.workspace_id, response.json()["intent"]["id"])
    assert intent.constraints["request_source"]["request_id"] == request["request_id"]


@pytest.mark.parametrize("mutation", ["old_version", "old_sequence", "other_clip", "unrelated_field", "extra", "nonfinite"])
def test_typed_requests_refuse_invalid_scope_and_fields(material_edit_state, mutation):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    if mutation == "old_version":
        payload["expected_timeline_version_id"] = "old"
    elif mutation == "old_sequence":
        payload["expected_timeline_sequence"] += 1
    elif mutation == "other_clip":
        payload["clip_id"] = "another-task-clip"
    elif mutation == "unrelated_field":
        payload["text"] = "must not be silently ignored"
    elif mutation == "extra":
        payload["invented"] = 1
    else:
        payload["source_start_seconds"] = float("inf")
    before = state.client.get(state.base_url).json()["task"]["current_timeline_version_id"]
    response = state.client.post(state.base_url + "/edits/exact/preview", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code in {404, 409, 422}, response.text
    assert state.client.get(state.base_url).json()["task"]["current_timeline_version_id"] == before


def test_assistant_typed_source_is_host_bound_and_not_parsed_again(material_edit_state, tmp_path, monkeypatch):
    state = material_edit_state
    conversations = ConversationRepository(tmp_path / "conversations.sqlite3")
    conversations.initialize()
    monkeypatch.setattr(main, "conversation_repo", conversations)
    member = state.members.resolve_member(state.client.headers.get("X-Local-Member-Token"))
    conversation = conversations.create(state.workspace_id, member.member_id, "task", state.task.id)
    payload = bound(state, **replace_action(state))
    raw = "请把第二段换成那个更合适的画面，旁白维持原样"
    conversation = conversations.save_draft(conversation["id"], state.workspace_id, member.member_id, raw, conversation["draft_version"])
    turn, _ = conversations.create_turn_idempotent(conversation["id"], state.workspace_id, member.member_id,
        raw, str(uuid4()), conversation["draft_version"], request_context={"kind": "task", "task_id": state.task.id,
        "target_snapshot": {"timeline_version_id": payload["expected_timeline_version_id"], "timeline_sequence": payload["expected_timeline_sequence"]}})
    payload["assistant_turn_id"] = turn["id"]
    response = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["intent"]["raw_command"] == raw
    # Submit through the real persisted JobRun/DeviceScheduler path using the
    # existing explicit media substitutes, then end Pi before executing it.
    original_post = state.client.post
    runtime = _granted_domain_preview_adapter(state, monkeypatch)
    from test_creation_resource_contract import _render_budget
    selected_budget_for = main._device_operation_budget
    monkeypatch.setattr(main, '_device_operation_budget',
        lambda name: _render_budget() if name == 'clip_preview' else selected_budget_for(name))
    media = runtime.registrations['creation_job'].owner.task_media
    media.facts = main._task_media_facts
    def render_input(inputs, path, context):
        # Domain lineage seam only; playable media has separate R1 regressions.
        context.check()
        assert context.grant.workspace_id == state.workspace_id
        state.preview_inputs.append(inputs)
        path.write_bytes(b'domain-render-receipt')
    monkeypatch.setattr('app.media.clip_preview.render_clip_input', render_input)
    crop = bound(state, operation_type="ChangeCrop", clip_id=state.clip.id,
                 fit_mode="fill", crop_x=0.2, crop_y=0.5, assistant_turn_id=turn["id"])
    queued = original_post(state.base_url + "/edits/exact/preview", json=crop)
    assert queued.status_code == 202, queued.text
    event = state.production.execution_records.get_task_event(state.workspace_id, f"event_{queued.json()['job_id']}_start")
    command = event.payload['arguments']['_clip_preview']
    assert command['base_version_id'] == crop['expected_timeline_version_id']
    assert command['values'] == {'fit_mode':'fill', 'crop_x':.2, 'crop_y':.5}
    assert command['source']['assistant_turn_id'] == turn['id']
    conversations.finish_turn(turn["id"], "completed", "已准备修改")
    assert runtime.run_once()
    settled = state.client.get(queued.json()["result_url"])
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "succeeded", settled.text
    assert settled.json()["result"]["intent"]["raw_command"] == raw
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).json() == response.json()
    payload["request_id"] = str(uuid4())
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).status_code == 409


@pytest.mark.parametrize("use_request_identity", [True, False])
def test_source_revision_and_apply_identity_cannot_change_after_preview(material_edit_state, use_request_identity):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    before = state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    with open(state.asset.path, "ab") as source:
        source.write(b"changed fixture revision")
    identity = str(uuid4())
    response = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": identity} if use_request_identity else {})
    assert response.status_code == 409, response.text
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == before
    assert state.client.get(state.base_url + f"/assistant/edit-requests/{payload['request_id']}").json()["status"] == "recorded"


def test_other_workspace_and_task_do_not_resolve_original_request(material_edit_state, monkeypatch):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    assert state.client.post(f"/api/creation-tasks/another-task/edits/{proposal_id}/confirm", json={"request_id": str(uuid4())}).status_code == 404
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "another-workspace")
    assert state.client.get(state.base_url + "/assistant/edit-capabilities").status_code == 404
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).status_code == 404
    assert state.client.get(state.base_url + f"/edits/{proposal_id}/result").status_code == 404


def test_typed_subtitle_update_and_apply_share_the_current_version(material_edit_state):
    state = _build_linked_state(material_edit_state)
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    track = next(item for item in state.production.subtitle_records.list_subtitle_tracks(state.workspace_id, task.id, timeline_version_id=task.current_timeline_version_id) if item.language == "zh-CN")
    payload = bound(state, operation_type="UpdateSubtitleCue", track_id=track.id,
                    cue_id=track.cues[0].id, text="这是修改后的第一句。")
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    apply = state.client.post(state.base_url + f"/edits/{proposal_id}/confirm", json={"request_id": str(uuid4())})
    assert apply.status_code == 200, apply.text
    updated = state.production.task_records.get_creation_task(state.workspace_id, task.id)
    result_track = next(item for item in state.production.subtitle_records.list_subtitle_tracks(state.workspace_id, task.id, timeline_version_id=updated.current_timeline_version_id) if item.language == "zh-CN")
    assert result_track.cues[0].text == payload["text"]
    assert result_track.cues[0].start_seconds == track.cues[0].start_seconds
    assert result_track.cues[0].end_seconds == track.cues[0].end_seconds


def test_subtitle_configuration_rejects_mixed_modes(material_edit_state):
    state = material_edit_state
    response = state.client.post(state.base_url + "/edits/exact/preview", json={
        "operation_type": "ConfigureSubtitleTrack", "style": {"font_size": 40}, "target_language": "en-US"})
    assert response.status_code == 422, response.text
    assert "不同动作" in response.text


def test_apply_request_identity_is_not_reusable_for_another_proposal(material_edit_state):
    state = material_edit_state
    first = state.client.post(state.base_url + "/edits/exact/preview", json=bound(state, **replace_action(state)))
    second = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=bound(state,
        operation_type="ChangeCrop", clip_id=state.clip.id, fit_mode="fill", crop_x=0.2, crop_y=0.5))
    assert first.status_code == second.status_code == 200
    identity = {"request_id": str(uuid4())}
    assert state.client.post(state.base_url + f"/edits/{first.json()['proposal']['id']}/confirm", json=identity).status_code == 200
    version = state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    response = state.client.post(state.base_url + f"/edits/{second.json()['proposal']['id']}/confirm", json=identity)
    assert response.status_code == 409 and "不同编辑输入" in response.text
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == version


def test_manual_undo_redo_require_current_expected_pair_without_assistant_turn(tmp_path, monkeypatch):
    from test_timeline_restore_application import restore_http_state
    state = restore_http_state(tmp_path, monkeypatch)
    original = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    original_version = state.production.timeline_records.get_timeline_version(state.workspace_id, original.current_timeline_version_id)
    saved = state.client.post(state.base_url + "/edits/exact", json=bound(state,
        operation_type="ChangeCrop", clip_id="clip", fit_mode="fit", crop_x=.3, crop_y=.7))
    assert saved.status_code == 200, saved.text
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    undo = state.base_url + "/timeline/undo"
    stale = state.client.post(undo, json={"expected_timeline_version_id": original_version.id, "expected_timeline_sequence": original_version.sequence})
    assert stale.status_code == 409
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == version.id
    assert state.client.post(undo, json={"expected_timeline_version_id": version.id}).status_code == 422
    assert state.client.post(undo, json={"expected_timeline_sequence": version.sequence}).status_code == 422
    assert state.client.post(undo, json={"expected_timeline_version_id": None, "expected_timeline_sequence": None}).status_code == 422
    assert state.client.post(undo, json={"expected_timeline_version_id": None, "expected_timeline_sequence": version.sequence}).status_code == 409
    _other, token = state.members.join(state.members.create_invite_code(), "other history caller")
    expected = {"expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence}
    assert state.client.post(undo, json=expected, headers={"X-Local-Member-Token": token}).status_code in {403, 404}
    undone = state.client.post(undo, json=expected)
    assert undone.status_code == 200, undone.text
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    undo_version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    redone = state.client.post(state.base_url + "/timeline/redo", json={
        "expected_timeline_version_id": undo_version.id, "expected_timeline_sequence": undo_version.sequence})
    assert redone.status_code == 200, redone.text


    legacy = state.client.post(undo, json={})
    assert legacy.status_code == 200, legacy.text
    unavailable = state.client.post(undo, json={})
    assert unavailable.status_code == 409 and unavailable.json()["detail"]["error"] == "timeline_undo_unavailable"

def test_other_member_cannot_read_or_replay_private_typed_edit(material_edit_state):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    _member, token = state.members.join(state.members.create_invite_code(), "other")
    headers = {"X-Local-Member-Token": token}
    assert state.client.get(state.base_url + "/assistant/edit-capabilities", headers=headers).status_code in {403, 404}
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload, headers=headers).status_code in {403, 404}
    proposal = preview.json()["proposal"]
    assert state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": str(uuid4())}, headers=headers).status_code in {403, 404}


@pytest.fixture
def linked_speed_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_linked_speed_application import speed_state
    state = speed_state(tmp_path)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('x-test-workspace', 'ws'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(tmp_path / 'outputs')))
    state.client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    state.url = f'/api/creation-tasks/{state.task.id}'
    return state


def test_linked_speed_HTTP_confirm_and_fixed_result_do_not_use_HTTP_recovery(linked_speed_http_state, monkeypatch):
    state = linked_speed_http_state
    wakes = []
    wake = main._wake_assistant_continuations
    def observed_wake():
        wakes.append(True)
        return wake()
    monkeypatch.setattr(main, '_wake_assistant_continuations', observed_wake)
    preview = state.client.post(state.url + '/edits/exact/preview', json={
        'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2,
        'request_id': str(uuid4()), 'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': 2})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    result_url = state.url + f'/edits/{proposal_id}/result'
    original = state.client.get(result_url).json()
    request = {'request_id': str(uuid4()), 'expected_base_version_id': state.base.id, 'expected_input_digest': original['input_digest']}
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('speed must recover durable facts directly'))
    before_confirm = len(wakes)
    accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert accepted.status_code == 200, accepted.text
    # Preserve the existing first-save helper wake and explicit fresh-apply wake.
    assert len(wakes) == before_confirm + 2
    operation = accepted.json()['operation']
    # A later real direct edit changes HEAD; the original result must stay fixed.
    later = state.client.post(state.url + '/edits/exact', json={'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2})
    assert later.status_code == 200, later.text
    from app.task_audio import media_digest
    event = 'event_edit_request_' + media_digest(['ws', state.member.member_id, request['request_id'], 'apply'])
    recorded = state.repo.read_edit_request_result(state.task, event)
    assert recorded['status_code'] == 200 and recorded['response'] == accepted.json()
    with state.repo._connect() as conn:
        conn.execute('DELETE FROM task_events WHERE id=?', (event + '_result',))
    before_recovery = len(wakes)
    replay = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert replay.status_code == 200 and replay.json()['operation']['id'] == operation['id']
    assert len(wakes) == before_recovery
    restored = state.repo.read_edit_request_result(state.task, event)
    assert restored['status_code'] == 200 and restored['response'] == replay.json()
    read = state.client.get(result_url)
    assert read.status_code == 200 and read.json()['result']['operation']['result_version_id'] == operation['result_version_id']
    assert read.json()['result']['montage']['duration_seconds'] == 3
    assert len(state.repo.edit_records.list_edit_operations('ws', state.task.id)) == 2


def test_linked_speed_direct_and_natural_consumers_call_real_application(linked_speed_http_state, monkeypatch):
    from app.creation.editing.linked_timeline_use_case import LinkedTimelineEditUseCase
    state = linked_speed_http_state
    calls = []
    preview = LinkedTimelineEditUseCase.preview
    def observed(self, command):
        assert self.repository is state.repo
        calls.append(command)
        return preview(self, command)
    monkeypatch.setattr(LinkedTimelineEditUseCase, 'preview', observed)
    monkeypatch.setattr(main, '_preview_exact_edit_request', lambda *args: pytest.fail('direct cannot reenter preview facade'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('direct cannot reenter confirm facade'))
    applied = state.client.post(state.url + '/edits/exact', json={'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2})
    assert applied.status_code == 200, applied.text
    natural = state.client.post(state.url + '/edits/preview', json={'raw_command': '这个镜头改成2倍速 同步声音和字幕',
        'context_stage': 'montage', 'selected_object_refs': ['clip:clip']})
    assert natural.status_code == 200, natural.text
    assert natural.json()['proposal']['operation']['operation_type'] == 'ChangeLinkedSpeed'
    assert len(calls) == 2 and all(command.actor_identity['member_id'] == state.member.member_id for command in calls)


def test_linked_speed_apply_control_preserves_unsubmitted_and_explicit_null(linked_speed_http_state):
    state = linked_speed_http_state
    preview = state.client.post(state.url + '/edits/exact/preview', json={'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    url = state.url + f'/edits/{proposal_id}/confirm'
    refused = state.client.post(url, json={'request_id': str(uuid4()), 'expected_base_version_id': None})
    assert refused.status_code == 409 and '基础版本' in refused.text
    accepted = state.client.post(url, json={'request_id': str(uuid4())})
    assert accepted.status_code == 200, accepted.text


def linked_clip_action(state, operation):
    action = {'operation_type': operation, 'clip_id': 'clip'}
    if operation == 'InsertClip':
        action.update(asset_id=state.neighbour.id, segment_id=state.neighbour.segments[0].id)
    return action


@pytest.mark.parametrize('operation,duration', [('DeleteClip', 2), ('DuplicateClip', 6), ('InsertClip', 8)])
def test_linked_clip_public_preview_confirm_and_fixed_read(linked_speed_http_state, monkeypatch, operation, duration):
    state = linked_speed_http_state
    preview = state.client.post(state.url + '/edits/exact/preview', json={**linked_clip_action(state, operation),
        'request_id': str(uuid4()), 'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': 2})
    assert preview.status_code == 200, preview.text
    proposal = preview.json()['proposal']
    result_url = state.url + f"/edits/{proposal['id']}/result"
    original = state.client.get(result_url)
    assert original.status_code == 200 and original.json()['result'] is None
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('family must read durable fixed results'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('family must bypass generic confirm'))
    accepted = state.client.post(state.url + f"/edits/{proposal['id']}/confirm", json={
        'request_id': str(uuid4()), 'expected_base_version_id': state.base.id,
        'expected_input_digest': original.json()['input_digest']})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['operation']['operation_type'] == operation
    assert accepted.json()['montage']['duration_seconds'] == duration
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result']['operation'] == accepted.json()['operation']
    assert fixed.json()['result']['montage']['duration_seconds'] == duration
    assert len(state.repo.edit_records.list_edit_operations('ws', state.task.id)) == 1


@pytest.mark.parametrize('operation,command', [('DeleteClip', '删除这个镜头'),
    ('DuplicateClip', '复制这个镜头'), ('InsertClip', '在这个镜头后插入镜头')])
def test_linked_clip_direct_and_natural_use_single_application(linked_speed_http_state, monkeypatch, operation, command):
    from app.creation.editing.linked_timeline_use_case import LinkedTimelineEditUseCase
    state = linked_speed_http_state
    calls = []
    preview = LinkedTimelineEditUseCase.preview
    def observed(self, command):
        assert self.repository is state.repo and self.montage_edits.repository is state.repo
        calls.append(command)
        return preview(self, command)
    monkeypatch.setattr(LinkedTimelineEditUseCase, 'preview', observed)
    natural = state.client.post(state.url + '/edits/preview', json={'raw_command': command,
        'context_stage': 'montage', 'selected_object_refs': ['clip:clip']})
    if operation == 'InsertClip':
        assert natural.status_code == 422 and natural.json()['detail'] == '插入镜头需要指定素材片段'
    else:
        assert natural.status_code == 200, natural.text
        assert natural.json()['proposal']['operation']['operation_type'] == operation
    monkeypatch.setattr(main, '_preview_exact_edit_request', lambda *args: pytest.fail('direct cannot reenter preview facade'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('direct cannot reenter confirm facade'))
    direct = state.client.post(state.url + '/edits/exact', json=linked_clip_action(state, operation))
    assert direct.status_code == 200, direct.text
    assert direct.json()['operation']['operation_type'] == operation
    assert len(calls) == 2 and all(item.operation_type == operation for item in calls)
    assert all(item.actor_identity['member_id'] == state.member.member_id for item in calls)


def test_linked_clip_recovery_keeps_original_version_and_does_not_wake(linked_speed_http_state, monkeypatch):
    from app.task_audio import media_digest
    state = linked_speed_http_state
    wakes = []
    wake = main._wake_assistant_continuations
    def observed_wake():
        wakes.append(True)
        return wake()
    monkeypatch.setattr(main, '_wake_assistant_continuations', observed_wake)
    preview = state.client.post(state.url + '/edits/exact/preview', json=linked_clip_action(state, 'DuplicateClip'))
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    result_url = state.url + f'/edits/{proposal_id}/result'
    request = {'request_id': str(uuid4()), 'expected_base_version_id': state.base.id}
    accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert accepted.status_code == 200, accepted.text
    operation = accepted.json()['operation']
    later = state.client.post(state.url + '/edits/exact', json=linked_clip_action(state, 'DeleteClip'))
    assert later.status_code == 200, later.text
    later_head = state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id
    assert later_head != operation['result_version_id']
    event = 'event_edit_request_' + media_digest(['ws', state.member.member_id, request['request_id'], 'apply'])
    with state.repo._connect() as conn:
        conn.execute('DELETE FROM task_events WHERE id=?', (event + '_result',))
    before_wakes = len(wakes)
    recovered = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert recovered.status_code == 200 and recovered.json()['operation'] == operation
    assert recovered.json()['montage']['duration_seconds'] == 6
    assert len(wakes) == before_wakes
    assert state.repo.read_edit_request_result(state.task, event)['response'] == recovered.json()
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result']['operation'] == operation
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == later_head
    assert len(state.repo.edit_records.list_edit_operations('ws', state.task.id)) == 2
    changed = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json={**request,
        'expected_input_digest': fixed.json()['input_digest']})
    assert changed.status_code == 409 and len(wakes) == before_wakes


def test_linked_insert_permission_and_range_keep_original_error_projection(linked_speed_http_state):
    state = linked_speed_http_state
    action = linked_clip_action(state, 'InsertClip')
    state.repo._save_asset(replace(state.neighbour, owner_user_id='outside', visibility='private'))
    denied = state.client.post(state.url + '/edits/exact/preview', json={**action, 'source_start_seconds': 4})
    assert denied.status_code == 403 and denied.json()['detail'] == '当前成员无权使用该素材'
    state.repo._save_asset(state.neighbour)
    out_of_range = state.client.post(state.url + '/edits/exact/preview', json={**action, 'source_start_seconds': 4})
    assert out_of_range.status_code == 400 and out_of_range.json()['detail'] == '插入起点须在 0.0–4.0 秒之间。'
    assert not state.repo.edit_records.list_edit_operations('ws', state.task.id)


@pytest.fixture
def subtitle_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_subtitle_application import subtitle_state
    state = subtitle_state(tmp_path)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('x-test-workspace', 'ws'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(state.case.output_root)))
    state.client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    state.url = f'/api/creation-tasks/{state.task.id}'
    return state


def subtitle_action(state, operation):
    if operation == 'ConfigureSubtitleTrack':
        return {'operation_type': operation, 'style': {'font_size': 64}}
    if operation == 'ReplaceSubtitleText':
        return {'operation_type': operation, 'track_id': state.zh.id, 'find_text': '原', 'replacement_text': '新'}
    return {'operation_type': operation, 'track_id': state.zh.id, 'cue_id': 'cue', 'end_seconds': 1.5}


@pytest.mark.parametrize('operation', ['ReplaceSubtitleText', 'ConfigureSubtitleTrack', 'UpdateSubtitleCue'])
def test_subtitle_family_public_preview_confirm_fixed_read_preserves_other_facts(subtitle_http_state, monkeypatch, operation):
    state = subtitle_http_state
    before_plan = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    preview = state.client.post(state.url + '/edits/exact/preview?draft=true', json={**subtitle_action(state, operation),
        'request_id': str(uuid4()), 'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': 2})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    result_url = state.url + f'/edits/{proposal_id}/result'
    original = state.client.get(result_url)
    assert original.status_code == 200 and original.json()['result'] is None
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('subtitle reads must consume durable business facts'))
    request = {'request_id': str(uuid4()), 'expected_base_version_id': state.base.id,
        'expected_input_digest': original.json()['input_digest']}
    null_base = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json={**request, 'expected_base_version_id': None})
    assert null_base.status_code == 409
    accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert accepted.status_code == 200, accepted.text
    result = state.client.get(result_url)
    assert result.status_code == 200 and result.json()['result']['operation'] == accepted.json()['operation']
    current = state.repo.task_records.get_creation_task('ws', state.task.id)
    version = state.repo.timeline_records.get_timeline_version('ws', current.current_timeline_version_id)
    after_plan = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert after_plan['clips'] == before_plan['clips'] and after_plan['audio_timeline'] == before_plan['audio_timeline']
    assert current.narration_cache_key == state.narration.cache_key
    assert (state.case.output_root / state.narration.audio_path).read_bytes() == state.voice_bytes
    cover = state.repo.cover_records.get_cover_asset('ws', state.task.id, version.snapshot['cover_asset_id'])
    assert cover.title == state.cover.title and cover.artifact_ref == state.cover.artifact_ref
    tracks = state.repo.subtitle_records.list_subtitle_tracks('ws', state.task.id, timeline_version_id=version.id)
    if operation == 'ReplaceSubtitleText':
        assert len(tracks) == 1 and tracks[0].cues[0].text == '新字幕'
    elif operation == 'ConfigureSubtitleTrack':
        assert [track.style['font_size'] for track in tracks] == [64, 64]
        assert next(track for track in tracks if track.language == 'en-US').style['enabled'] is False
    else:
        assert len(tracks) == 2 and all(track.cues[0].end_seconds == 1.5 for track in tracks)
        assert next(track for track in tracks if track.language == 'en-US').cues[0].text == 'Original caption'


def test_subtitle_public_structure_sequence_ends_with_explicit_empty_track_snapshot(subtitle_http_state):
    state = subtitle_http_state
    # This structure inlet uses an independent English track; bilingual merge mapping is a separate boundary.
    from app.creation.contracts import TimelineVersion
    from app.creation.transactions import clone_subtitle_track, clone_cover_asset
    zh = clone_subtitle_track(state.zh, timeline_version_id='structure-start', track_id='structure-zh')
    en = replace(clone_subtitle_track(state.en, timeline_version_id='structure-start', track_id='structure-en'),
        source_language=None, generation_method='timeline')
    cover = clone_cover_asset(state.cover, timeline_version_id='structure-start')
    state.repo.timeline_records.create_timeline_version(TimelineVersion('structure-start', 'ws', state.task.id, state.base.id, 3,
        {**state.base.snapshot, 'subtitle_track_ids': [zh.id, en.id], 'cover_asset_id': cover.id}, None))
    state.repo.subtitle_records.create_subtitle_track(zh)
    state.repo.subtitle_records.create_subtitle_track(en)
    state.repo.cover_records.create_cover_asset(cover)
    def preview_confirm(operation, **values):
        task = state.repo.task_records.get_creation_task('ws', state.task.id)
        track = next(item for item in state.repo.subtitle_records.list_subtitle_tracks('ws', task.id,
            timeline_version_id=task.current_timeline_version_id) if item.language == 'en-US')
        preview = state.client.post(state.url + '/edits/exact/preview', json={
            'operation_type': operation, 'track_id': track.id, 'cue_id': track.cues[0].id, **values})
        assert preview.status_code == 200, preview.text
        proposal_id = preview.json()['proposal']['id']
        accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json={'request_id': str(uuid4())})
        assert accepted.status_code == 200, accepted.text
        fixed = state.client.get(state.url + f'/edits/{proposal_id}/result')
        assert fixed.status_code == 200 and fixed.json()['result']['operation'] == accepted.json()['operation']
    preview_confirm('SplitSubtitleCue', split_offset=8, split_time_seconds=1)
    preview_confirm('MergeSubtitleCue')
    preview_confirm('DeleteSubtitleCue')
    preview_confirm('DeleteSubtitleCue')
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    tracks = state.repo.subtitle_records.list_subtitle_tracks('ws', task.id, timeline_version_id=task.current_timeline_version_id)
    assert len(tracks) == 1 and tracks[0].language == 'zh-CN'
    preview = state.client.post(state.url + '/edits/exact/preview', json={
        'operation_type': 'DeleteSubtitleCue', 'track_id': tracks[0].id, 'cue_id': tracks[0].cues[0].id})
    assert preview.status_code == 200, preview.text
    assert state.client.post(state.url + f"/edits/{preview.json()['proposal']['id']}/confirm").status_code == 200
    task = state.repo.task_records.get_creation_task('ws', task.id)
    remaining = state.repo.subtitle_records.list_subtitle_tracks('ws', task.id, timeline_version_id=task.current_timeline_version_id)[0]
    direct = state.client.post(state.url + '/edits/exact', json={
        'operation_type': 'DeleteSubtitleCue', 'track_id': remaining.id, 'cue_id': remaining.cues[0].id})
    assert direct.status_code == 200, direct.text
    task = state.repo.task_records.get_creation_task('ws', task.id)
    version = state.repo.timeline_records.get_timeline_version('ws', task.current_timeline_version_id)
    assert version.snapshot['subtitle_track_ids'] == []
    assert state.case.current_subtitle_tracks('ws', task, current_version=version, montage=state.plan) == []


@pytest.mark.parametrize('operation,raw_command,refs', [
    ('ReplaceSubtitleText', '全文把“原”替换成“新”', ['subtitle-track:family-zh']),
    ('ConfigureSubtitleTrack', '字幕字号改成64', []),
    ('UpdateSubtitleCue', '当前字幕时间改成0到1.5秒', ['cue:cue', 'subtitle-track:family-zh'])])
def test_subtitle_direct_and_natural_consumers_use_real_family(subtitle_http_state, monkeypatch, operation, raw_command, refs):
    from app.creation.editing.subtitle_use_case import SubtitleEditUseCase
    state = subtitle_http_state
    calls = []
    prepare = SubtitleEditUseCase.prepare_preview
    def observed(self, command):
        assert self.repository is self.montage_edits.repository is state.repo
        calls.append(command)
        return prepare(self, command)
    monkeypatch.setattr(SubtitleEditUseCase, 'prepare_preview', observed)
    monkeypatch.setattr(main, '_publish_task_picture_preview', lambda *args, **kwargs: pytest.fail('draft preview must not generate media'))
    natural = state.client.post(state.url + '/edits/preview?draft=true', json={
        'raw_command': raw_command, 'context_stage': 'subtitle_cover', 'selected_object_refs': refs})
    assert natural.status_code == 200, natural.text
    assert natural.json()['proposal']['operation']['operation_type'] == operation
    monkeypatch.setattr(main, '_preview_exact_edit_request', lambda *args: pytest.fail('direct cannot reenter HTTP preview'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('direct cannot reenter HTTP confirm'))
    direct = state.client.post(state.url + '/edits/exact', json=subtitle_action(state, operation))
    assert direct.status_code == 200, direct.text
    assert direct.json()['operation']['operation_type'] == operation
    assert len(calls) == 2 and all(item.operation_type == operation for item in calls)


def test_subtitle_unknown_claim_and_HTTP_projection_are_not_application_facts(subtitle_http_state):
    from app.task_audio import media_digest
    state = subtitle_http_state
    preview = state.client.post(state.url + '/edits/exact/preview', json=subtitle_action(state, 'ReplaceSubtitleText'))
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    fixed = state.client.get(state.url + f'/edits/{proposal_id}/result').json()
    request = {'request_id': str(uuid4())}
    event, fresh = state.repo.claim_edit_request(state.task, state.member.member_id, request['request_id'], 'apply',
        {'proposal_id': proposal_id, 'base_version_id': state.base.id, 'input_digest': fixed['input_digest'], 'request': request})
    assert fresh
    state.repo.save_edit_request_result(state.task, event, status_code=200, response={'only': 'a transport projection'})
    refused = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert refused.status_code == 409 and '尚未确定' in refused.text
    assert not state.repo.edit_records.list_edit_operations('ws', state.task.id)


@pytest.fixture
def compound_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_compound_application import compound_state
    state = compound_state(tmp_path)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('x-test-workspace', 'ws'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(state.case.output_root)))
    state.client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    state.url = f'/api/creation-tasks/{state.task.id}'
    return state


def test_compound_public_selection_restore_confirm_and_fixed_read_are_one_joint_edit(compound_http_state, monkeypatch):
    from test_compound_application import COMPOUND_TEXT
    from app.task_audio import TaskAudio
    state = compound_http_state
    validations = []
    validate = TaskAudio.validate
    def observed(self, *args, **kwargs):
        assert self.repository is state.repo
        validations.append(kwargs.get('draft_only'))
        return validate(self, *args, **kwargs)
    monkeypatch.setattr(TaskAudio, 'validate', observed)
    monkeypatch.setattr(main, '_publish_task_picture_preview', lambda *args, **kwargs: pytest.fail('draft must not publish video'))
    before = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    full = state.client.post(state.url + '/edits/preview?draft=true', json={
        'raw_command': COMPOUND_TEXT, 'context_stage': 'montage', 'selected_object_refs': ['clip:clip'], 'playhead_ms': 750})
    assert full.status_code == 200, full.text
    original = full.json()['proposal']
    assert original['operation']['operation_type'] == 'EditCompoundTimeline'
    assert [step['enabled'] for step in original['operation']['payload']['steps']] == [True, True, True]
    selection_url = state.url + f"/edits/{original['id']}/selection/preview?draft=true"
    invalid = state.client.post(selection_url, json={'included_step_indices': [99]})
    assert invalid.status_code == 422
    partial = state.client.post(selection_url, json={'included_step_indices': [0, 2]})
    assert partial.status_code == 200, partial.text
    selected = partial.json()['proposal']
    assert [step['index'] for step in selected['operation']['payload']['steps']] == [0, 1, 2]
    assert [step['enabled'] for step in selected['operation']['payload']['steps']] == [True, False, True]
    assert selected['operation']['payload']['audio_timeline']['tracks'][0]['clips'][0]['gain_db'] == 0
    restored = state.client.post(state.url + f"/edits/{selected['id']}/selection/preview?draft=true",
        json={'raw_command': '恢复第2项'})
    assert restored.status_code == 200, restored.text
    proposal = restored.json()['proposal']
    assert [step['enabled'] for step in proposal['operation']['payload']['steps']] == [True, True, True]
    assert proposal['operation']['payload']['audio_timeline']['tracks'][0]['clips'][0]['gain_db'] == -3
    assert state.repo.edit_records.get_edit_proposal('ws', original['id']).status == 'proposed'
    assert state.repo.edit_records.get_edit_proposal('ws', selected['id']).status == 'proposed'
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id) == before
    result_url = state.url + f"/edits/{proposal['id']}/result"
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result'] is None
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('compound must read durable business results'))
    accepted = state.client.post(state.url + f"/edits/{proposal['id']}/confirm", json={
        'request_id': str(uuid4()), 'expected_base_version_id': state.base.id,
        'expected_input_digest': fixed.json()['input_digest']})
    assert accepted.status_code == 200, accepted.text
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    version = state.repo.timeline_records.get_timeline_version('ws', task.current_timeline_version_id)
    assert version.sequence == 4 and len(state.repo.edit_records.list_edit_operations('ws', state.task.id)) == 1
    after = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert after['clips'][0]['visual_effects']['exposure'] == .25 and after['clips'][1] == before['clips'][1]
    assert after['audio_timeline']['tracks'][0]['clips'][0]['gain_db'] == -3
    tracks = state.repo.subtitle_records.list_subtitle_tracks('ws', state.task.id, timeline_version_id=version.id)
    assert len(tracks) == 2 and all(track.style['font_size'] == 64 for track in tracks)
    assert next(track for track in tracks if track.language == 'en-US').style['enabled'] is False
    assert {track.language: [cue.text for cue in track.cues] for track in tracks} == {
        'zh-CN': ['原字幕', '后续字幕'], 'en-US': ['Original caption', 'Next caption']}
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result']['operation'] == accepted.json()['operation']
    assert len(validations) >= 4 and all(validations)


def test_compound_unknown_claim_with_projection_has_no_durable_edit_and_no_exact_API(compound_http_state):
    from test_compound_application import COMPOUND_TEXT
    state = compound_http_state
    exact = state.client.post(state.url + '/edits/exact', json={'operation_type': 'EditCompoundTimeline'})
    assert exact.status_code == 422
    preview = state.client.post(state.url + '/edits/preview?draft=true', json={
        'raw_command': COMPOUND_TEXT, 'context_stage': 'montage', 'selected_object_refs': ['clip:clip']})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    fixed = state.client.get(state.url + f'/edits/{proposal_id}/result').json()
    request = {'request_id': str(uuid4())}
    event, fresh = state.repo.claim_edit_request(state.task, state.member.member_id, request['request_id'], 'apply',
        {'proposal_id': proposal_id, 'base_version_id': state.base.id, 'input_digest': fixed['input_digest'], 'request': request})
    assert fresh
    state.repo.save_edit_request_result(state.task, event, status_code=200, response={'only': 'transport projection'})
    refused = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert refused.status_code == 409 and '尚未确定' in refused.text
    assert not state.repo.edit_records.list_edit_operations('ws', state.task.id)
