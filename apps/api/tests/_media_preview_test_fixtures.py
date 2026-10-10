"""Private fixtures for profile-preview tests; all media and identities are synthetic."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import shutil
import subprocess
import wave

from PIL import Image

from app.avatar_lipsync import Authorization, PersonProfile, ProfileReference, VoiceProfile
from app.creation_execution import CreationExecutionActor
from app.device_runtime import build_device_runtime
from app.device_scheduler import ProcessIdentity
from app.host_resource_probes import NarrationBudget, PresenterBudget
from app.production import MaterialAsset, MaterialSegment, ProductionRepository
from app.profile_preview_execution import profile_preview_registration, submit_profile_preview


def synthetic_wav_bytes(seconds: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x01\x00" * (8000 * seconds))
    return buffer.getvalue()


def write_synthetic_video(path: Path, *, seconds: int = 24) -> Path:
    """Create a small, decodable audio/video source without external media."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FileNotFoundError("ffmpeg is required to create the synthetic video fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s=64x64:r=1:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=24000:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "24k", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def register_synthetic_video_asset(
    repository: ProductionRepository, *, workspace_id: str, member_id: str,
    path: Path, asset_id: str, seconds: int = 24,
) -> tuple[MaterialAsset, str]:
    write_synthetic_video(path, seconds=seconds)
    asset = repository._save_asset(MaterialAsset(
        id=asset_id, workspace_id=workspace_id, file_name=path.name, path=str(path),
        size_bytes=path.stat().st_size,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
        duration_seconds=seconds, usage_role="b_roll", review_status="approved",
        analysis_status="ready", media_type="video", owner_user_id=member_id,
        segments=[MaterialSegment(
            id=f"{asset_id}-segment-{index}", asset_id=asset_id,
            start_seconds=float(index * seconds / 3), end_seconds=float((index + 1) * seconds / 3),
            description=f"合成片段 {index + 1}", review_status="approved",
        ) for index in range(3)],
    ))
    reference = repository.workspace_media_reference_for_asset(workspace_id, asset.id)
    with repository._connect() as conn:
        repository._claim_workspace_media_reference(
            conn, workspace_id=workspace_id, reference=reference
        )
    return asset, reference


def register_synthetic_profiles(
    repository: ProductionRepository, *, workspace_id: str, member_id: str,
    person_id: str = "person_a", voice_id: str = "voice_a",
    person_version: str = "person-v1", voice_version: str = "voice-v1",
    language: str = "zh-en", model_id: str = "fixture-voice-runtime-v1",
    person_authorization_id: str | None = None,
    voice_authorization_id: str | None = None,
    person_usage_scopes: tuple[str, ...] = ("avatar_lipsync",),
    voice_usage_scopes: tuple[str, ...] = ("narration", "avatar_lipsync"),
):
    voice_reference, _size, _duration = repository.import_voice_reference_wav(
        workspace_id, synthetic_wav_bytes(), verified_owner_user_id=member_id
    )
    voice_path = repository.resolve_workspace_media_reference(workspace_id, voice_reference)
    voice_asset = repository._asset_by_path(workspace_id, str(voice_path))

    image_path = repository.video_library_path / "approved" / f"{workspace_id}-{person_id}.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=(32, 96, 160)).save(image_path, format="PNG")
    stat = image_path.stat()
    image_asset = repository._save_asset(MaterialAsset(
        id=f"{person_id}-reference", workspace_id=workspace_id, file_name=image_path.name,
        path=str(image_path), size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime), media_type="image",
        owner_user_id=member_id,
    ))

    def authorization(suffix: str, content_type: str, scopes: tuple[str, ...]) -> Authorization:
        return Authorization(
            id=(person_authorization_id if suffix == "person-auth" and person_authorization_id else
                voice_authorization_id if suffix == "voice-auth" and voice_authorization_id else
                f"{person_id}-{suffix}"), version="v1", rights_holder="合成测试成员",
            content_types=(content_type,), usage_scopes=scopes,
            effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc), expires_at=None,
            evidence_reference=f"fixture://{person_id}/{suffix}", status="active",
            workspace_id=workspace_id,
        )

    person_authorization = authorization("person-auth", "person_likeness", person_usage_scopes)
    person_authorization = replace(
        person_authorization,
        content_types=("person_likeness", "avatar_video"),
    )
    voice_authorization = authorization("voice-auth", "voice", voice_usage_scopes)
    avatar_authorization = authorization("avatar-auth", "avatar_video", ("avatar_lipsync",))
    person = PersonProfile(
        id=person_id, workspace_id=workspace_id, display_name="合成主播", version=person_version,
        avatar_reference=repository.workspace_media_reference_for_asset(workspace_id, image_asset.id),
        background_capabilities=("studio",), shot_capabilities=("medium",),
        authorization=person_authorization, reference=ProfileReference(asset_id=image_asset.id),
    )
    voice = VoiceProfile(
        id=voice_id, workspace_id=workspace_id, person_profile_id=person.id,
        person_version=person.version, version=voice_version, language=language,
        display_name="合成声音",
        reference_audio_path=voice_reference, reference_text="合成测试参考文字",
        model_id=model_id, authorization=voice_authorization,
        reference=ProfileReference(asset_id=voice_asset.id),
    )
    person = repository.save_person_profile(person, verified_owner_user_id=member_id)
    voice = repository.save_versioned_voice_profile(voice, verified_owner_user_id=member_id)
    repository.save_authorization(avatar_authorization)
    return person, voice, avatar_authorization, voice_path, image_path


class ControlledResources:
    def __init__(self, admitted: bool | None = True):
        self.admitted = admitted

    def admit(self, _request):
        return self.admitted

    def released(self, _grant):
        return True

    def continue_unit(self, _grant):
        return True


class ControlledProcesses:
    def current(self):
        return ProcessIdentity(os.getpid(), "synthetic-test-process", "synthetic-test-boot")

    def children_released(self, _children):
        return True

    def recovery_state(self, _holder, _children):
        return "released"


class ControlledDeviceAccess:
    def can_view_device_summary(self, _actor):
        return True


@dataclass
class PreviewRuntimeHarness:
    repository: ProductionRepository
    runtime: object
    resources: ControlledResources
    actor: CreationExecutionActor
    member_id: str

    def prepare(self, service, params: dict):
        prepared = service.request(
            repository=self.repository,
            workspace_id=self.actor.workspace_id,
            viewer_user_id=self.member_id,
            prepare_only=True,
            member_identity=self.actor.identity_payload(),
            **params,
        )
        if prepared.status == "queued" and not prepared.device_request_id:
            prepared = submit_profile_preview(self.runtime, prepared, self.actor, params)
        return prepared

    def complete_once(self) -> bool:
        return self.runtime.run_once()

    def close(self) -> bool:
        return self.runtime.stop(1)


def build_preview_runtime(
    repository: ProductionRepository, *, output_root: Path, voice_service,
    avatar_service, member_id: str, workspace_id: str = "workspace_a",
    resources: ControlledResources | None = None,
) -> PreviewRuntimeHarness:
    resources = resources or ControlledResources()
    actor = CreationExecutionActor(workspace_id, member_id)
    narration_budget = NarrationBudget(100, 1, 10**6, 5, 10**6, 1, 10, 10**6, 2, 0.1, 10**6, 10**6)
    presenter_budget = PresenterBudget(1, 10**6, 5, 10**6, 1, 10, 10**6, 2, 0.1, 10**6, 10**6)
    registration = profile_preview_registration(
        repository, voice_service=voice_service, avatar_service=avatar_service,
        narration_budget=narration_budget, presenter_budget=presenter_budget,
        verify_identity=lambda candidate: "ready" if candidate == actor else "unauthorized",
    )
    runtime = build_device_runtime(
        database_path=repository.database_path,
        device_lock_path=repository.database_path.with_name("preview-device.lock"),
        device_access=ControlledDeviceAccess(), registrations={"media_preview": registration},
        resources=resources, processes=ControlledProcesses(), activity_source_reliable=True,
    )
    return PreviewRuntimeHarness(repository, runtime, resources, actor, member_id)
