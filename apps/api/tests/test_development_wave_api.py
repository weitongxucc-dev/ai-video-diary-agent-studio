"""Shared HTTP integration with isolated SQLite/files; no model, microphone or GUI."""
import io
import wave
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.materials.discovery import MaterialDiscoveryService
from app.materials.discovery_intake import DiscoveryIntake
from app.materials.discovery_store import DiscoveryStore


def _sampling_context(tmp_path, monkeypatch):
    members = LocalMemberService(tmp_path / "members.sqlite3")
    owner, token = members.whoami(None)
    _, other = members.join(members.create_invite_code(), "另一位成员")
    library = tmp_path / "library"
    library.mkdir()
    repository = ProductionRepository(database_path=tmp_path / "studio.sqlite3", video_library_path=library)
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "production_repo", repository)
    return TestClient(main.app), repository, owner, {"X-Local-Member-Token": token}, {"X-Local-Member-Token": other}


def _pcm_sample():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    return buffer.getvalue()


def test_sampling_http_persists_replay_and_keeps_member_ownership(tmp_path, monkeypatch):
    client, repository, owner, headers, other = _sampling_context(tmp_path, monkeypatch)
    base = "/api/media/voice-sampling-tasks"
    create = {"request_id": str(uuid4()), "display_name": "我的声音", "language": "zh", "purpose": "voice_profile"}
    created = client.post(base, json=create, headers=headers)
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["owner_user_id"] == owner.member_id
    assert task["status"] == "awaiting_sample"
    assert client.post(base, json=create, headers=headers).json()["id"] == task["id"]
    target = f"{base}/{task['id']}"
    assert client.get(target, headers=other).status_code == 404
    assert client.get(base, headers=other).json()["items"] == []
    data = {"request_id": str(uuid4()), "text_version": task["text_version"], "purpose": "voice_profile", "source_device": "desktop"}
    files = {"audio": ("sample.wav", _pcm_sample(), "audio/wav")}
    assert client.post(f"{target}/sample", data=data, files=files, headers=other).status_code == 404
    received = client.post(f"{target}/sample", data=data, files=files, headers=headers)
    assert received.status_code == 200, received.text
    receipt = received.json()
    assert receipt["status"] == "received"
    assert receipt["voice_profile_id"]
    assert client.post(f"{target}/sample", data=data, files=files, headers=headers).json() == receipt
    assert client.post(f"{target}/sample", data={**data, "text_version": "obsolete"}, files=files, headers=headers).status_code == 409
    reopened = ProductionRepository(database_path=repository.database_path, video_library_path=repository.video_library_path)
    assert reopened.voice_sampling.get(task["workspace_id"], owner.member_id, task["id"]).voice_profile_id == receipt["voice_profile_id"]
    assert len(list(repository.video_library_path.rglob("*.wav"))) == 1
    with repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM material_assets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM versioned_voice_profiles").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 0


def test_sampling_http_rejects_missing_or_forged_identity_and_unpaired_mobile(tmp_path, monkeypatch):
    client, repository, owner, headers, other = _sampling_context(tmp_path, monkeypatch)
    base = "/api/media/voice-sampling-tasks"
    create = {"request_id": str(uuid4()), "display_name": "我的声音"}
    assert client.post(base, json=create).status_code == 403
    assert client.post(base, json={**create, "owner_user_id": "someone-else"}, headers=headers).status_code == 422
    assert client.post(base, json={**create, "workspace_id": "other-space"}, headers=headers).status_code == 422
    task = client.post(base, json=create, headers=headers).json()
    response = client.post(f"{base}/{task['id']}/sample", headers=headers,
        data={"request_id": str(uuid4()), "purpose": "voice_profile", "text_version": task["text_version"], "source_device": "mobile"},
        files={"audio": ("sample.wav", b"opaque-unread-sample", "audio/wav")})
    assert response.status_code == 401
    assert client.get(f"{base}/{task['id']}", headers=headers).json()["status"] == "awaiting_sample"

    assert client.post(base, json=create, headers=headers).json() == task
    assert client.post(base, json={**create, "display_name": "另一名称"}, headers=headers).status_code == 409
    assert client.get(base, headers=headers).json() == {"items": [task], "total_count": 1}
    assert client.get(base, headers=other).json() == {"items": [], "total_count": 0}
    assert client.get(f"{base}/{task['id']}", headers=other).status_code == 404
    rejected = client.post(f"{base}/{task['id']}/sample", headers=other,
        data={"request_id": str(uuid4()), "purpose": "voice_profile", "text_version": task["text_version"], "source_device": "desktop"},
        files={"audio": ("sample.wav", b"opaque-unread-sample", "audio/wav")})
    assert rejected.status_code == 404
    assert client.get(f"{base}/{task['id']}", headers=headers).json() == task
    reopened = ProductionRepository(database_path=repository.database_path,
                                    video_library_path=repository.video_library_path)
    persisted = reopened.voice_sampling.get(task["workspace_id"], owner.member_id, task["id"])
    assert persisted.status == "awaiting_sample"
    assert persisted.last_error is None and persisted.submission_request_id is None
    assert persisted.voice_profile_id is None
    assert list(repository.video_library_path.iterdir()) == []
    with reopened._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM voice_sampling_tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM material_assets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM versioned_voice_profiles").fetchone()[0] == 0


def test_discovery_http_selects_only_one_candidate_and_preserves_it_on_retry(tmp_path, monkeypatch):
    from test_material_copy_confirmation_api import material_intake_no_execution
    violations = material_intake_no_execution(monkeypatch)
    client, repository, owner, headers, other = _sampling_context(tmp_path, monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "chosen.png").write_bytes(b"isolated source A")
    (source / "unselected.png").write_bytes(b"isolated source B")
    excluded = source / "excluded"
    excluded.mkdir()
    (excluded / "private.png").write_bytes(b"excluded source")
    repository.material_source_files.bind_material_ingestion("local", expected_directory=str(repository.video_library_path), verified_owner_user_id=owner.member_id)
    repository.material_discovery = MaterialDiscoveryService(
        DiscoveryStore(repository._connect), root_provider=lambda: [source],
        excluded_roots=lambda: [repository.video_library_path], target_for=repository.material_source_files.material_link_target,
        accept_copy=DiscoveryIntake(repository._connect, repository._material_assets,
            workspace_references=repository._workspace_media_references,
            target_for=repository.material_source_files.material_link_target, confirm_copy=repository.material_source_files.confirm_material_copy_complete),
    )
    monkeypatch.setattr(main, "material_ingestion", None)
    base = "/api/production/materials/discovery"
    assert client.get(f"{base}/roots").status_code == 403
    roots = client.get(f"{base}/roots", headers=headers).json()["roots"]
    invalid = client.post(base, headers=headers, json={"root_ids": [str(tmp_path)]})
    assert invalid.status_code == 409
    created = client.post(base, headers=headers, json={"root_ids": [roots[0]["id"]], "exclusions": [str(excluded)]})
    assert created.status_code == 201, created.text
    target = f"{base}/{created.json()['id']}"
    assert client.get(target, headers=other).status_code == 404
    scanned = client.post(f"{target}/advance", headers=headers).json()
    assert {item["file_name"] for item in scanned["candidates"]} == {"chosen.png", "unselected.png"}
    with repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM material_assets").fetchone()[0] == 0
    chosen = next(item for item in scanned["candidates"] if item["file_name"] == "chosen.png")
    selected = {"candidate_ids": [chosen["id"]]}
    received = client.post(f"{target}/receive", headers=headers, json=selected)
    assert received.status_code == 200, received.text
    received_items = [item for item in received.json()["candidates"] if item["state"] == "received"]
    assert len(received_items) == 1, received.json()
    first_asset_id = received_items[0]["asset_id"]
    assert client.post(f"{target}/receive", headers=headers, json=selected).status_code == 200
    with repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM material_assets").fetchone()[0] == 1
    assert repository.get_asset("local", first_asset_id).owner_user_id == owner.member_id
    assert len(list(repository.video_library_path.iterdir())) == 1
    assert client.get(f"{target}/candidates/{chosen['id']}/preview", headers=other).status_code == 404
    assert client.get(f"{target}/candidates/{chosen['id']}/preview", headers=headers).content == b"isolated source A"
    assert violations == []


def test_running_input_http_replays_original_receipt_and_consumes_at_sdk_boundary(tmp_path, monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(main, "_creation_task_script_generator_for", forbidden)
    from app.conversation_repository import ConversationRepository
    client, _, owner, headers, other = _sampling_context(tmp_path, monkeypatch)
    conversations = ConversationRepository(tmp_path / "conversations.sqlite3")
    monkeypatch.setattr(main, "conversation_repo", conversations)
    chat = conversations.create("local", owner.member_id, "home", "home")
    conversations.save_draft(chat["id"], "local", owner.member_id, "准备一段视频", 0)
    base = f"/api/assistant/conversations/{chat['id']}/inputs"
    observed = {}

    def run_plan(_settings, **arguments):
        conversations.save_draft(chat["id"], "local", owner.member_id, "先改成十秒", 2)
        payload = {"client_request_id": str(uuid4()), "target_turn_id": arguments["turn_id"],
                   "mode": "steer", "message": "先改成十秒", "expected_draft_version": 3,
                   "expected_reference_version": 0}
        accepted = client.post(base, headers=headers, json=payload)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["input"]["status"] == "accepted"
        assert accepted.json()["draft_version"] == 4
        assert client.get(base, headers=other).status_code == 404
        conversations.save_reference(chat["id"], "local", owner.member_id, None, 0)
        assert client.post(base, headers=headers, json=payload).json() == accepted.json()
        assert client.post(base, headers=headers, json={**payload, "message": "另一个要求"}).status_code == 409
        delivery = arguments["input_handler"]
        offered = delivery("next", mode="steer")
        assert offered["input_id"] == payload["client_request_id"]
        verified = delivery("consume", offered["input_id"])
        assert verified["environment"]["AI_VIDEO_DIARY_AGENT_INPUT_ID"] == offered["input_id"]
        assert verified["environment"]["AI_VIDEO_DIARY_AGENT_INPUT_TURN_ID"] == arguments["turn_id"]
        assert verified["environment"]["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] == "先改成十秒"
        assert verified["environment"]["AI_VIDEO_DIARY_AGENT_SELECTED_REFS"] == "[]"
        assert verified["environment"]["AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES"] == ""
        assert client.post(f"{base}/{offered['input_id']}/withdraw", headers=headers).status_code == 409
        delivery("complete", offered["input_id"])
        observed.update(input_id=offered["input_id"], turn_id=arguments["turn_id"])
        return "已处理本次要求", None

    monkeypatch.setattr(main, "run_plan_turn", run_plan)
    response = client.post("/api/assistant/plan/turn", headers=headers, json={
        "conversation_id": chat["id"], "client_request_id": str(uuid4()),
        "expected_draft_version": 1, "expected_reference_version": 0, "message": "准备一段视频"})
    assert response.status_code == 200, response.text
    receipts = client.get(base, headers=headers).json()["items"]
    assert [(item["input_id"], item["status"]) for item in receipts] == [(observed["input_id"], "completed")]
    reopened = ConversationRepository(conversations.database_path)
    assert reopened.list_inputs(chat["id"], "local", owner.member_id) == receipts
    assert conversations.get(chat["id"], "local", owner.member_id)["draft_version"] == 4


def test_first_turn_rejects_stale_reference_version_without_consuming_draft(tmp_path, monkeypatch):
    from app.conversation_repository import ConversationRepository
    client, _, owner, headers, _ = _sampling_context(tmp_path, monkeypatch)
    conversations = ConversationRepository(tmp_path / "references.sqlite3")
    monkeypatch.setattr(main, "conversation_repo", conversations)
    chat = conversations.create("local", owner.member_id, "home", "home")
    conversations.save_draft(chat["id"], "local", owner.member_id, "修改这一段", 0)
    conversations.save_reference(chat["id"], "local", owner.member_id, None, 0)
    def unexpected_run(*args, **kwargs):
        raise AssertionError("stale selection must not launch a runtime")
    monkeypatch.setattr(main, "run_plan_turn", unexpected_run)
    result = client.post("/api/assistant/plan/turn", headers=headers, json={
        "conversation_id": chat["id"], "client_request_id": str(uuid4()),
        "expected_draft_version": 1, "expected_reference_version": 0, "message": "修改这一段"})
    assert result.status_code == 409, result.text
    current = conversations.get(chat["id"], "local", owner.member_id)
    assert current["draft_text"] == "修改这一段" and current["draft_version"] == 1
    assert conversations.list_turns(chat["id"]) == []


def test_execution_finish_keeps_original_input_binding_after_steering(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess
    import pytest
    from app.assistant.execution_binding import execution_locator_id

    def forbidden(*args, **kwargs):
        pytest.fail('original binding representative forbids media helpers, Popen, models and drivers')
    # Install before any seed context/helper import, with no ASGI lifespan start.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    import _media_preview_test_fixtures as media_helpers
    import test_creation_execution as creation_helpers
    for helpers in (media_helpers, creation_helpers):
        for name in ('write_synthetic_video', 'register_synthetic_video_asset',
                     'register_synthetic_profiles', 'synthetic_wav_bytes'):
            monkeypatch.setattr(helpers, name, forbidden)
    monkeypatch.setattr(main, 'run_task_turn', forbidden)
    monkeypatch.setattr(main, 'run_plan_turn', forbidden)
    monkeypatch.setattr(main, 'start_assistant_continuation_driver', forbidden)
    monkeypatch.setattr(main.montage_renderer, 'render', forbidden)
    from app.creation.contracts import TaskEvent

    _, repository, owner, _, _ = _sampling_context(tmp_path, monkeypatch)
    # Isolated ledger seam: the host read has already verified the real job.
    source = {"assistant_turn_id": "turn", "input_id": "input-old"}
    event = TaskEvent(id=execution_locator_id("turn", "input-old"),
        workspace_id="local", task_id="task", job_id="job", event_type="assistant_execution_located",
        payload={"source": source, "job_id": "job", "input_digest": "digest"})
    monkeypatch.setattr(repository.execution_records, "list_task_events", lambda *_: [event])
    monkeypatch.setattr(repository.execution_records, "get_task_event", lambda *_: event)
    receipt = {"job": {"id": "job", "input_digest": "digest", "status": "succeeded"}}
    records = []
    monkeypatch.setattr(main, "_read_bound_execution_result", lambda *_: receipt)
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "local")
    monkeypatch.setattr(main, "_assistant_member_id", lambda *_: owner.member_id)
    monkeypatch.setattr(repository.task_records, "get_creation_task", lambda *_: SimpleNamespace(id="task"))
    monkeypatch.setattr(main, "_goal_projection", lambda: SimpleNamespace(
        execution_source_current=lambda *_: True))
    monkeypatch.setattr(main, "conversation_repo", SimpleNamespace(
        record_task_execution=lambda *args, **kwargs: records.append(kwargs)))
    monkeypatch.setattr(main, "_wake_assistant_continuations", lambda: None)
    assert main._bind_assistant_execution_result("task", object(), "turn", receipt) == receipt
    assert records == [{"current_source": True, "input_id": "input-old"}]
    assert execution_locator_id("turn", "input-new") != event.id
    assert execution_locator_id("turn") == "event_assistant_execution_locator_turn"
    proofs = main._task_turn_http_adapter().case().action_proofs("local", "task", "turn",
        media_job=None, edit_preview=None, edit_request=None, execution_status=receipt)
    assert proofs == [{"kind": "execution_status", "id": "job", "input_id": "input-old"}]


def test_late_input_request_is_rejected_but_matching_host_input_is_accepted():
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request

    source = {"assistant_turn_id": "turn", "input_id": "new-input"}
    for header in (None, "old-input"):
        request = Request({"type": "http", "headers": [(b"x-assistant-input-id", header.encode())] if header else []})
        with pytest.raises(HTTPException) as error:
            main._require_assistant_input_origin(request, source)
        assert error.value.status_code == 409
    request = Request({"type": "http", "headers": [(b"x-assistant-input-id", b"new-input")]})
    main._require_assistant_input_origin(request, source)


def test_received_material_reference_is_atomic_and_restored_by_rescan(tmp_path, monkeypatch):
    import pytest
    from app.materials.contracts import MediaAuthorityError
    client, repository, owner, headers, _ = _sampling_context(tmp_path, monkeypatch)
    path = repository.video_library_path / 'owned.mp4'
    path.write_bytes(b'synthetic metadata only')
    asset = repository._upsert_discovered_asset('local', path, owner_user_id=owner.member_id)
    asset.analysis_status = 'ready'
    asset.review_status = 'approved'
    asset.user_description = '已确认的人工说明'
    repository._save_asset(asset)
    reference = repository.workspace_media_reference_for_asset('local', asset.id)
    assert repository.resolve_workspace_media_reference('local', reference, require_registered=True) == path
    with repository._connect() as conn:
        conn.execute('DELETE FROM workspace_media_references WHERE reference=?', (reference,))
    repository.material_source_files.bind_material_ingestion('local', expected_directory=str(repository.video_library_path), verified_owner_user_id=owner.member_id)
    repository.material_source_files.confirm_material_copy_complete('local', relative_path=path.name,
        expected_revision=repository.material_source_files.material_copy_revision(path.stat()), confirmation_source='app_import',
        verified_owner_user_id=owner.member_id)
    response = client.post('/api/production/materials/scan', headers=headers)
    assert response.status_code == 200
    assert response.json()['assets'][0]['id'] == asset.id
    restored = repository.get_asset('local', asset.id)
    assert (restored.owner_user_id, restored.analysis_status, restored.review_status,
            restored.user_description) == (owner.member_id, 'ready', 'approved', '已确认的人工说明')
    assert repository.resolve_workspace_media_reference('local', reference, require_registered=True) == path
    conflict = repository.video_library_path / 'conflict.mp4'
    conflict.write_bytes(b'another synthetic metadata input')
    with repository._connect() as conn:
        repository._claim_workspace_media_reference(conn, workspace_id='other', reference='workspace://conflict.mp4')
    with pytest.raises(MediaAuthorityError):
        repository._upsert_discovered_asset('local', conflict, owner_user_id=owner.member_id)
    with repository._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM material_assets WHERE file_path=?', (str(conflict),)).fetchone()[0] == 0
        assert conn.execute("SELECT workspace_id FROM workspace_media_references WHERE reference='workspace://conflict.mp4'").fetchone()[0] == 'other'
