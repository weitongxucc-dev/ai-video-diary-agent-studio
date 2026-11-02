"""Isolated persistence/qualification; no real provider, user media or GUI."""

import hashlib
import io
import os
import struct
import threading
import time
from pathlib import Path
import json
import subprocess
import wave
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from app.avatar_lipsync import (
    Authorization,
    PersonProfile,
    ProfileReference,
    VoiceProfile,
    profile_use_failure,
)
from app.production import (
    ProductionRepository,
    MaterialAsset,
    MaterialSegment,
    ProductionProfile,
)
from app.narration import VoicePreviewService, NarrationGenerator
from app.creation_execution import presenter_inputs, CreationStageGrantContext
from app.device_scheduler import Grant, ProcessIdentity
from app.host_resource_probes import (
    HostProcessProbe,
    HostResourceProbe,
    SceneRuntimeLimits,
)
from app.granted_subprocess import GrantedCommandCancelled
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationPlanPersonReference, CreationPlanVoiceReference, CreationTask
from app.avatar_lipsync import AvatarAndLipSyncService, avatar_lipsync_cache_key
from app.voice_clone import VoiceCloneError, VoiceCloneResult


@pytest.fixture
def repository(tmp_path):
    return ProductionRepository(
        database_path=tmp_path / "db.sqlite3",
        video_library_path=tmp_path / "library",
        duration_reader=lambda _: 2,
        scene_reader=lambda *_: [],
    )


def wav_bytes():
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 16000)
    return target.getvalue()


def voice(repository, **changes):
    ref, _, _ = repository.import_voice_reference_wav(
        "ws", wav_bytes(), verified_owner_user_id="member"
    )
    return replace(
        VoiceProfile(
            id="voice",
            workspace_id="ws",
            display_name="独立声音",
            version="v1",
            language="zh",
            reference_audio_path=ref,
            reference_text="合成参考",
            reference=ProfileReference(),
        ),
        **changes,
    )


def save_voice(repository, **changes):
    return repository.save_versioned_voice_profile(
        voice(repository, **changes), verified_owner_user_id="member"
    )


def image_asset(repository):
    root = repository.video_library_path
    root.mkdir(parents=True, exist_ok=True)
    path = root / "portrait.png"
    Image.new("RGB", (2, 2), (100, 100, 100)).save(path)
    return repository._save_asset(
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


def person(repository, asset):
    return PersonProfile(
        id="person",
        workspace_id="ws",
        display_name="合成人物",
        version="v1",
        avatar_reference=repository.workspace_media_reference_for_asset("ws", asset.id),
        background_capabilities=(),
        shot_capabilities=(),
        reference=ProfileReference(asset_id=asset.id),
    )


def test_independent_wav_registration_restart_and_default_pins_version(repository):
    saved = save_voice(repository)
    assert (
        not saved.person_profile_id
        and not saved.model_id
        and saved.authorization is None
    )
    assert saved.use_state == "available" and saved.owner_user_id == "member"
    repository.set_default_versioned_voice(
        "ws", saved.id, saved.version, viewer_user_id="member"
    )
    v2 = repository.save_versioned_voice_profile(
        replace(saved, version="v2", display_name="改名"),
        verified_owner_user_id="member",
    )
    reopened = ProductionRepository(
        database_path=repository.database_path,
        video_library_path=repository.video_library_path,
    )
    assert reopened.get_default_versioned_voice("ws") == saved
    assert reopened.profile_records.get_versioned_voice_profile("ws", saved.id) == v2
    assert (
        reopened.profile_inputs.resolve_narration_inputs(
            workspace_id="ws",
            episode_id="task",
            voice_profile_id=saved.id,
            voice_version="v1",
        ).voice.version
        == "v1"
    )
    assert (
        reopened.resolve_voice_reference(saved, viewer_user_id="member").read_bytes()
        == wav_bytes()
    )
    with pytest.raises(KeyError):
        reopened.profile_records.get_versioned_voice_profile("other", saved.id)
    with pytest.raises(ValueError):
        reopened.set_default_versioned_voice(
            "ws", saved.id, "", viewer_user_id="member"
        )


def test_immutable_version_and_owner_cannot_be_replaced(repository):
    saved = save_voice(repository)
    with pytest.raises(ValueError, match="不可|版本"):
        repository.save_versioned_voice_profile(
            replace(saved, display_name="覆盖"), verified_owner_user_id="member"
        )
    with pytest.raises(PermissionError):
        repository.save_versioned_voice_profile(
            replace(saved, version="v2"), verified_owner_user_id="intruder"
        )
    assert repository.profile_records.get_versioned_voice_profile("ws", "voice", "v1") == saved


def test_person_reference_and_optional_person_pair(repository):
    asset = image_asset(repository)
    saved_person = repository.save_person_profile(
        person(repository, asset), verified_owner_user_id="member"
    )
    independent = save_voice(repository)
    assert (
        repository.resolve_profile_reference(saved_person, viewer_user_id="member")[
            0
        ].name
        == "portrait.png"
    )
    linked = repository.save_versioned_voice_profile(
        replace(
            independent,
            version="v2",
            person_profile_id=saved_person.id,
            person_version=saved_person.version,
        ),
        verified_owner_user_id="member",
    )
    assert linked.person_version == "v1"
    with pytest.raises(ValueError, match="同时"):
        repository.save_versioned_voice_profile(
            replace(independent, version="v3", person_profile_id="person"),
            verified_owner_user_id="member",
        )
    with pytest.raises(KeyError):
        repository.save_versioned_voice_profile(
            replace(
                independent,
                version="v3",
                person_profile_id="foreign",
                person_version="v1",
            ),
            verified_owner_user_id="member",
        )


@pytest.mark.parametrize(
    "reference",
    [
        ProfileReference(start_seconds=-1, end_seconds=1),
        ProfileReference(start_seconds=0),
        ProfileReference(start_seconds=0, end_seconds=3),
        ProfileReference(segment_id="unknown"),
        ProfileReference(start_seconds=float("nan"), end_seconds=1),
    ],
)
def test_invalid_reference_range_is_not_registered(repository, reference):
    with pytest.raises(ValueError):
        repository.save_versioned_voice_profile(
            voice(repository, reference=reference), verified_owner_user_id="member"
        )
    assert repository.profile_records.list_versioned_voice_profiles("ws") == []


def test_person_segment_range_persists_without_a_model_and_never_uses_whole_file(
    repository,
):
    root = repository.video_library_path
    root.mkdir(parents=True)
    path = root / "synthetic.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=16x16:r=2",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=10,
        capture_output=True,
    )
    asset = repository._save_asset(
        MaterialAsset(
            id="video",
            workspace_id="ws",
            file_name=path.name,
            path=str(path),
            size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            duration_seconds=2,
            owner_user_id="member",
            segments=[
                MaterialSegment(
                    id="segment", asset_id="video", start_seconds=0, end_seconds=2
                )
            ],
        )
    )
    profile = replace(
        person(repository, asset),
        reference=ProfileReference(
            asset_id="video", segment_id="segment", start_seconds=0.25, end_seconds=1.5
        ),
    )
    saved = repository.save_person_profile(profile, verified_owner_user_id="member")
    assert repository.profile_records.get_person_profile("ws", "person", "v1") == saved
    assert repository.resolve_profile_reference(
        saved, viewer_user_id="member", for_generation=False
    )[1:] == (0.25, 1.5)
    with pytest.raises(ValueError, match="精确派生"):
        repository.resolve_profile_reference(saved, viewer_user_id="member")
    with pytest.raises(ValueError, match="超出"):
        repository.save_person_profile(
            replace(
                saved, version="v2", reference=replace(saved.reference, end_seconds=3)
            ),
            verified_owner_user_id="member",
        )


@pytest.mark.parametrize("state", ["reference_only", "excluded", "not_for_automation"])
def test_profile_and_source_restrictions_are_separate_and_reread(repository, state):
    saved = save_voice(repository)
    asset = repository._asset_by_path(
        "ws",
        str(
            repository.resolve_workspace_media_reference(
                "ws", saved.reference_audio_path
            )
        ),
    )
    asset.use_state = state
    repository._save_asset(asset)
    assert (
        repository.profile_records.get_versioned_voice_profile("ws", "voice").use_state == "available"
    )
    with pytest.raises(ValueError, match="来源"):
        repository.resolve_voice_reference(saved, viewer_user_id="member")
    asset.use_state = "available"
    repository._save_asset(asset)
    repository.save_versioned_voice_profile(
        replace(saved, version="v2", use_state=state), verified_owner_user_id="member"
    )
    with pytest.raises(ValueError, match=state):
        repository.resolve_voice_reference(saved, viewer_user_id="member")


def test_shared_source_revocation_foreign_owner_and_lost_file(repository):
    saved = save_voice(repository, visibility="shared")
    path = repository.resolve_workspace_media_reference(
        "ws", saved.reference_audio_path
    )
    asset = repository._asset_by_path("ws", str(path))
    # Shared profile never widens the underlying private source.
    with pytest.raises(PermissionError):
        repository.resolve_voice_reference(saved, viewer_user_id="other")
    asset.visibility = "shared"
    repository._save_asset(asset)
    repository.set_asset_visibility("ws", asset.id, "shared")
    assert repository.resolve_voice_reference(saved, viewer_user_id="other") == path
    repository.set_asset_visibility("ws", asset.id, "private")
    with pytest.raises(PermissionError):
        repository.resolve_voice_reference(saved, viewer_user_id="other")
    path.unlink()
    assert repository.profile_records.get_versioned_voice_profile("ws", "voice") == saved
    with pytest.raises(ValueError, match="失联"):
        repository.resolve_voice_reference(saved, viewer_user_id="member")


def test_source_identity_escape_and_invalid_wav_stop_registration(repository):
    reference, _, _ = repository.import_voice_reference_wav(
        "ws", wav_bytes(), verified_owner_user_id="member"
    )
    with pytest.raises(PermissionError):
        repository.save_versioned_voice_profile(
            replace(voice(repository), reference_audio_path=reference),
            verified_owner_user_id="other",
        )
    with pytest.raises(ValueError):
        repository.save_versioned_voice_profile(
            voice(repository, reference_audio_path="workspace://../outside.wav"),
            verified_owner_user_id="member",
        )
    with pytest.raises(ValueError):
        repository.import_voice_reference_wav(
            "ws", b"not wav", verified_owner_user_id="member"
        )
    wrong_asset = image_asset(repository)
    with pytest.raises(ValueError, match="不一致"):
        repository.save_versioned_voice_profile(
            voice(repository, reference=ProfileReference(asset_id=wrong_asset.id)),
            verified_owner_user_id="member",
        )


def test_expired_or_revoked_authorization_never_upgrades_historical_reference(
    repository,
):
    saved = save_voice(repository)
    auth = Authorization(
        id="restriction",
        version="v1",
        rights_holder="holder",
        content_types=("voice",),
        usage_scopes=("narration",),
        effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
        evidence_reference="test://proof",
        status="revoked",
        workspace_id="ws",
    )
    restricted = repository.save_versioned_voice_profile(
        replace(saved, version="v2", authorization=auth),
        verified_owner_user_id="member",
    )
    repository.save_authorization(replace(auth, version="v2", status="active"))
    resolved = repository.profile_inputs.resolve_narration_inputs(
        workspace_id="ws",
        episode_id="task",
        voice_profile_id="voice",
        voice_version="v2",
    )
    assert resolved.voice.authorization.status == "revoked"
    with pytest.raises(ValueError, match="revoked"):
        repository.resolve_voice_reference(restricted, viewer_user_id="member")
    expired = replace(
        restricted,
        authorization=replace(
            auth,
            status="active",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        ),
    )
    assert (
        profile_use_failure(expired, content_type="voice", usage_scope="narration")
        == "authorization_expired"
    )
    pending = replace(
        saved,
        authorization=replace(
            auth, status="pending", rights_holder="", evidence_reference=""
        ),
    )
    assert (
        profile_use_failure(pending, content_type="voice", usage_scope="narration")
        is None
    )


def test_legacy_payload_is_read_without_write_or_assumed_private_owner(repository):
    save_voice(repository)
    with repository._connect() as conn:
        raw = json.loads(
            conn.execute("SELECT payload FROM versioned_voice_profiles").fetchone()[
                "payload"
            ]
        )
        for key in (
            "reference",
            "use_state",
            "owner_user_id",
            "visibility",
            "display_name",
            "model_id",
            "person_profile_id",
            "person_version",
        ):
            raw.pop(key)
        before = json.dumps(raw, ensure_ascii=False)
        conn.execute("UPDATE versioned_voice_profiles SET payload=?", (before,))
    old = repository.profile_records.get_versioned_voice_profile("ws", "voice")
    assert old.reference is None and old.owner_user_id is None
    assert old.model_id == "" and old.person_profile_id == ""
    with pytest.raises(PermissionError):
        repository.resolve_voice_reference(old, viewer_user_id="member")
    with repository._connect() as conn:
        assert (
            conn.execute("SELECT payload FROM versioned_voice_profiles").fetchone()[
                "payload"
            ]
            == before
        )


def test_zero_percent_presenter_never_queries_person_or_model():
    class Trap:
        def __getattr__(self, name):
            raise AssertionError(name)

    assert (
        presenter_inputs(
            Trap(), Trap(), Trap(), SimpleNamespace(presenter_share_target=0), Trap()
        )
        == []
    )


class Provider:
    model_id = "controlled-test-provider"
    ready = True
    supported_languages = ("zh",)
    supports_pronunciation_dictionary = True
    message = "test"
    calls = 0
    fail = False

    def generate(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise VoiceCloneError("controlled provider failure")
        output = kwargs["output_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(wav_bytes())
        return VoiceCloneResult(str(output), 2, 8000)


def test_independent_narration_provider_failure_and_cached_preview_rechecks_source(
    repository, tmp_path
):
    provider = Provider()
    saved = save_voice(repository, model_id=provider.model_id)
    context = range_context(tmp_path)
    context = replace(
        context,
        grant=replace(context.grant, owner_type="media_preview", unit_key="voice:test"),
    )
    service = VoicePreviewService(tmp_path / "output", provider)
    args = dict(
        repository=repository,
        workspace_id="ws",
        voice_profile_id=saved.id,
        preview_text="合成文字",
        viewer_user_id="member",
        grant_context=context,
    )
    first = service.request(**args)
    assert first.status == "succeeded" and provider.calls == 1
    assert service.request(**args).id == first.id and provider.calls == 1
    asset = repository._asset_by_path(
        "ws",
        str(
            repository.resolve_workspace_media_reference(
                "ws", saved.reference_audio_path
            )
        ),
    )
    asset.use_state = "excluded"
    repository._save_asset(asset)
    denied = service.request(**args)
    assert (
        denied.status == "blocked"
        and denied.output_path is None
        and provider.calls == 1
    )
    asset.use_state = "available"
    repository._save_asset(asset)
    provider.fail = True
    failed = service.request(**{**args, "preview_text": "另一条合成文字"})
    assert (
        failed.status == "failed" and failed.output_path is None and provider.calls == 2
    )


def test_unconfigured_model_does_not_block_saved_reference_or_fake_preview(
    repository, tmp_path
):
    saved = save_voice(repository)
    provider = Provider()
    provider.ready = False
    result = VoicePreviewService(tmp_path / "output", provider).request(
        repository=repository,
        workspace_id="ws",
        voice_profile_id=saved.id,
        preview_text="合成文字",
        viewer_user_id="member",
    )
    assert repository.profile_records.get_versioned_voice_profile("ws", "voice") == saved
    assert result.status == "unavailable" and result.reason == "capability_unavailable"
    assert result.output_path is None and provider.calls == 0


def test_editing_segment_text_preserves_recognition_and_brand_is_not_global_gate(
    repository,
):
    asset = image_asset(repository)
    segment = MaterialSegment(
        id="seg",
        asset_id=asset.id,
        start_seconds=0,
        end_seconds=1,
        description="人工说明",
        transcript="旧字幕",
        system_tags=["识别事实"],
        user_tags=["我的标签"],
        usage_count=7,
    )
    asset.segments = [segment]
    repository._save_asset(asset)
    repository.update_segment(
        "ws",
        asset.id,
        segment.id,
        description="",
        transcript="新字幕",
        review_status="raw",
        user_tags=["新标签"],
    )
    actual = repository.get_asset("ws", asset.id).segments[0]
    assert (
        actual.description == ""
        and actual.transcript == "新字幕"
        and actual.user_tags == ["新标签"]
    )
    assert actual.system_tags == ["识别事实"] and actual.usage_count == 7
    assert (
        actual.start_seconds == segment.start_seconds
        and actual.end_seconds == segment.end_seconds
    )
    assert "brand_profile_missing" not in {
        item["code"] for item in repository.foundation_readiness.readiness("ws")["blockers"]
    }


def test_independent_voice_is_consumed_by_task_narration_without_person(
    repository, tmp_path
):
    saved = save_voice(repository)
    resolved = repository.resolve_voice_reference(saved, viewer_user_id="member")
    provider = Provider()
    result = NarrationGenerator(
        tmp_path / "output", provider
    ).generate_for_creation_task(
        workspace_id="ws",
        task_id="task",
        script_id="script",
        narration_text="测试",
        max_duration_seconds=10,
        voice_identity=saved,
        pronunciation_entries=(),
        resolved_reference_audio_path=resolved,
    )
    assert result.voice_profile_id == saved.id and result.task_id == "task"
    assert (
        result.script_id == "script"
        and result.duration_seconds == 2
        and provider.calls == 1
    )
    assert not saved.person_profile_id and saved.authorization is None


@pytest.mark.parametrize(
    "auth_link",
    [
        dict(authorization_id="lost", authorization_version="v1"),
        dict(authorization_id="lost", authorization_version=None),
    ],
)
def test_unmapped_historical_authorization_remains_readable_but_not_runnable(
    repository, auth_link
):
    saved = save_voice(repository)
    with repository._connect() as conn:
        payload = json.loads(
            conn.execute("SELECT payload FROM versioned_voice_profiles").fetchone()[
                "payload"
            ]
        )
        payload.update(auth_link)
        before = json.dumps(payload)
        conn.execute("UPDATE versioned_voice_profiles SET payload=?", (before,))
    read = repository.profile_records.get_versioned_voice_profile("ws", "voice")
    assert (
        read.id == saved.id and read.reference_audio_path == saved.reference_audio_path
    )
    assert read.reference_issue.startswith("historical_authorization_reference_")
    with pytest.raises(ValueError, match="重新定位"):
        repository.resolve_voice_reference(read, viewer_user_id="member")
    with repository._connect() as conn:
        assert (
            conn.execute("SELECT payload FROM versioned_voice_profiles").fetchone()[
                "payload"
            ]
            == before
        )


def test_reference_replaced_at_same_path_is_not_silently_the_same_version(repository):
    import os

    saved = save_voice(repository)
    path = repository.resolve_workspace_media_reference(
        "ws", saved.reference_audio_path
    )
    stamp = path.stat().st_mtime
    path.write_bytes(wav_bytes())
    os.utime(path, (stamp + 10, stamp + 10))
    with pytest.raises(ValueError, match="已变化"):
        repository.resolve_voice_reference(saved, viewer_user_id="member")


def test_voice_reference_can_be_registered_before_transcript_but_not_generated(
    repository, tmp_path
):
    saved = save_voice(repository, reference_text="")
    assert repository.profile_records.get_versioned_voice_profile("ws", "voice") == saved
    provider = Provider()
    result = VoicePreviewService(tmp_path / "output", provider).request(
        repository=repository,
        workspace_id="ws",
        voice_profile_id="voice",
        preview_text="合成文字",
        viewer_user_id="member",
    )
    assert result.status == "blocked" and result.reason == "reference_text_missing"
    assert result.output_path is None and provider.calls == 0


def test_new_registration_cannot_use_legacy_proof_shape_to_skip_reference_validation(
    repository,
):
    value = voice(repository)
    with pytest.raises(ValueError, match="来源引用"):
        repository.save_versioned_voice_profile(
            replace(value, reference=None), verified_owner_user_id="member"
        )
    assert repository.profile_records.list_versioned_voice_profiles("ws") == []


def test_readiness_does_not_count_a_lost_reference_as_usable(repository):
    saved = save_voice(repository)
    repository.resolve_workspace_media_reference(
        "ws", saved.reference_audio_path
    ).unlink()
    blockers = repository.foundation_readiness.readiness("ws", viewer_user_id="member")["blockers"]
    assert any(
        item["code"] == "voice_reference_unavailable" and "失联" in item["message"]
        for item in blockers
    )


# RANGE slice: synthetic inputs and the real POSIX registration/exec gate.
# The scheduler permission facts are controlled fixtures, not a user Grant.


class RangeScheduler:
    def __init__(self, *, evidence_path, reject=False, cancel=None):
        self.processes = HostProcessProbe()
        self.children = []
        self.active = True
        self.reject = reject
        self.cancel = cancel
        self.evidence_path = evidence_path

        class ControlledResourceFacts(HostResourceProbe):
            def __init__(self):
                pass

            def scene_runtime_limits(self, grant):
                return SceneRuntimeLimits(128 * 1024 * 1024, 16, 1, 0, 0.1)

        self.resources = ControlledResourceFacts()

    def assert_grant(self, grant):
        if not self.active:
            raise PermissionError("test grant revoked")

    def register_child(self, grant, child):
        records = (
            json.loads(self.evidence_path.read_text())
            if self.evidence_path.exists()
            else []
        )
        records.append({**asdict(child), "pgid": os.getpgid(child.pid)})
        self.evidence_path.write_text(json.dumps(records))
        if self.reject:
            raise PermissionError("test child registration denied")
        self.children.append(child)
        if self.cancel:
            self.cancel.set()


@pytest.fixture(autouse=True)
def controlled_range_resource_samples(request, monkeypatch, tmp_path):
    if request.node.name.startswith("test_range_"):
        # Host pressure is not sampled/benchmarked by this Dispatch. The real
        # child identity/registration, Grant checks and process-group exit stay.
        monkeypatch.setattr(
            "app.granted_subprocess._check_scene_runtime", lambda *_args: None
        )
    yield
    if request.node.name.startswith("test_range_"):
        evidence = tmp_path / "registered-children.json"
        if evidence.exists():
            records = json.loads(evidence.read_text())
            released = HostProcessProbe().children_released(
                tuple(
                    ProcessIdentity(item["pid"], item["start_token"], item["boot_id"])
                    for item in records
                )
            )
            evidence.with_name("released-children.json").write_text(
                json.dumps({"children": records, "all_released": released})
            )
            assert released is True


def range_context(
    tmp_path,
    *,
    stage="narration",
    reject=False,
    cancel=None,
    max_output_bytes=2 * 1024 * 1024,
):
    scheduler = RangeScheduler(
        evidence_path=tmp_path / "registered-children.json",
        reject=reject,
        cancel=cancel,
    )
    grant = Grant(
        "range-test",
        "ws",
        "creation_job",
        "test-job",
        stage + ":reference-test",
        "a" * 64,
        1,
        1,
        "test-only",
        tmp_path / "db.sqlite3",
    )
    return CreationStageGrantContext(
        scheduler, grant, time.monotonic() + 15, max_output_bytes, 1, cancel
    )


def range_voice(repository, *, start=0.5, end=2.5):
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(
            b"".join(struct.pack("<h", frame // 32 - 1000) for frame in range(64000))
        )
    ref, _, _ = repository.import_voice_reference_wav(
        "ws", target.getvalue(), verified_owner_user_id="member"
    )
    return repository.save_versioned_voice_profile(
        VoiceProfile(
            id="ranged-voice",
            workspace_id="ws",
            display_name="合成范围声音",
            version="v1",
            language="zh",
            reference_audio_path=ref,
            reference_text="仅测试输入",
            reference=ProfileReference(start_seconds=start, end_seconds=end),
        ),
        verified_owner_user_id="member",
    )


def range_plan(*, selected_person=None, selected_voice=None):
    return CreationPlan(
        id="plan",
        workspace_id="ws",
        goal="test",
        name="test",
        target_count=1,
        language="zh",
        aspect_ratio="16:9",
        duration_target_seconds=10,
        knowledge_references=[],
        source_references=[],
        selected_voice=selected_voice,
        selected_person=selected_person,
        pronunciation_references=[],
        approved_asset_count=0,
        approved_clip_count=0,
        material_rule_summary="",
        model_snapshot=[],
        confirmation_points=[],
        blockers=[],
        preflight_fingerprint="test",
    )


def range_plan_voice():
    return CreationPlanVoiceReference(
        "voice", "v1", "legacy-person", "v3", "", "", "", "", "zh", ""
    )


def test_range_selected_person_persists_independent_fixed_version_and_legacy_read_projection(
    repository,
):
    from dataclasses import asdict

    explicit = CreationPlanPersonReference("explicit-person", "v7")
    plan = range_plan(selected_person=explicit, selected_voice=range_plan_voice())
    task = CreationTask(
        id="task", workspace_id="ws", plan_id=plan.id, name="test", scenario="test"
    )
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    assert repository.plan_records.get_creation_plan("ws", "plan").selected_person == explicit
    payload = json.loads(json.dumps(asdict(plan), default=str))
    assert (
        repository.plan_records.creation_plan_from_payload(dict(payload)).selected_person
        == explicit
    )
    payload.pop("selected_person")
    legacy = repository.plan_records.creation_plan_from_payload(dict(payload))
    assert legacy.selected_person == CreationPlanPersonReference("legacy-person", "v3")
    payload["selected_person"] = None
    assert repository.plan_records.creation_plan_from_payload(dict(payload)).selected_person is None
    payload.pop("selected_person")
    payload["selected_voice"]["person_version"] = ""
    assert repository.plan_records.creation_plan_from_payload(dict(payload)).selected_person is None
    assert repository.plan_records.get_creation_plan("ws", "plan").selected_person == explicit


@pytest.mark.parametrize("values", [("", "v1"), ("person", "")])
def test_range_person_selection_never_resolves_latest(values):
    with pytest.raises(ValueError, match="固定"):
        CreationPlanPersonReference(*values)


def test_range_selected_person_qualification_pins_original_version_without_a_model(
    repository,
):
    asset = image_asset(repository)
    first = repository.save_person_profile(
        person(repository, asset), verified_owner_user_id="member"
    )
    repository.save_person_profile(
        replace(first, version="v2"), verified_owner_user_id="member"
    )
    assert (
        repository.creation_plan_person_reference(
            "ws", first.id, "v1", viewer_user_id="member"
        ).version
        == "v1"
    )
    with pytest.raises(PermissionError):
        repository.creation_plan_person_reference(
            "ws", first.id, "v1", viewer_user_id="other"
        )
    with pytest.raises(KeyError):
        repository.creation_plan_person_reference(
            "other", first.id, "v1", viewer_user_id="member"
        )


def test_range_presenter_consumes_explicit_person_not_voice_relationship():
    observed = []
    selected = CreationPlanPersonReference("explicit-person", "v7")
    plan = range_plan(selected_person=selected, selected_voice=range_plan_voice())
    plan.presenter_share_target = 100
    plan.lipsync_quality = "fast"
    segment = SimpleNamespace(
        id="audio-1",
        source_section_id="s1",
        status="ready",
        duration_seconds=2,
        voice_profile_id="voice",
        voice_version="v1",
        audio_reference="artifact.wav",
        language="zh",
    )

    class Repo:
        def list_narration_segments(self, *args):
            return [segment]

        def get_person_profile(self, ws, id, version):
            observed.append((ws, id, version))
            return SimpleNamespace(id=id, version=version, authorization=None)

        narration_artifacts = SimpleNamespace(
            resolve_authorized_narration_audio_reference=lambda *args: Path("artifact.wav"))

        class ProfileInputs:
            def resolve_avatar_lipsync_request(self, **kwargs):
                assert (
                    kwargs["person_id"] == "explicit-person"
                    and kwargs["person_version"] == "v7"
                )
                assert kwargs["viewer_user_id"] == "member"
                return "controlled-request"

        profile_inputs = ProfileInputs()

    # The authorization boundary is tested by real profile tests; this case
    # isolates the plan's version forwarding, not a media/model success.
    from unittest.mock import patch

    task = SimpleNamespace(workspace_id="ws", id="task", owner_user_id="member")
    script = SimpleNamespace(id="script", sections=[SimpleNamespace(id="s1")])
    with patch("app.creation_execution._authorization_failure", return_value=None):
        assert (
            presenter_inputs(Repo(), task, script, plan, {"fast": "test-model"})[0][0]
            == segment
        )
        assert observed == [("ws", "explicit-person", "v7")]
        plan.selected_person = None
        with pytest.raises(ValueError, match="单独选择"):
            presenter_inputs(Repo(), task, script, plan, {"fast": "test-model"})


def test_range_wav_derivation_exact_samples_source_immutable_and_cache_revalidated(
    repository, tmp_path
):
    value = range_voice(repository)
    source = repository.resolve_workspace_media_reference(
        "ws", value.reference_audio_path
    )
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    qualified = repository.profile_reference_input(value, viewer_user_id="member")
    assert qualified.needs_derivation and qualified.asset_id
    with pytest.raises(PermissionError, match="许可"):
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=None
        )
    context = range_context(tmp_path)
    result = repository.materialize_profile_reference(
        value, viewer_user_id="member", grant_context=context
    )
    assert result.derived and result.path != source and result.duration_seconds == 2
    with wave.open(str(source)) as original, wave.open(str(result.path)) as actual:
        original.setpos(8000)
        assert actual.readframes(actual.getnframes()) == original.readframes(32000)
    children = len(context.scheduler.children)
    assert (
        children > 0
        and context.scheduler.processes.children_released(
            tuple(context.scheduler.children)
        )
        is True
    )
    assert (
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
        == result
    )
    assert (
        len(context.scheduler.children) == children
    )  # WAV cache is checked without another FFmpeg
    manifest = json.loads(result.path.with_suffix(".json").read_text())
    assert manifest["source"]["asset_id"] == value.reference.asset_id
    assert (
        manifest["source"]["start_seconds"] == 0.5
        and manifest["source"]["end_seconds"] == 2.5
    )
    assert manifest["source"]["mtime_ns"] == source.stat().st_mtime_ns
    assert manifest["sha256"] == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    result.path.with_suffix(".json").write_text("{broken")
    with pytest.raises(ValueError, match="缓存记录无效"):
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
    result.path.with_suffix(".json").write_text(json.dumps(manifest))
    result.path.write_bytes(b"corrupt cache")
    with pytest.raises(ValueError, match="缓存"):
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize(
    "mode",
    [
        "register_denied",
        "cancel_before",
        "cancel_register",
        "cancel_after",
        "revoked_after",
    ],
)
def test_range_permission_failure_cancel_never_publish_cache_and_release_registered_children(
    repository, tmp_path, monkeypatch, mode
):
    value = range_voice(repository)
    cancelled = threading.Event()
    context = range_context(
        tmp_path,
        reject=mode == "register_denied",
        cancel=cancelled if mode.startswith("cancel") else None,
    )
    if mode == "cancel_before":
        cancelled.set()
    original_run = CreationStageGrantContext.run
    if mode in {"cancel_after", "revoked_after"}:
        # Permit the real child; revoke only after it wrote scratch output.
        context.scheduler.cancel = None

        def run_then_revoke(current, command, **kwargs):
            result = original_run(current, command, **kwargs)
            if command[0] == "ffmpeg":
                if mode == "cancel_after":
                    cancelled.set()
                else:
                    context.scheduler.active = False
            return result

        monkeypatch.setattr(CreationStageGrantContext, "run", run_then_revoke)
    with pytest.raises((PermissionError, GrantedCommandCancelled)) as denied:
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
    if mode in {"register_denied", "cancel_register"}:
        assert denied.value.command_diagnostics["exec_ack"] is False
        assert denied.value.command_diagnostics["group_released"] is True
    assert not list(
        (repository.video_library_path / ".profile_references").glob("**/*.json")
    )
    assert not list(
        (repository.video_library_path / ".profile_references").glob("**/*.wav")
    )
    assert (
        context.scheduler.processes.children_released(tuple(context.scheduler.children))
        is True
    )
    assert (
        repository.profile_records.get_versioned_voice_profile("ws", value.id, value.version) == value
    )


def test_range_source_restriction_changed_during_execution_is_not_published(
    repository, tmp_path, monkeypatch
):
    value = range_voice(repository)
    context = range_context(tmp_path)
    original_run = CreationStageGrantContext.run

    def restrict_after_run(current, command, **kwargs):
        result = original_run(current, command, **kwargs)
        asset = repository.get_asset("ws", value.reference.asset_id)
        asset.use_state = "reference_only"
        repository._save_asset(asset)
        return result

    monkeypatch.setattr(CreationStageGrantContext, "run", restrict_after_run)
    with pytest.raises(ValueError, match="来源"):
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
    assert not list(
        (repository.video_library_path / ".profile_references").glob("**/*.json")
    )
    assert (
        context.scheduler.processes.children_released(tuple(context.scheduler.children))
        is True
    )


def test_range_failed_real_ffmpeg_keeps_profile_without_success_output(
    repository, tmp_path, monkeypatch
):
    import app.profile_reference_media as media

    value = range_voice(repository)
    original_command = media._command

    def fail_command(source, target, *, threads):
        command = original_command(source, target, threads=threads)
        command[command.index("-c:a") + 1] = "definitely_not_a_codec"
        return command

    monkeypatch.setattr(media, "_command", fail_command)
    context = range_context(tmp_path)
    with pytest.raises(ValueError, match="派生失败"):
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
    assert not list(
        (repository.video_library_path / ".profile_references").glob("**/*.json")
    )
    assert not list(
        (repository.video_library_path / ".profile_references").glob("**/*.wav")
    )
    assert (
        context.scheduler.processes.children_released(tuple(context.scheduler.children))
        is True
    )
    assert (
        repository.profile_records.get_versioned_voice_profile("ws", value.id, value.version) == value
    )


def test_range_video_audio_and_person_use_distinct_exact_derived_media(
    repository, tmp_path
):
    root = repository.video_library_path
    root.mkdir()
    source = root / "synthetic-source.mp4"
    context = range_context(tmp_path)
    # This is owned synthetic input construction, never a model output.
    built = context.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=16x16:r=24,drawbox=color=white:t=fill:enable='gte(t,2)'",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=400:sample_rate=16000",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        output_paths=(source,),
    )
    assert built.returncode == 0
    asset = repository._save_asset(
        MaterialAsset(
            id="ranged-video",
            workspace_id="ws",
            file_name=source.name,
            path=str(source),
            size_bytes=source.stat().st_size,
            modified_at=datetime.fromtimestamp(source.stat().st_mtime),
            duration_seconds=4,
            owner_user_id="member",
            segments=[
                MaterialSegment(
                    id="range-segment",
                    asset_id="ranged-video",
                    start_seconds=0.25,
                    end_seconds=3,
                )
            ],
        )
    )
    reference = ProfileReference(
        asset_id=asset.id,
        segment_id="range-segment",
        start_seconds=0.5,
        end_seconds=2.5,
    )
    visual = repository.save_person_profile(
        replace(person(repository, asset), reference=reference),
        verified_owner_user_id="member",
    )
    audio = repository.save_versioned_voice_profile(
        VoiceProfile(
            id="video-voice",
            workspace_id="ws",
            version="v1",
            display_name="合成视频声音",
            language="zh",
            reference_audio_path=visual.avatar_reference,
            reference_text="",
            reference=reference,
        ),
        verified_owner_user_id="member",
    )
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    children = len(context.scheduler.children)
    preview = VoicePreviewService(tmp_path / "output", Provider()).request(
        repository=repository,
        workspace_id="ws",
        voice_profile_id=audio.id,
        preview_text="合成文字",
        viewer_user_id="member",
        grant_context=context,
    )
    assert preview.status == "blocked" and preview.reason == "reference_text_missing"
    assert (
        len(context.scheduler.children) == children
    )  # no extraction or generation without needed transcript
    voice_input = repository.materialize_profile_reference(
        audio, viewer_user_id="member", grant_context=context
    )
    person_context = range_context(tmp_path, stage="person_render")
    person_input = repository.materialize_profile_reference(
        visual, viewer_user_id="member", grant_context=person_context
    )
    assert voice_input.path.suffix == ".wav" and person_input.path.suffix == ".mp4"
    assert (
        voice_input.path != person_input.path
        and voice_input.source.segment_id == "range-segment"
    )
    assert (
        voice_input.duration_seconds == 2
        and abs(person_input.duration_seconds - 2) <= 1 / 24
    )
    pixels = context.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            str(person_input.path),
            "-vf",
            r"select=eq(n\,0)+eq(n\,47)",
            "-vsync",
            "0",
            "-threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )
    assert pixels.returncode == 0 and len(pixels.stdout) == 16 * 16 * 3 * 2
    assert (
        pixels.stdout[:768] == bytes(768) and pixels.stdout[768:] == bytes([255]) * 768
    )
    from app.avatar_lipsync import AvatarAndLipSyncResult

    class AvatarRecorder:
        ready = True
        model_id = "range-recorder"
        supported_languages = ("zh",)
        calls = []

        def render(self, *, request, output_path, grant_context):
            self.calls.append(request)
            # Deliberately no generated media; this verifies the real adapter
            # handed the exact derived input to a controlled provider boundary.
            return AvatarAndLipSyncResult(
                "failed",
                "controlled",
                None,
                None,
                None,
                "controlled-no-model",
                "test only",
                "test only",
            )

    recorder = AvatarRecorder()
    avatar = AvatarAndLipSyncService(tmp_path / "output", recorder)
    request_args = dict(
        workspace_id="ws",
        person_id=visual.id,
        person_version=visual.version,
        voice_profile_id=audio.id,
        voice_version=audio.version,
        audio_path=str(voice_input.path),
        audio_cache_key="synthetic-input",
        language="zh",
        motion="natural",
        expression="neutral",
        authorization_id="",
        authorization_version="",
        model_id=recorder.model_id,
        viewer_user_id="member",
    )
    request = repository.profile_inputs.resolve_avatar_lipsync_request(**request_args)
    with pytest.raises(PermissionError, match="精确派生"):
        avatar.render(request, grant_context=context)
    assert not recorder.calls
    result = avatar.render_from_repository(
        repository=repository, grant_context=person_context, **request_args
    )
    assert result.status == "failed" and len(recorder.calls) == 1
    assert Path(recorder.calls[0].visual_path) == person_input.path != source
    assert avatar_lipsync_cache_key(request) == result.cache_key
    assert (
        repository.profile_records.get_versioned_voice_profile("ws", audio.id, "v1").reference_text
        == ""
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    assert (
        context.scheduler.processes.children_released(tuple(context.scheduler.children))
        is True
    )
    assert (
        person_context.scheduler.processes.children_released(
            tuple(person_context.scheduler.children)
        )
        is True
    )
    asset.duration_seconds = (
        3  # coherent bounds, but a false registered source duration
    )
    repository._save_asset(asset)
    with pytest.raises(ValueError, match="登记时长与真实媒体"):
        repository.materialize_profile_reference(
            audio, viewer_user_id="member", grant_context=context
        )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_range_task_generation_does_not_send_whole_wav_without_repository(
    repository, tmp_path
):
    value = range_voice(repository)
    provider = Provider()
    generator = NarrationGenerator(tmp_path / "output", provider)
    with pytest.raises(PermissionError, match="精确派生"):
        generator.generate_for_creation_task(
            workspace_id="ws",
            task_id="task",
            script_id="script",
            narration_text="短句",
            max_duration_seconds=10,
            voice_identity=value,
            pronunciation_entries=(),
            resolved_reference_audio_path=repository.resolve_workspace_media_reference(
                "ws", value.reference_audio_path
            ),
        )
    assert provider.calls == 0


def test_range_task_generation_hands_only_exact_reference_to_granted_provider(
    repository, tmp_path
):
    from app.voice_clone import Qwen3TTSSubprocessProvider

    class ControlledQwen(Qwen3TTSSubprocessProvider):
        ready = True
        calls = []

        def __init__(self):
            pass

        def generate(self, **kwargs):
            kwargs["grant_context"].check()
            self.calls.append(kwargs)
            output = kwargs["output_path"]
            output.parent.mkdir(parents=True)
            output.write_bytes(Path(kwargs["reference_audio_path"]).read_bytes())
            return VoiceCloneResult(str(output), 2, 16000)

    value = range_voice(repository)
    provider = ControlledQwen()
    context = range_context(tmp_path)
    source = repository.resolve_workspace_media_reference(
        "ws", value.reference_audio_path
    )
    generated = NarrationGenerator(
        tmp_path / "output", provider
    ).generate_for_creation_task(
        repository=repository,
        viewer_user_id="member",
        grant_context=context,
        workspace_id="ws",
        task_id="task",
        script_id="script",
        narration_text="短句",
        max_duration_seconds=10,
        voice_identity=value,
        pronunciation_entries=(),
        resolved_reference_audio_path=source,
    )
    assert len(provider.calls) == 1
    input_path = Path(provider.calls[0]["reference_audio_path"])
    assert input_path != source
    with wave.open(str(input_path)) as actual:
        assert actual.getnframes() / actual.getframerate() == 2
    assert Path(generated.audio_path).read_bytes() == input_path.read_bytes()
    assert (
        context.scheduler.processes.children_released(tuple(context.scheduler.children))
        is True
    )


def test_range_repository_narration_consumer_passes_precise_artifact_and_source_identity(
    repository, tmp_path, monkeypatch
):
    value = range_voice(repository)
    generator = NarrationGenerator(tmp_path / "output", Provider())
    monkeypatch.setattr(generator, "generate", lambda **kwargs: kwargs)
    context = range_context(tmp_path)
    result = generator.generate_from_repository(
        repository=repository,
        workspace_id="ws",
        episode=SimpleNamespace(id="task"),
        profile=ProductionProfile(workspace_id="ws"),
        voice_profile_id=value.id,
        voice_version=value.version,
        viewer_user_id="member",
        grant_context=context,
    )
    source = repository.profile_reference_input(value, viewer_user_id="member")
    assert result["reference_input_identity"] == source.identity
    assert result["resolved_reference_audio_path"] != Path(source.source_path)
    with wave.open(str(result["resolved_reference_audio_path"])) as audio:
        assert audio.getnframes() / audio.getframerate() == 2
    assert (
        context.scheduler.processes.children_released(tuple(context.scheduler.children))
        is True
    )


def test_range_reference_rejects_other_stage_workspace_or_member_before_exec(
    repository, tmp_path
):
    value = range_voice(repository)
    context = range_context(tmp_path, stage="render")
    with pytest.raises(PermissionError, match="专属许可"):
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
    context = replace(
        context,
        grant=replace(context.grant, workspace_id="other", unit_key="narration:test"),
    )
    with pytest.raises(PermissionError, match="工作区"):
        repository.materialize_profile_reference(
            value, viewer_user_id="member", grant_context=context
        )
    context = range_context(tmp_path)
    with pytest.raises(PermissionError, match="成员"):
        repository.materialize_profile_reference(
            value, viewer_user_id="other", grant_context=context
        )
    assert not context.scheduler.children


@pytest.mark.parametrize("start,end", [(float("nan"), 2), (-1, 2), (2, 1), (1, None)])
def test_range_historical_invalid_bounds_are_not_executed(
    repository, tmp_path, start, end
):
    value = range_voice(repository)
    invalid = replace(
        value, reference=replace(value.reference, start_seconds=start, end_seconds=end)
    )
    context = range_context(tmp_path)
    with pytest.raises(ValueError):
        repository.materialize_profile_reference(
            invalid, viewer_user_id="member", grant_context=context
        )
    assert not context.scheduler.children


def test_profile_input_resolver_keeps_historical_facts_current_access_and_readonly_privacy(repository, monkeypatch):
    import socket
    from app.profiles.contracts import PronunciationEntry
    from app.profiles.input_resolution import VersionedNarrationInputs
    from app.materials.contracts import MediaAuthorityError

    def forbidden(*args, **kwargs):
        raise AssertionError('profile metadata forbids media, network and generation')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    now = datetime.now(timezone.utc)
    authorization = Authorization(id='original-consent', workspace_id='ws', version='v1', rights_holder='metadata',
        content_types=('voice', 'person'), usage_scopes=('narration', 'avatar_lipsync'),
        effective_from=now - timedelta(days=1), expires_at=None, evidence_reference='metadata', status='active')
    alternate = replace(authorization, id='different-current-consent')
    historical_revoked = replace(authorization, id='historical-consent', status='revoked')
    for value in (authorization, alternate, historical_revoked, replace(historical_revoked, version='v2', status='active')):
        repository._media_authorizations.save_authorization(value)
    old_voice = VoiceProfile('metadata-voice', 'ws', 'v1', 'zh', '', 'fixed old text',
        authorization=authorization, owner_user_id='member', visibility='private')
    current_voice = replace(old_voice, version='v2', reference_text='different current text',
        authorization=alternate, use_state='excluded', visibility='shared')
    old_person = PersonProfile('metadata-person', 'ws', 'fixed old person', 'v1', '', (), (),
        authorization=authorization, owner_user_id='member', visibility='private')
    current_person = replace(old_person, version='v2', display_name='different current person', visibility='shared')
    protected_voice = replace(old_voice, id='historically-blocked', use_state='excluded', authorization=historical_revoked)
    with repository._connect() as conn:
        for value in (old_voice, current_voice, protected_voice,
                      replace(protected_voice, version='v2', use_state='available', authorization=alternate)):
            repository.profile_records._save_versioned_voice_profile(conn, value)
        for value in (old_person, current_person):
            repository.profile_records._save_person_profile(conn, value)
        conn.execute('INSERT INTO episode_voice_selections VALUES(?,?,?,?,?)',
            ('ws', 'metadata-episode', old_voice.id, 'v1', now.isoformat()))
    entry = PronunciationEntry('metadata-pronunciation', 'ws', 'term', 'zh', 'fixed pronunciation', 'workspace', None, 'v1')
    repository.profile_records.save_pronunciation_entry(entry)
    inputs = repository.profile_inputs.resolve_narration_inputs(workspace_id='ws', episode_id='metadata-episode',
        voice_profile_id=old_voice.id, voice_version='v1')
    assert isinstance(inputs, VersionedNarrationInputs)
    assert inputs.voice.version == 'v1' and inputs.voice.reference_text == 'fixed old text'
    assert inputs.voice.use_state == 'excluded' and inputs.voice.visibility == 'shared'
    assert inputs.voice.authorization.id == authorization.id and inputs.pronunciation_entries == (entry,)
    protected = repository.profile_inputs.resolve_narration_inputs(workspace_id='ws', episode_id='metadata-episode',
        voice_profile_id=protected_voice.id, voice_version='v1')
    assert protected.voice.use_state == 'excluded' and protected.voice.authorization == historical_revoked
    assert repository.profile_inputs.selected_episode_versioned_voice_identity('ws', 'metadata-episode') == (old_voice.id, 'v1')
    with pytest.raises(MediaAuthorityError) as episode_refusal:
        repository.profile_inputs.resolve_episode_narration_inputs('ws', 'metadata-episode')
    assert episode_refusal.value.reason == 'voice_authorization_profile_excluded'
    reference_calls = []
    def reference_forbidden(profile, **kwargs):
        reference_calls.append(profile.id)
        return forbidden()
    monkeypatch.setattr(repository, 'profile_reference_input', reference_forbidden)
    command = dict(workspace_id='ws', person_id=old_person.id, person_version='v1',
        voice_profile_id=old_voice.id, voice_version='v1', audio_path='metadata-only.wav', audio_cache_key='metadata',
        language='zh', motion='none', expression='none', authorization_id=authorization.id,
        authorization_version='v1', model_id='metadata', viewer_user_id='other-member')
    readonly = repository.profile_inputs.resolve_avatar_lipsync_request(**command, for_generation=False)
    assert readonly.person.version == 'v1' and readonly.person.display_name == 'fixed old person'
    assert readonly.voice.version == 'v1' and readonly.visual_path == '' and readonly.visual_reference_input is None
    assert reference_calls == []
    with pytest.raises(AssertionError, match='profile metadata forbids'):
        repository.profile_inputs.resolve_avatar_lipsync_request(**command)
    assert reference_calls == [old_voice.id]
    with repository._connect() as conn:
        repository.profile_records._save_person_profile(conn, replace(current_person, version='v3', visibility='private'))
        repository.profile_records._save_versioned_voice_profile(conn, replace(current_voice, version='v3', visibility='private'))
    with pytest.raises(PermissionError, match='已取消共享'):
        repository.profile_inputs.resolve_avatar_lipsync_request(**command, for_generation=False)
    assert reference_calls == [old_voice.id]
    revoked = replace(authorization, version='v2', status='revoked')
    repository._media_authorizations.save_authorization(revoked)
    still_fixed = repository.profile_inputs.resolve_narration_inputs(workspace_id='ws', episode_id='metadata-episode',
        voice_profile_id=old_voice.id, voice_version='v1')
    assert still_fixed.voice.reference_text == 'fixed old text' and still_fixed.voice.authorization == revoked
    assert still_fixed.voice.authorization.id != alternate.id
