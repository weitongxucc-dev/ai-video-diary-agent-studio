"""Synthetic/isolated API and queue consumers; not model or GUI acceptance."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import io
import os
import wave
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.production import (
    ProductionRepository,
    MaterialAsset,
    MaterialSegment,
    automatic_segment_eligible,
)
from app.avatar_lipsync import (
    PersonProfile,
    ProfileReference,
    VoiceProfile,
    AvatarPreviewService,
    UnavailableAvatarAndLipSyncProvider,
)
from app.narration import VoicePreviewService
from app.creation_execution import CreationExecutionActor
from app.profile_preview_execution import (
    profile_preview_registration,
    submit_profile_preview,
)
from app.device_runtime import build_device_runtime
from app.device_scheduler import ProcessIdentity
from app.host_resource_probes import NarrationBudget
from app.voice_clone import VoiceCloneResult


def wav_bytes():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x01\x00" * 16000)
    return buf.getvalue()


@pytest.fixture
def repo(tmp_path, monkeypatch, request):
    if request.node.originalname in {
        "test_material_search_reader_filters_private_before_limit",
        "test_automation_and_coverage_keep_observation",
    }:
        from test_production_foundation import edit_adoption_no_execution
        forbidden = edit_adoption_no_execution(monkeypatch)
        monkeypatch.setattr(main, "_creation_task_script_generator_for", forbidden)
    return ProductionRepository(
        database_path=tmp_path / "db.sqlite3",
        video_library_path=tmp_path / "library",
        duration_reader=lambda _: 2,
        scene_reader=lambda *_: [],
    )


@pytest.fixture
def client(repo, monkeypatch):
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(
        main,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "ws"),
    )
    monkeypatch.setattr(
        main,
        "_content_reader",
        lambda request, ws: (request.headers.get("x-test-member", "member"), False),
    )
    return TestClient(main.app)


def test_independent_registration_default_fixed_reopen_and_reader(client, repo):
    imported = client.post(
        "/api/media/voice-references/import",
        files={"audio": ("synthetic.wav", wav_bytes(), "audio/wav")},
    )
    assert imported.status_code == 201, imported.text
    payload = {
        "id": "voice",
        "display_name": "独立声音",
        "language": "zh",
        "reference_audio_path": imported.json()["reference"],
    }
    saved = client.post("/api/media/voice-profiles", json=payload)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (
        body["authorization"] is None
        and body["person_profile_id"] == ""
        and body["model_id"] == ""
    )
    assert body["reference"]["asset_id"] and body["reference_url"]
    assert client.get("/api/media/voice-profiles").json()["items"][0] == body
    assert client.get(body["reference_url"]).content == wav_bytes()
    assert (
        client.put(
            "/api/media/default-voice",
            json={"profile_id": "voice", "version": body["version"]},
        ).status_code
        == 200
    )
    updated = client.post(
        "/api/media/voice-profiles", json={**payload, "display_name": "新名称"}
    )
    assert updated.status_code == 200 and updated.json()["version"] != body["version"]
    assert client.get("/api/media/default-voice").json()["version"] == body["version"]
    assert (
        client.get(
            "/api/media/voice-profiles", headers={"x-test-member": "other"}
        ).json()["items"]
        == []
    )
    assert (
        client.get(
            body["reference_url"], headers={"x-test-member": "other"}
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/media/voice-profiles", headers={"x-test-workspace": "other"}
        ).json()["items"]
        == []
    )


def test_person_registration_no_model_and_member_write_boundary(client, repo):
    from PIL import Image

    path = repo.video_library_path / "portrait.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2)).save(path)
    asset = repo._save_asset(
        MaterialAsset(
            id="portrait",
            workspace_id="ws",
            file_name=path.name,
            path=str(path),
            size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            media_type="image",
            owner_user_id="member",
        )
    )
    payload = {
        "id": "person",
        "display_name": "自制图",
        "avatar_reference": repo.workspace_media_reference_for_asset("ws", asset.id),
        "reference": {"asset_id": asset.id},
    }
    saved = client.post("/api/media/person-profiles", json=payload)
    assert saved.status_code == 200, saved.text
    assert (
        saved.json()["authorization"] is None
        and saved.json()["body_reference_available"]
    )
    assert (
        client.post(
            "/api/media/person-profiles",
            json=payload,
            headers={"x-test-member": "other"},
        ).status_code
        == 403
    )
    path.unlink()
    again = client.get("/api/media/person-profiles")
    assert (
        again.status_code == 200
        and not again.json()["items"][0]["body_reference_available"]
    )


def test_automation_and_coverage_keep_observation(repo, tmp_path):
    path = repo.video_library_path / "synthetic.mp4"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"isolated metadata fixture, not claimed decoded video")
    segment = MaterialSegment(
        id="s",
        asset_id="a",
        start_seconds=0,
        end_seconds=2,
        description="办公画面",
        system_tags=["戴眼镜女性", "笔记本", "办公室"],
        usage_role="a_roll",
    )
    asset = repo._save_asset(
        MaterialAsset(
            id="a",
            workspace_id="ws",
            file_name=path.name,
            path=str(path),
            size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            duration_seconds=2,
            analysis_status="ready",
            segments=[segment],
            owner_user_id="member",
        )
    )
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(
            conn,
            workspace_id="ws",
            reference=repo.workspace_media_reference_for_asset("ws", asset.id),
        )
    assert automatic_segment_eligible(asset, segment, "ws")
    assert len(repo.automatic_material_segments("ws", viewer_user_id="member")) == 1
    for value in ["reference_only", "excluded", "not_for_automation"]:
        assert not automatic_segment_eligible(
            replace(asset, use_state=value), segment, "ws"
        )
    assert not automatic_segment_eligible(
        asset, replace(segment, review_status="rejected"), "ws"
    )
    assert not automatic_segment_eligible(asset, replace(segment, end_seconds=3), "ws")
    assert repo.automatic_material_segments("ws", viewer_user_id="other") == []
    insights = repo.library_insights("ws", viewer_user_id="member")
    assert not {"主体:人物", "物体:电脑", "场景:办公"} & set(
        insights["recommended_gaps"]
    )
    assert {"戴眼镜女性", "笔记本", "办公室"} <= {i["tag"] for i in insights["tags"]}

    from copy import deepcopy
    from dataclasses import asdict
    import sqlite3
    from app.materials.library_insights import material_library_insights

    # Fixed value inputs: the policy must never mutate the caller's observations.
    first_segment = replace(segment, system_tags=['repeat', 'repeat', '办公室'],
        user_tags=['repeat', ''], usage_count=3)
    second_segment = replace(segment, id='second-segment', start_seconds=2, end_seconds=4,
        system_tags=['repeat'], user_tags=['repeat'], usage_count=4)
    first_asset = replace(asset, duration_seconds=4, user_tags=['repeat', 'repeat', 'asset-only', ''],
        segments=[first_segment, second_segment])
    second_asset = replace(asset, id='second-value-asset', user_tags=['repeat'], duration_seconds=2,
        segments=[replace(segment, id='third-segment', asset_id='second-value-asset',
            start_seconds=0, end_seconds=1.23456, system_tags=['repeat'], user_tags=['repeat'], usage_count=5)])
    values = (first_asset, second_asset)
    untouched = deepcopy([asdict(item) for item in values])
    projected = material_library_insights(values)
    assert [asdict(item) for item in values] == untouched
    assert (projected['asset_count'], projected['segment_count']) == (2, 3)
    assert [item['tag'] for item in projected['tags']] == [
        'repeat', '', '办公室', 'asset-only', '主体:人物', '内容:软件界面', '动作:打字',
        '场景:办公', '场景:户外', '物体:电脑', '镜头:特写']
    tags = {item['tag']: item for item in projected['tags']}
    assert tags['repeat'] == {'tag': 'repeat', 'asset_count': 2, 'segment_count': 3,
        'duration_seconds': 5.235, 'usage_count': 12}
    assert tags['asset-only'] == {'tag': 'asset-only', 'asset_count': 1, 'segment_count': 0,
        'duration_seconds': 0.0, 'usage_count': 0}
    assert tags[''] == {'tag': '', 'asset_count': 0, 'segment_count': 1,
        'duration_seconds': 2.0, 'usage_count': 3}
    assert tags['主体:人物']['asset_count'] == tags['主体:人物']['segment_count'] == 0
    assert projected['recommended_gaps'] == ['内容:软件界面', '动作:打字', '场景:户外', '物体:电脑', '镜头:特写']

    # A second private asset is actual persisted input, never a mocked visible list.
    other_path = repo.video_library_path / 'other-private.mp4'
    other_path.write_bytes(b'isolated other-member metadata; no media decoding')
    repo._save_asset(replace(asset, id='other-private', owner_user_id='other', path=str(other_path),
        file_name=other_path.name, size_bytes=other_path.stat().st_size,
        segments=[replace(segment, id='other-private-segment', asset_id='other-private',
            system_tags=['other-private-tag'], user_tags=[])], user_tags=['other-private-tag']))
    reopened = ProductionRepository(repo.database_path, repo.video_library_path,
        duration_reader=lambda _: 2, scene_reader=lambda *_: [])

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
    visible = reopened.list_assets('ws', viewer_user_id='member', local_diagnostic=False)
    assert [item.id for item in visible] == [asset.id]
    assert reopened.library_insights('ws', viewer_user_id='member') == material_library_insights(visible) == insights
    other = reopened.library_insights('ws', viewer_user_id='other')
    assert other['asset_count'] == 1 and other['segment_count'] == 1
    assert 'other-private-tag' in {item['tag'] for item in other['tags']}
    assert 'other-private-tag' not in {item['tag'] for item in insights['tags']}
    # Legacy repository calls without a viewer include private assets; this is not anonymous HTTP.
    assert reopened.library_insights('ws')['asset_count'] == 2
    assert snapshot() == before


class VoiceProvider:
    ready = True
    model_id = "fixture-model"
    message = "test"
    supported_languages = ("zh",)
    calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        assert kwargs["grant_context"].grant.owner_type == "media_preview"
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(wav_bytes())
        return VoiceCloneResult(
            audio_path=str(out), sample_rate=8000, duration_seconds=2
        )


class Resources:
    admit_value = False

    def admit(self, request):
        from app.host_resource_probes import HostResourceDecision
        return HostResourceDecision(self.admit_value, dict(request.resources), {})

    def released(self, grant):
        return True

    def continue_unit(self, grant):
        return True


class Processes:
    def current(self):
        return ProcessIdentity(os.getpid(), "fixture-start", "fixture-boot")

    def children_released(self, children):
        return True

    def recovery_state(self, holder, children):
        return "released"


class Access:
    def can_view_device_summary(self, actor):
        return True


def preview_record_guards(monkeypatch):
    import subprocess
    from app.device_runtime import DeviceRuntime
    import _media_preview_test_fixtures as helpers
    import test_creation_execution as creation_helpers
    def forbidden(*args, **kwargs):
        pytest.fail('preview-record evidence forbids provider, waveform, Popen, runtime, model and driver execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'start', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'run_once', forbidden)
    monkeypatch.setattr(VoiceProvider, 'generate', forbidden)
    monkeypatch.setattr(UnavailableAvatarAndLipSyncProvider, 'render', forbidden)
    monkeypatch.setattr(main, 'run_task_turn', forbidden)
    monkeypatch.setattr(main, 'start_assistant_continuation_driver', forbidden)
    for owner in (helpers, creation_helpers):
        for name in ('write_synthetic_video', 'register_synthetic_video_asset', 'register_synthetic_profiles', 'synthetic_wav_bytes'):
            monkeypatch.setattr(owner, name, forbidden)
    return forbidden


def record_only_preview(repo, tmp_path, monkeypatch, *, before_submit=None):
    """Real profile/reference metadata and scheduler; declared bytes, no media execution."""
    forbidden = preview_record_guards(monkeypatch)
    root = repo.video_library_path
    root.mkdir(parents=True, exist_ok=True)
    path = root/'declared-reference.mp4'
    path.write_bytes(b'declared registered reference metadata only')
    stat = path.stat()
    asset = repo._save_asset(MaterialAsset(id='declared-reference', workspace_id='ws',file_name=path.name,
        path=str(path),size_bytes=stat.st_size,modified_at=datetime.fromtimestamp(stat.st_mtime),
        duration_seconds=2,media_type='video',analysis_status='ready',review_status='approved',owner_user_id='member'))
    ref = repo.workspace_media_reference_for_asset('ws', asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='ws', reference=ref)
    voice = VoiceProfile(id='voice',workspace_id='ws',version='v1',language='zh',owner_user_id='member',
        display_name='declared voice',reference_audio_path=ref,reference_text='已声明参考文字',model_id='fixture-model',
        reference=ProfileReference(asset_id=asset.id))
    # Seed an already registered profile fact; registration's real ffprobe is outside this evidence.
    with repo._connect() as conn:
        repo._profile_records._save_versioned_voice_profile(conn, voice)
    provider = VoiceProvider()
    service = VoicePreviewService(tmp_path/'output',provider)
    budget = NarrationBudget(100,1,10**6,5,10**6,1,10,10**6,2,0.1,10**6,10**6)
    reg = profile_preview_registration(repo,voice_service=service,
        avatar_service=AvatarPreviewService(tmp_path/'output',UnavailableAvatarAndLipSyncProvider()),
        narration_budget=budget,presenter_budget=None,verify_identity=lambda actor:'ready')
    resources = Resources()
    runtime = build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'record-device.lock',
        device_access=Access(),registrations={'media_preview':reg},resources=resources,processes=Processes())
    # The executor is forbidden too: tests call only the real scheduler's transactional owner callback.
    monkeypatch.setattr(reg.executor, 'execute', forbidden)
    actor = CreationExecutionActor('ws','member')
    params = {'voice_profile_id':voice.id,'voice_version':voice.version,'preview_text':'metadata binding check'}
    prepared = service.request(repository=repo,workspace_id='ws',viewer_user_id='member',prepare_only=True,**params)
    assert prepared.status=='queued' and provider.calls==0
    if before_submit is not None:
        before_submit(runtime,resources,provider)
    queued = submit_profile_preview(runtime,prepared,actor,params)
    return runtime,resources,provider,queued


def setup_preview(
    repo, tmp_path, provider=None, before_submit=None, authorization=None
):
    ref, _, _ = repo.import_voice_reference_wav(
        "ws", wav_bytes(), verified_owner_user_id="member"
    )
    voice = repo.save_versioned_voice_profile(
        VoiceProfile(
            id="voice",
            workspace_id="ws",
            version="v1",
            language="zh",
            display_name="声音",
            reference_audio_path=ref,
            reference_text="合成文字",
            model_id="fixture-model",
            reference=ProfileReference(),
            authorization=authorization,
        ),
        verified_owner_user_id="member",
    )
    provider = provider or VoiceProvider()
    service = VoicePreviewService(tmp_path / "output", provider)
    budget = NarrationBudget(
        100, 1, 10**6, 5, 10**6, 1, 10, 10**6, 2, 0.1, 10**6, 10**6
    )
    reg = profile_preview_registration(
        repo,
        voice_service=service,
        avatar_service=AvatarPreviewService(
            tmp_path / "output", UnavailableAvatarAndLipSyncProvider()
        ),
        narration_budget=budget,
        presenter_budget=None,
        verify_identity=lambda actor: "ready",
    )
    resources = Resources()
    runtime = build_device_runtime(
        database_path=repo.database_path,
        device_lock_path=tmp_path / "device.lock",
        device_access=Access(),
        registrations={"media_preview": reg},
        resources=resources,
        processes=Processes(),
    )
    actor = CreationExecutionActor("ws", "member")
    params = {
        "voice_profile_id": voice.id,
        "voice_version": voice.version,
        "preview_text": "简短合成测试",
    }
    prepared = service.request(
        repository=repo,
        workspace_id="ws",
        viewer_user_id="member",
        prepare_only=True,
        **params,
    )
    if before_submit is not None:
        before_submit(runtime, resources, provider)
    queued = submit_profile_preview(runtime, prepared, actor, params)
    return runtime, resources, provider, queued


@pytest.mark.parametrize("binding_time", ["after_terminal", "during_provider"])
@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
def test_preview_binding_tick_before_submitter_bind(
    repo, tmp_path, monkeypatch, binding_time, outcome
):
    original_bind = repo.preview_records.bind_preview_device_request
    phases = []
    runtime_ref = []

    def arrange(runtime, resources, provider):
        runtime_ref.append(runtime)
        resources.admit_value = True
        generate = provider.generate

        def delayed_bind(run, request_id, *, conn=None):
            if conn is not None:
                return original_bind(run, request_id, conn=conn)

            def interleaved_generate(**kwargs):
                if binding_time == "during_provider":
                    bound = original_bind(run, request_id)
                    assert bound.status == "running"
                if outcome == "succeeded":
                    return generate(**kwargs)
                provider.calls += 1
                if outcome == "failed":
                    raise OSError("synthetic provider failure")
                assert (
                    runtime.scheduler.cancel(
                        request_id, CreationExecutionActor("ws", "member")
                    )["state"]
                    == "yield_requested"
                )
                runtime.registrations["media_preview"].executor.request_cancel(
                    request_id
                )
                kwargs["grant_context"].check()
                raise AssertionError("cancelled provider must not produce an artifact")

            provider.generate = interleaved_generate
            assert runtime.run_once()
            terminal = repo.preview_records.get_media_preview_run("ws", "voice", run.identity)
            phases.append(terminal)
            # Inspect the committed checkpoint before the submitter can repair it.
            assert terminal.device_request_id == request_id
            rebound = original_bind(run, request_id)
            assert rebound == terminal
            return rebound

        monkeypatch.setattr(repo.preview_records, "bind_preview_device_request", delayed_bind)

    try:
        runtime, _, provider, final = setup_preview(
            repo, tmp_path, before_submit=arrange
        )
        assert final.status == outcome and final == phases[0]
        assert (
            final.finished_at is not None
            and final.preview_input["preview_text"] == "简短合成测试"
        )
        assert (
            final.member_identity
            == CreationExecutionActor("ws", "member").identity_payload()
        )
        assert provider.calls == 1
        if outcome == "succeeded":
            assert Path(final.output_path).read_bytes() == wav_bytes()
        else:
            assert final.output_path is None and final.reason is not None
        with repo._connect() as conn:
            request = conn.execute(
                "SELECT state,checkpoint_ref,work_class FROM device_work_requests WHERE id=?",
                (final.device_request_id,),
            ).fetchone()
            assert tuple(request) == (
                {"succeeded": "done", "failed": "failed", "cancelled": "cancelled"}[
                    outcome
                ],
                None if outcome == "failed" else final.id,
                "normal_make",
            )
            assert conn.execute(
                "SELECT state,request_id FROM device_permit"
            ).fetchone()[:] == ("free", None)
        assert not runtime.run_once() and provider.calls == 1
    finally:
        assert runtime_ref[0].stop(1)


def test_preview_binding_queued_cancel_before_late_bind_preserves_terminal(
    repo, tmp_path, monkeypatch
):
    original_bind = repo.preview_records.bind_preview_device_request
    runtime_ref = []

    def arrange(runtime, resources, provider):
        runtime_ref.append(runtime)

        def delayed_bind(run, request_id, *, conn=None):
            if conn is not None:
                return original_bind(run, request_id, conn=conn)
            assert (
                runtime.scheduler.cancel(
                    request_id, CreationExecutionActor("ws", "member")
                )["state"]
                == "cancelled"
            )
            cancelled = repo.preview_records.save_media_preview_run(
                replace(
                    run,
                    status="cancelled",
                    finished_at=main.utc_now(),
                    reason="cancelled",
                    impact="Cancelled before execution",
                    repair_action="Explicit retry",
                )
            )
            bound = original_bind(run, request_id)
            assert bound == replace(cancelled, device_request_id=request_id)
            return bound

        monkeypatch.setattr(repo.preview_records, "bind_preview_device_request", delayed_bind)

    try:
        runtime, resources, provider, final = record_only_preview(
            repo, tmp_path, monkeypatch, before_submit=arrange
        )
        resources.admit_value = True
        assert (
            final.status == "cancelled"
            and provider.calls == 0
        )
        assert original_bind(final, final.device_request_id) == final
    finally:
        assert runtime_ref[0].stop(1)


def test_preview_binding_recovery_unknown_keeps_metadata_and_permit(
    repo, tmp_path, monkeypatch
):
    from app.device_scheduler import RecoveryBlocked

    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    recovered = None
    try:
        resources.admit_value = True
        monkeypatch.setattr(resources, "released", lambda grant: None)
        with pytest.raises(RecoveryBlocked, match="未确认释放"):
            runtime.run_once()
        pending = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        assert (
            pending.status == "running"
            and pending.device_request_id == queued.device_request_id
        )
        assert pending.finished_at is None and pending.output_path is None
        assert (
            pending.member_identity == queued.member_identity
            and pending.preview_input == queued.preview_input
        )
        assert (
            repo.preview_records.bind_preview_device_request(queued, queued.device_request_id)
            == pending
        )
        with repo._connect() as conn:
            assert conn.execute(
                "SELECT state,request_id FROM device_permit"
            ).fetchone()[:] == ("held", queued.device_request_id)
            assert (
                conn.execute(
                    "SELECT state FROM device_work_requests WHERE id=?",
                    (queued.device_request_id,),
                ).fetchone()[0]
                == "granted"
            )
        assert provider.calls == 1 and runtime.stop(1)
        processes = Processes()
        recovery = {"state": "unknown"}
        monkeypatch.setattr(
            processes, "recovery_state", lambda holder, children: recovery["state"]
        )
        recovered = build_device_runtime(
            database_path=repo.database_path,
            device_lock_path=tmp_path / "device.lock",
            device_access=Access(),
            registrations=runtime.registrations,
            resources=resources,
            processes=processes,
        )
        with pytest.raises(RecoveryBlocked):
            recovered.run_once()
        assert (
            repo.preview_records.get_media_preview_run("ws", "voice", queued.identity) == pending
            and provider.calls == 1
        )
        recovery["state"] = "released"
        monkeypatch.setattr(resources, "released", lambda grant: True)
        assert recovered.run_once()
        final = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        assert (
            final.status == "succeeded"
            and final.id == queued.id
            and final.device_request_id == queued.device_request_id
        )
        with repo._connect() as conn:
            assert (
                conn.execute(
                    "SELECT state FROM device_work_requests WHERE id=?",
                    (queued.device_request_id,),
                ).fetchone()[0]
                == "done"
            )
            assert conn.execute(
                "SELECT state,request_id FROM device_permit"
            ).fetchone()[:] == ("free", None)
        assert provider.calls == 2
    finally:
        assert runtime.stop(1)
        if recovered is not None:
            assert recovered.stop(1)


def test_preview_binding_stale_snapshot_cannot_erase_or_replace_request(repo, tmp_path, monkeypatch):
    runtime, resources, provider, queued = record_only_preview(repo, tmp_path, monkeypatch)
    try:
        unbound = replace(queued, device_request_id=None)
        assert repo.preview_records.save_media_preview_run(unbound) == queued
        with pytest.raises(ValueError, match="其他设备请求"):
            repo.preview_records.save_media_preview_run(
                replace(queued, device_request_id="other-request")
            )
        with pytest.raises(ValueError, match="输入不一致"):
            repo.preview_records.bind_preview_device_request(queued, "nonexistent-request")
        with pytest.raises(ValueError, match="输入不一致"):
            repo.preview_records.bind_preview_device_request(
                replace(queued, input_digest="changed"), queued.device_request_id
            )
        assert (
            repo.preview_records.get_media_preview_run("ws", "voice", queued.identity) == queued
            and provider.calls == 0
        )
        resources.admit_value = True
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None
        # Service terminal receipts stay provisional until the sole device transaction.
        from app.profile_preview_execution import _PreviewCheckpointRecords
        port = _PreviewCheckpointRecords(repo.preview_records)
        assert not hasattr(port, 'resolve_narration_inputs')
        declared = VoicePreviewService._finish(port, queued, status='failed', reason='declared_failure',
            impact='No provider execution in this evidence', repair_action='Explicit retry')
        assert repo.preview_records.get_media_preview_run('ws','voice',queued.identity)==queued
        runtime.scheduler.fail_unit(grant, reason='preview_failed', failure_write=lambda conn, _:
            repo.preview_records.checkpoint(declared, grant.request_id, connection=conn))
        final = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        with pytest.raises(ValueError, match="不可覆盖"):
            repo.preview_records.save_media_preview_run(unbound)
        assert (
            repo.preview_records.bind_preview_device_request(unbound, queued.device_request_id) == final
        )
        assert repo.preview_records.get_media_preview_run("ws", "voice", queued.identity) == final
    finally:
        assert runtime.stop(1)


@pytest.fixture
def private_search_assets(repo):
    for asset_id, owner, visibility, description in [
        ("a-two", "member-two", "private", "预算"),
        ("z-one", "member-one", "private", "预算"),
        ("shared", None, "shared", "公共镜头"),
    ]:
        path = repo.video_library_path / (asset_id + ".mp4")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"isolated search metadata fixture, not decoded media")
        segment = MaterialSegment(
            id=asset_id + "-segment",
            asset_id=asset_id,
            start_seconds=0,
            end_seconds=2,
            description=description,
            usage_role="b_roll",
        )
        repo._save_asset(
            MaterialAsset(
                id=asset_id,
                workspace_id="ws",
                file_name=path.name,
                path=str(path),
                size_bytes=path.stat().st_size,
                modified_at=datetime.fromtimestamp(path.stat().st_mtime),
                duration_seconds=2,
                analysis_status="ready",
                segments=[segment],
                owner_user_id=owner,
                visibility=visibility,
            )
        )


@pytest.mark.parametrize(
    "reader,owned", [("member-one", "z-one"), ("member-two", "a-two")]
)
def test_material_search_reader_filters_private_before_limit(
    repo, private_search_assets, reader, owned
):
    args = {"query": "预算", "usage_roles": ["b_roll"], "viewer_user_id": reader}
    first = repo.material_search.search_material_segments("ws", limit=1, **args)
    assert [item.asset_ref for item in first] == ["asset:" + owned]
    assert first[0].rank == 1
    assert {
        item.asset_ref
        for item in repo.material_search.search_material_segments("ws", limit=100, **args)
    } == {"asset:" + owned, "asset:shared"}

    # The same readable rows feed explanatory ranking; private rows cannot
    # influence qualification counts, the limit, or the reported candidate total.
    search = repo.material_search.search_material_segments
    report = search("ws", query="没有字面交集", usage_roles=["b_roll"],
                    viewer_user_id=reader, limit=1, include_eligibility=True)
    assert set(report) == {"matches", "qualification_message", "total_count", "truncated"}
    assert report["total_count"] == 2 and report["truncated"] is True
    match = report["matches"][0]
    assert match.asset_ref == min("asset:" + owned, "asset:shared")
    assert match.rank == 1 and match.match_tier == "structural"
    assert match.needs_confirmation and match.matched_conditions == ["用途 b_roll"]
    assert "文字未匹配，按结构化条件返回" in match.reasons
    assert match.reasons[-1] == "综合排第 1 名（本次候选 1 条）"
    assert match.duration_seconds == 2 and match.time_range == "00:00 – 00:02"
    assert match.evidence_excerpt and len(match.evidence_excerpt) <= 900
    assert "人工用途返回1条候选" in report["qualification_message"]
    assert "未提交画面语义推理" in report["qualification_message"]
    assert search("ws", viewer_user_id=reader, asset_ids=[], usage_roles=["b_roll"]) == []
    # IDs/pairs restrict scope but do not authorize structural fallback.
    assert search("ws", viewer_user_id=reader, asset_ids=[owned],
                  eligible_segment_refs={(owned, owned + "-segment")}, query="没有字面交集") == []
    current = repo.get_asset("ws", owned)
    repo._save_asset(replace(current, use_state="not_for_automation"))
    manual = search("ws", asset_ids=[owned], viewer_user_id=reader, purpose="manual",
                    include_eligibility=True)
    automatic = search("ws", asset_ids=[owned], viewer_user_id=reader, purpose="automatic",
                       include_eligibility=True)
    assert [item.asset_ref for item in manual["matches"]] == ["asset:" + owned]
    assert automatic["matches"] == [] and automatic["total_count"] == 0
    assert automatic["truncated"] is False
    assert "1项受本次用途限制" in automatic["qualification_message"]


def test_material_search_missing_reader_keeps_only_legacy_shared(
    repo, private_search_assets
):
    args = {"query": "预算", "usage_roles": ["b_roll"], "limit": 1}
    assert [item.asset_ref for item in repo.material_search.search_material_segments("ws", **args)] == [
        "asset:shared"
    ]
    assert repo.material_search.search_material_segments("other", **args) == []
    # Explicit existing local diagnostics are a different, controlled caller.
    assert [
        item.asset_ref
        for item in repo.material_search.search_material_segments("ws", local_diagnostic=True, **args)
    ] == ["asset:a-two"]


@pytest.fixture
def restricted_preview(repo, tmp_path):
    from datetime import timedelta, timezone
    from app.avatar_lipsync import Authorization
    from PIL import Image

    auth = Authorization(
        "auth",
        "a1",
        "synthetic owner",
        ("voice", "person_likeness", "avatar_video"),
        ("narration", "avatar_lipsync"),
        datetime.now(timezone.utc) - timedelta(days=1),
        None,
        "synthetic evidence",
        "active",
        "ws",
    )
    runtime, resources, provider, queued = setup_preview(
        repo, tmp_path, authorization=auth
    )
    try:
        resources.admit_value = True
        assert runtime.run_once()
        voice = repo.get_versioned_voice_profile("ws", "voice", "v1")
        path = repo.video_library_path / "person.png"
        Image.new("RGB", (2, 2)).save(path)
        asset = repo._save_asset(
            MaterialAsset(
                "person-source",
                "ws",
                path.name,
                str(path),
                path.stat().st_size,
                datetime.fromtimestamp(path.stat().st_mtime),
                media_type="image",
                owner_user_id="member",
            )
        )
        person = repo.save_person_profile(
            PersonProfile(
                "person",
                "ws",
                "synthetic",
                "p1",
                repo.workspace_media_reference_for_asset("ws", asset.id),
                (),
                (),
                authorization=auth,
                reference=ProfileReference(asset_id=asset.id),
            ),
            verified_owner_user_id="member",
        )

        class NoAvatarProvider:
            ready = True
            model_id = "avatar-fixture"
            supported_languages = ("zh",)
            message = "fixture"
            calls = 0

            def render(self, **kwargs):
                self.calls += 1
                raise AssertionError("restriction checks must not render")

        avatar_provider = NoAvatarProvider()
        avatar_service = AvatarPreviewService(tmp_path / "output", avatar_provider)
        audio = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        avatar_params = dict(
            person_id=person.id,
            person_version=person.version,
            voice_profile_id=voice.id,
            voice_version=voice.version,
            audio_path=audio.output_path,
            audio_cache_key=audio.identity,
            language="zh",
            motion="natural",
            expression="neutral",
            authorization_id=None,
            authorization_version=None,
            model_id=avatar_provider.model_id,
        )
        yield SimpleNamespace(
            runtime=runtime,
            voice_provider=provider,
            avatar_provider=avatar_provider,
            voice_service=runtime.registrations["media_preview"].owner.services[
                "voice"
            ],
            avatar_service=avatar_service,
            voice=voice,
            person=person,
            auth=auth,
            voice_params=queued.preview_input,
            avatar_params=avatar_params,
            audio=audio,
        )
    finally:
        assert runtime.stop(1)


@pytest.mark.parametrize("kind", ["voice", "avatar"])
@pytest.mark.parametrize("restriction", ["revoked", "expired", "excluded"])
def test_preview_restriction_exact_blocked_reason_no_cache_reuse(
    repo, restricted_preview, kind, restriction
):
    from datetime import timedelta, timezone

    f = restricted_preview
    if restriction in {"revoked", "expired"}:
        updated = replace(
            f.auth,
            version="a2",
            status="revoked" if restriction == "revoked" else "active",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
            if restriction == "expired"
            else None,
        )
        repo.save_authorization(updated)
        reason = "authorization_" + restriction
    elif kind == "voice":
        repo.save_versioned_voice_profile(
            replace(f.voice, version="v2", use_state="excluded"),
            verified_owner_user_id="member",
        )
        reason = "profile_excluded"
    else:
        repo.save_person_profile(
            replace(f.person, version="p2", use_state="excluded"),
            verified_owner_user_id="member",
        )
        reason = "profile_excluded"
    service = f.voice_service if kind == "voice" else f.avatar_service
    params = f.voice_params if kind == "voice" else f.avatar_params
    result = service.request(
        repository=repo,
        workspace_id="ws",
        viewer_user_id="member",
        prepare_only=True,
        **params,
    )
    assert (result.status, result.reason, result.output_path) == (
        "blocked",
        reason,
        None,
    )
    assert f.voice_provider.calls == 1 and f.avatar_provider.calls == 0
    assert repo.preview_records.get_media_preview_run("ws", "voice", f.audio.identity) == f.audio
    # Default facts/generation resolution remains strict, even though preview
    # preparation can read the reference and describe the explicit restriction.
    with pytest.raises(ValueError, match="受限"):
        if kind == "voice":
            repo.profile_reference_input(
                repo.resolve_narration_inputs(
                    workspace_id="ws",
                    episode_id="voice_preview",
                    voice_profile_id=f.voice.id,
                    voice_version=f.voice.version,
                ).voice,
                viewer_user_id="member",
            )
        else:
            repo.resolve_avatar_lipsync_request(
                workspace_id="ws", viewer_user_id="member", **f.avatar_params
            )


@pytest.mark.parametrize("kind", ["voice", "avatar"])
def test_preview_restriction_missing_source_is_media_error(
    repo, restricted_preview, kind
):
    f = restricted_preview
    profile = f.voice if kind == "voice" else f.person
    Path(
        repo.profile_reference_input(profile, viewer_user_id="member").source_path
    ).unlink()
    service = f.voice_service if kind == "voice" else f.avatar_service
    params = f.voice_params if kind == "voice" else f.avatar_params
    result = service.request(
        repository=repo,
        workspace_id="ws",
        viewer_user_id="member",
        prepare_only=True,
        **params,
    )
    assert (result.status, result.reason, result.output_path) == (
        "blocked",
        "reference_audio_invalid" if kind == "voice" else "reference_visual_invalid",
        None,
    )
    assert f.voice_provider.calls == 1 and f.avatar_provider.calls == 0


@pytest.mark.parametrize("kind", ["voice", "avatar"])
@pytest.mark.parametrize("private_scope", ["profile", "source"])
def test_preview_restriction_private_read_denied_before_authorization(
    repo, restricted_preview, kind, private_scope
):
    f = restricted_preview
    repo.save_authorization(replace(f.auth, version="a2", status="revoked"))
    profile = f.voice if kind == "voice" else f.person
    viewer = "other"
    if private_scope == "source":
        viewer = "member"
        asset = repo.get_asset("ws", profile.reference.asset_id)
        repo._save_asset(replace(asset, owner_user_id="other"))
        Path(asset.path).unlink()
    before = repo.preview_records.list_media_preview_runs("ws")
    service = f.voice_service if kind == "voice" else f.avatar_service
    params = f.voice_params if kind == "voice" else f.avatar_params
    with pytest.raises(PermissionError):
        service.request(
            repository=repo,
            workspace_id="ws",
            viewer_user_id=viewer,
            prepare_only=True,
            **params,
        )
    assert repo.preview_records.list_media_preview_runs("ws") == before
    assert f.voice_provider.calls == 1 and f.avatar_provider.calls == 0


@pytest.mark.parametrize("kind", ["voice", "avatar"])
def test_preview_restriction_fact_read_does_not_grant_generation(
    repo, restricted_preview, kind
):
    f = restricted_preview
    service = f.voice_service if kind == "voice" else f.avatar_service
    params = (
        {**f.voice_params, "preview_text": "另一条合成测试"}
        if kind == "voice"
        else f.avatar_params
    )
    result = service.request(
        repository=repo, workspace_id="ws", viewer_user_id="member", **params
    )
    assert (result.status, result.reason, result.output_path) == (
        "blocked",
        "device_grant_required",
        None,
    )
    assert f.voice_provider.calls == 1 and f.avatar_provider.calls == 0


@pytest.mark.parametrize("kind", ["voice", "avatar"])
def test_preview_source_excluded_is_explicit_use_restriction(
    repo, restricted_preview, kind
):
    f = restricted_preview
    profile = f.voice if kind == "voice" else f.person
    assert profile.use_state == "available"
    repo.update_asset_use_state("ws", profile.reference.asset_id, "excluded")
    service = f.voice_service if kind == "voice" else f.avatar_service
    params = f.voice_params if kind == "voice" else f.avatar_params
    result = service.request(
        repository=repo,
        workspace_id="ws",
        viewer_user_id="member",
        prepare_only=True,
        **params,
    )
    assert (result.status, result.reason, result.output_path) == (
        "blocked",
        "source_excluded",
        None,
    )
    assert "excluded" in result.repair_action
    assert f.voice_provider.calls == 1 and f.avatar_provider.calls == 0
    assert repo.preview_records.get_media_preview_run("ws", "voice", f.audio.identity) == f.audio
    assert Path(f.audio.output_path).is_file()
    with pytest.raises(ValueError, match="使用状态限制"):
        repo.profile_reference_input(profile, viewer_user_id="member")


def test_preview_queue_zero_provider_until_grant_and_atomic_success(repo, tmp_path):
    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    try:
        assert queued.status == "queued" and provider.calls == 0
        assert runtime.run_once() is False and provider.calls == 0
        resources.admit_value = True
        assert runtime.run_once()
        final = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        assert final.status == "succeeded", final
        assert (
            Path(final.output_path).read_bytes() == wav_bytes() and provider.calls == 1
        )
        assert not runtime.run_once() and provider.calls == 1
    finally:
        assert runtime.stop(1)


def test_preview_stale_and_cancel_do_not_generate(repo, tmp_path):
    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    try:
        voice = repo.get_versioned_voice_profile("ws", "voice", "v1")
        asset = repo.get_asset("ws", voice.reference.asset_id)
        repo.update_asset_use_state("ws", asset.id, "excluded")
        resources.admit_value = True
        assert not runtime.run_once() and provider.calls == 0
        result = runtime.scheduler.cancel(
            queued.device_request_id, CreationExecutionActor("ws", "member")
        )
        assert result["state"] == "cancelled" and provider.calls == 0
    finally:
        assert runtime.stop(1)


def test_preview_failure_is_not_success_and_preserves_input(repo, tmp_path):
    class Failing(VoiceProvider):
        def generate(self, **kwargs):
            self.calls += 1
            raise OSError("synthetic provider failure")

    runtime, resources, provider, queued = setup_preview(repo, tmp_path, Failing())
    try:
        resources.admit_value = True
        assert runtime.run_once()
        final = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        assert (
            final.status == "failed"
            and final.output_path is None
            and provider.calls == 1
        )
        assert final.preview_input["preview_text"] == "简短合成测试"
        assert not runtime.run_once() and provider.calls == 1
    finally:
        assert runtime.stop(1)


def test_preview_submission_failure_is_persisted_without_provider(
    repo, tmp_path, monkeypatch
):
    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    try:
        runtime.scheduler.cancel(
            queued.device_request_id, CreationExecutionActor("ws", "member")
        )
        repo.preview_records.save_media_preview_run(
            replace(
                queued,
                status="cancelled",
                finished_at=main.utc_now(),
                reason="cancelled",
                impact="Fixture cancelled",
                repair_action="Explicit retry",
            )
        )
        service = runtime.registrations["media_preview"].owner.services["voice"]
        params = {**queued.preview_input, "retry": True}
        prepared = service.request(
            repository=repo,
            workspace_id="ws",
            viewer_user_id="member",
            prepare_only=True,
            **params,
        )

        def rejected(*args):
            raise PermissionError("synthetic ledger refusal")

        monkeypatch.setattr(runtime.scheduler, "submit", rejected)
        with pytest.raises(PermissionError, match="ledger refusal"):
            submit_profile_preview(
                runtime, prepared, CreationExecutionActor("ws", "member"), params
            )
        last = repo.preview_records.get_media_preview_run("ws", "voice", prepared.identity)
        assert (
            last.status == "failed"
            and last.reason == "device_submission_failed"
            and provider.calls == 0
        )
    finally:
        assert runtime.stop(1)


def test_default_and_profile_usage_are_not_upgraded_on_reopen(client, repo):
    ref, _, _ = repo.import_voice_reference_wav(
        "ws", wav_bytes(), verified_owner_user_id="member"
    )
    payload = {
        "id": "limited",
        "language": "zh",
        "display_name": "仅参考",
        "reference_audio_path": ref,
        "use_state": "reference_only",
    }
    first = client.post("/api/media/voice-profiles", json=payload)
    assert first.status_code == 200 and not first.json()["runnable"]
    del payload["use_state"]
    second = client.post("/api/media/voice-profiles", json=payload)
    assert second.status_code == 200 and second.json()["use_state"] == "reference_only"
    assert (
        client.put(
            "/api/media/default-voice",
            json={"profile_id": "limited", "version": first.json()["version"]},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/media/default-voice", headers={"x-test-member": "other"}
        ).json()
        is None
    )
    assert client.put("/api/media/default-voice", json={}).status_code == 422


def test_plan_duration_pin_and_zero_person_do_not_query_lipsync(
    client, repo, monkeypatch
):
    from app.production import VideoTemplate
    from app.schemas import CreationPlanPreviewRequest
    from starlette.requests import Request

    def knowledge_for_use(workspace_id, *, purpose, user_id, local_diagnostic=False):
        assert workspace_id == "ws"
        assert purpose == "reference"
        return []

    def sources_for_use(workspace_id, *, purpose):
        assert workspace_id == "ws"
        assert purpose == "reference"
        return []

    monkeypatch.setattr(
        main,
        "source_repository",
        SimpleNamespace(
            list_knowledge_items_for_use=knowledge_for_use,
            list_source_items_for_use=sources_for_use,
            list_knowledge_items_readable_by=lambda *a, **kw: [],
            list_source_items=lambda *a: [],
        ),
    )
    monkeypatch.setattr(
        main, "model_catalog", SimpleNamespace(list_statuses=lambda: [])
    )

    def no_lipsync(*args):
        raise AssertionError("0 percent must not inspect lipsync provider")

    monkeypatch.setattr(main, "_lipsync_provider", no_lipsync)
    template = repo.save_video_template(
        VideoTemplate(
            id="t",
            workspace_id="ws",
            version=1,
            name="模板",
            origin="user",
            settings={
                "duration_target_seconds": 90,
                "language": "zh-CN",
                "aspect_ratio": "9:16",
            },
        ),
        verified_owner_user_id="member",
    )
    request = Request({"type": "http", "headers": []})
    base = {
        "goal": "合成目标",
        "template_id": template.id,
        "template_version": 1,
        "presenter_share_target": 0,
    }
    inherited = main._creation_plan_preflight(
        request, "ws", CreationPlanPreviewRequest(**base)
    )
    explicit = main._creation_plan_preflight(
        request, "ws", CreationPlanPreviewRequest(**base, duration_target_seconds=30)
    )
    confirm = main._creation_plan_preflight(
        request,
        "ws",
        main.CreationPlanConfirmRequest(
            **base,
            duration_target_seconds=30,
            preflight_fingerprint=explicit.preflight_fingerprint,
        ),
    )
    assert (
        inherited.duration_target_seconds == 90
        and explicit.duration_target_seconds == 30
    )
    assert (
        confirm.preflight_fingerprint
        == explicit.preflight_fingerprint
        != inherited.preflight_fingerprint
    )
    ref, _, _ = repo.import_voice_reference_wav(
        "ws", wav_bytes(), verified_owner_user_id="member"
    )
    voice = repo.save_versioned_voice_profile(
        VoiceProfile(
            id="independent",
            workspace_id="ws",
            version="v1",
            language="zh",
            display_name="声音",
            reference_text="",
            reference_audio_path=ref,
            reference=ProfileReference(),
        ),
        verified_owner_user_id="member",
    )
    person_path = repo.video_library_path / "portrait.png"
    from PIL import Image

    Image.new("RGB", (2, 2)).save(person_path)
    asset = repo._save_asset(
        MaterialAsset(
            id="p",
            workspace_id="ws",
            file_name=person_path.name,
            path=str(person_path),
            size_bytes=person_path.stat().st_size,
            modified_at=datetime.fromtimestamp(person_path.stat().st_mtime),
            media_type="image",
            owner_user_id="member",
        )
    )
    person = repo.save_person_profile(
        PersonProfile(
            id="p",
            workspace_id="ws",
            version="v2",
            display_name="人物",
            background_capabilities=(),
            shot_capabilities=(),
            avatar_reference=repo.workspace_media_reference_for_asset("ws", asset.id),
            reference=ProfileReference(asset_id=asset.id),
        ),
        verified_owner_user_id="member",
    )
    pinned = main._creation_plan_preflight(
        request,
        "ws",
        CreationPlanPreviewRequest(
            **base,
            selected_voice={"profile_id": voice.id, "version": voice.version},
            selected_person={"profile_id": person.id, "version": person.version},
        ),
    )
    assert (
        pinned.selected_voice.profile_id == voice.id
        and pinned.selected_voice.person_profile_id == ""
    )
    assert (
        pinned.selected_person.profile_id == person.id
        and pinned.selected_person.version == person.version
    )


def test_health_uses_workspace_effective_settings_and_current_call_only(
    client, monkeypatch
):
    from app import workspace_settings as store
    from app.config import Settings, record_llm_service_verification
    from app.runtime_health import _deepseek_item
    from app.models import utc_now

    base = Settings(
        llm_provider="minimax",
        llm_api_key="synthetic-old-env",
        llm_api_key_provider="minimax",
    )
    for ws in ("health-a", "health-b"):
        store.save_workspace_config(ws, {"llm_provider": "minimax"})
        store.save_workspace_secret(ws, "llm_api_key", "synthetic-test-secret")
    effective = {
        ws: store.workspace_settings(ws, base).settings
        for ws in ("health-a", "health-b")
    }
    record_llm_service_verification(
        effective["health-a"], workspace_id="health-a", ok=True, cause="verified"
    )
    assert (
        _deepseek_item(effective["health-a"], utc_now(), "health-a")["status"]
        == "ready"
    )
    assert (
        _deepseek_item(effective["health-b"], utc_now(), "health-b")["status"]
        == "degraded"
    )
    record_llm_service_verification(
        effective["health-a"], workspace_id="health-a", ok=False, cause="key_rejected"
    )
    assert (
        "密钥" in _deepseek_item(effective["health-a"], utc_now(), "health-a")["reason"]
    )
    store.delete_workspace_secret("health-a", "llm_api_key")
    assert (
        _deepseek_item(
            store.workspace_settings("health-a", base).settings, utc_now(), "health-a"
        )["status"]
        == "missing"
    )
    captured = []
    monkeypatch.setattr(main, "_workspace_settings_for", lambda ws: effective[ws])
    monkeypatch.setattr(
        main,
        "runtime_health",
        lambda settings, workspace_id: (
            captured.append((settings, workspace_id))
            or {
                "summary": {
                    "total": 0,
                    "ready": 0,
                    "missing": 0,
                    "degraded": 0,
                    "invalid": 0,
                },
                "items": [],
                "feature_gates": [],
            }
        ),
    )
    response = client.get(
        "/api/runtime/health", headers={"x-test-workspace": "health-b"}
    )
    assert response.status_code == 200, response.text
    assert captured == [(effective["health-b"], "health-b")]


def test_preview_unknown_admission_recovery_and_cache_stay_same_request(repo, tmp_path):
    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    try:
        resources.admit_value = None
        assert not runtime.run_once() and provider.calls == 0
        # Interrupted executor state; the existing queue grants after its process
        # recovery verifier declares the prior group released, not a second queue.
        repo.preview_records.save_media_preview_run(replace(queued, status="running"))
        resources.admit_value = True
        assert runtime.run_once() and provider.calls == 1
        final = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        assert (
            final.id == queued.id
            and final.device_request_id == queued.device_request_id
            and final.status == "succeeded"
        )
        service = runtime.registrations["media_preview"].owner.services["voice"]
        reused = service.request(
            repository=repo,
            workspace_id="ws",
            viewer_user_id="member",
            prepare_only=True,
            member_identity=queued.member_identity,
            **queued.preview_input,
        )
        assert (
            reused.id == queued.id
            and reused.status == "succeeded"
            and provider.calls == 1
        )
    finally:
        assert runtime.stop(1)


def test_changed_pronunciation_before_admission_does_not_use_old_queue(repo, tmp_path):
    from app.avatar_lipsync import PronunciationEntry

    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    try:
        repo.save_pronunciation_entry(
            PronunciationEntry(
                "rule", "ws", "AI", "zh", "诶艾", "workspace", None, "v1"
            )
        )
        resources.admit_value = True
        assert not runtime.run_once() and provider.calls == 0
        view = runtime.scheduler.view_for(
            queued.device_request_id, CreationExecutionActor("ws", "member")
        )
        assert view["wait_reason"] == "input_stale"
    finally:
        assert runtime.stop(1)


def test_api_preview_posts_queue_and_cancel_with_member_filtered_read(
    client, repo, tmp_path, monkeypatch
):
    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    monkeypatch.setattr(main, "_creation_runtime", lambda: runtime)
    monkeypatch.setattr(
        main,
        "voice_preview_service",
        runtime.registrations["media_preview"].owner.services["voice"],
    )
    monkeypatch.setattr(
        main,
        "_require_permit_actor",
        lambda request, ws: CreationExecutionActor(
            ws, request.headers.get("x-test-member", "member")
        ),
    )
    try:
        response = client.post("/api/media/previews/voice", json=queued.preview_input)
        assert response.status_code == 200, response.text
        assert (
            response.json()["id"] == queued.id
            and response.json()["status"] == "queued"
            and response.json()["output_url"] is None
        )
        assert provider.calls == 0
        assert (
            client.get(
                "/api/media/previews", headers={"x-test-member": "other"}
            ).json()["items"]
            == []
        )
        denied = client.post(
            f"/api/media/previews/voice/{queued.identity}/cancel",
            headers={"x-test-member": "other"},
        )
        assert denied.status_code == 404
        cancelled = client.post(f"/api/media/previews/voice/{queued.identity}/cancel")
        assert (
            cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        )
        resources.admit_value = True
        assert not runtime.run_once() and provider.calls == 0
        assert (
            repo.preview_records.get_media_preview_run("ws", "voice", queued.identity).preview_input
            == queued.preview_input
        )
    finally:
        assert runtime.stop(1)


def test_avatar_preview_uses_own_grant_and_independent_person_version(
    repo, tmp_path, monkeypatch
):
    from app.host_resource_probes import PresenterBudget
    from PIL import Image

    class AvatarProvider:
        ready = True
        model_id = "avatar-fixture"
        supported_languages = ("zh",)
        message = "fixture"
        calls = 0

        def render(self, **kwargs):
            self.calls += 1
            assert kwargs["grant_context"].grant.owner_type == "media_preview"
            assert kwargs["grant_context"].grant.unit_key.startswith("avatar_lipsync:")
            raise OSError("intentional synthetic avatar failure")

    runtime, resources, provider, queued = setup_preview(repo, tmp_path)
    try:
        resources.admit_value = True
        assert runtime.run_once()
        audio = repo.preview_records.get_media_preview_run("ws", "voice", queued.identity)
        path = repo.video_library_path / "avatar.png"
        Image.new("RGB", (2, 2)).save(path)
        asset = repo._save_asset(
            MaterialAsset(
                "avatar",
                "ws",
                path.name,
                str(path),
                path.stat().st_size,
                datetime.fromtimestamp(path.stat().st_mtime),
                media_type="image",
                owner_user_id="member",
            )
        )
        person = repo.save_person_profile(
            PersonProfile(
                "independent-person",
                "ws",
                "人物",
                "p1",
                repo.workspace_media_reference_for_asset("ws", asset.id),
                (),
                (),
                reference=ProfileReference(asset_id=asset.id),
            ),
            verified_owner_user_id="member",
        )
        avatar_provider = AvatarProvider()
        owner = runtime.registrations["media_preview"].owner
        owner.services["avatar_lipsync"] = AvatarPreviewService(
            tmp_path / "output", avatar_provider
        )
        owner.presenter_budget = PresenterBudget(
            1, 10**6, 5, 10**6, 1, 10, 10**6, 2, 0.1, 10**6, 10**6
        )
        params = dict(
            person_id=person.id,
            person_version=person.version,
            voice_profile_id="voice",
            voice_version="v1",
            audio_path=audio.output_path,
            audio_cache_key=audio.identity,
            language="zh",
            motion="natural",
            expression="neutral",
            authorization_id=None,
            authorization_version=None,
            model_id=avatar_provider.model_id,
        )
        service = owner.services["avatar_lipsync"]
        prepared = service.request(
            repository=repo,
            workspace_id="ws",
            viewer_user_id="member",
            prepare_only=True,
            member_identity=queued.member_identity,
            **params,
        )
        assert prepared.status == "queued"
        submitted = submit_profile_preview(
            runtime, prepared, CreationExecutionActor("ws", "member"), params
        )
        assert avatar_provider.calls == 0
        assert runtime.run_once()
        result = repo.preview_records.get_media_preview_run("ws", "avatar_lipsync", submitted.identity)
        assert (
            result.status == "failed"
            and result.output_path is None
            and result.person_version == "p1"
        )
        assert avatar_provider.calls == 1
        assert repo.get_person_profile("ws", person.id, person.version) == person
        assert repo.preview_records.get_media_preview_run("ws", "voice", queued.identity) == audio
    finally:
        assert runtime.stop(1)


def test_person_cache_validation_uses_actual_task_reader(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "production_repo", repo)
    render = SimpleNamespace(
        workspace_id="ws",
        task_id="task",
        person_profile_id="person",
        person_version="p1",
        cache_key="render-key",
        artifact_ref="artifact",
        motion="natural",
        expression="neutral",
        model_id="fixture",
    )
    segment = SimpleNamespace(
        audio_reference="audio",
        voice_profile_id="voice",
        voice_version="v1",
        language="zh",
    )
    facts = {"source": "exact-source-revision"}
    transform = SimpleNamespace(
        transform_type="person_render",
        status="succeeded",
        input_digest="render-key",
        output_refs=["artifact"],
        input_refs=["audio"],
        parameters={"language": "zh", "presenter_input_facts": facts},
    )
    monkeypatch.setattr(
        repo.task_records,
        "get_creation_task",
        lambda ws, task: SimpleNamespace(owner_user_id="member"),
    )
    monkeypatch.setattr(
        repo,
        "get_person_profile",
        lambda *args: SimpleNamespace(id="person", version="p1", authorization=None),
    )
    monkeypatch.setattr(
        repo,
        "resolve_authorized_narration_audio_reference",
        lambda *args: tmp_path / "synthetic.wav",
    )
    captured = []
    monkeypatch.setattr(
        repo,
        "resolve_avatar_lipsync_request",
        lambda **kw: captured.append(kw) or SimpleNamespace(),
    )
    monkeypatch.setattr(main, "presenter_input_facts", lambda request: facts)
    monkeypatch.setattr(
        "app.avatar_lipsync._authorization_failure", lambda request: None
    )
    assert main._person_render_matches_narration(render, segment, [transform])
    assert (
        captured[0]["viewer_user_id"] == "member"
        and captured[0]["person_version"] == "p1"
    )

    def denied(**kw):
        raise PermissionError("source sharing revoked")

    monkeypatch.setattr(repo, "resolve_avatar_lipsync_request", denied)
    assert not main._person_render_matches_narration(render, segment, [transform])
