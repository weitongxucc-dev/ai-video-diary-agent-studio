"""Fixed profile versions combined with current access and generation inputs."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from app.avatar_lipsync import AvatarAndLipSyncRequest
from app.creation.task_records import TaskRecordStore
from app.materials.contracts import MediaAuthorityError as _MediaAuthorityError
from app.profiles.contracts import VoiceProfile as VersionedVoiceProfile, PronunciationEntry, profile_use_failure


@dataclass(frozen=True)
class VersionedNarrationInputs:
    voice: VersionedVoiceProfile
    pronunciation_entries: tuple[PronunciationEntry, ...]


class ProfileInputResolver:
    def __init__(self, *, connection_factory, read_voice, read_person, current_authorization,
                 read_authorization, task_records: TaskRecordStore, list_pronunciation,
                 profile_readable: Callable, profile_reference_input: Callable) -> None:
        self._connect = connection_factory
        self._get_versioned_voice_profile = read_voice
        self._get_person_profile = read_person
        self._current_authorization = current_authorization
        self._get_authorization = read_authorization
        self.task_records = task_records
        self.list_applicable_pronunciation_entries = list_pronunciation
        self.profile_readable = profile_readable
        self.profile_reference_input = profile_reference_input

    def resolve_narration_inputs(
        self,
        *,
        workspace_id: str,
        episode_id: str,
        voice_profile_id: str,
        voice_version: str | None = None,
    ) -> VersionedNarrationInputs:
        with self._connect() as conn:
            historical_voice = self._get_versioned_voice_profile(
                conn,
                workspace_id,
                voice_profile_id,
                voice_version,
            )
            current_voice = self._get_versioned_voice_profile(conn, workspace_id, voice_profile_id)
            voice = replace(
                historical_voice,
                use_state=(historical_voice.use_state if historical_voice.use_state != "available"
                           else current_voice.use_state),
                visibility=current_voice.visibility,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_voice.authorization,
                ),
            )
        return VersionedNarrationInputs(
            voice=voice,
            pronunciation_entries=tuple(
                self.list_applicable_pronunciation_entries(
                    workspace_id=workspace_id,
                    person_profile_id=voice.person_profile_id,
                    episode_id=episode_id,
                )
            ),
        )

    def resolve_avatar_lipsync_request(
        self,
        *,
        workspace_id: str,
        person_id: str,
        person_version: str,
        voice_profile_id: str,
        voice_version: str,
        audio_path: str,
        audio_cache_key: str,
        language: str,
        motion: str,
        expression: str,
        authorization_id: str,
        authorization_version: str,
        model_id: str,
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
        for_generation: bool = True,
    ) -> AvatarAndLipSyncRequest:
        if language not in {"zh", "en", "zh-en"}:
            raise ValueError("人物口型语言必须是 zh、en 或 zh-en")
        with self._connect() as conn:
            historical_person = self._get_person_profile(
                conn,
                workspace_id,
                person_id,
                person_version,
            )
            historical_voice = self._get_versioned_voice_profile(
                conn,
                workspace_id,
                voice_profile_id,
                voice_version,
            )
            current_person = self._get_person_profile(conn, workspace_id, person_id)
            current_voice = self._get_versioned_voice_profile(conn, workspace_id, voice_profile_id)
            person = replace(
                historical_person,
                use_state=(historical_person.use_state if historical_person.use_state != "available" else current_person.use_state),
                visibility=current_person.visibility,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_person.authorization,
                ),
            )
            voice = replace(
                historical_voice,
                use_state=(historical_voice.use_state if historical_voice.use_state != "available" else current_voice.use_state),
                visibility=current_voice.visibility,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_voice.authorization,
                ),
            )
            if bool(authorization_id) != bool(authorization_version):
                raise ValueError("授权引用必须同时指定ID和版本")
            requested_authorization = (self._get_authorization(
                conn, workspace_id, authorization_id, authorization_version,
            ) if authorization_id else None)
            authorization = self._current_authorization(
                conn,
                workspace_id,
                requested_authorization,
            )
        if not self.profile_readable(person, viewer_user_id, local_diagnostic) or not self.profile_readable(voice, viewer_user_id, local_diagnostic):
            raise PermissionError("档案不属于当前成员或已取消共享")
        visual_input = None
        if for_generation:
            self.profile_reference_input(voice, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
            visual_input = self.profile_reference_input(person, viewer_user_id=viewer_user_id,
                                                        local_diagnostic=local_diagnostic)
        return AvatarAndLipSyncRequest(
            workspace_id=workspace_id,
            person=person,
            voice=voice,
            audio_path=audio_path,
            audio_cache_key=audio_cache_key,
            language=language,
            motion=motion,
            expression=expression,
            authorization=authorization,
            model_id=model_id,
            visual_path=visual_input.source_path if visual_input is not None else "",
            visual_reference_input=visual_input,
        )

    def selected_episode_versioned_voice_identity(
        self,
        workspace_id: str,
        episode_id: str,
    ) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT voice_profile_id, voice_version FROM episode_voice_selections
                WHERE workspace_id = ? AND episode_id = ?
                """,
                (workspace_id, episode_id),
            ).fetchone()
        if row is None:
            return None
        version = row["voice_version"]
        if not isinstance(version, str) or not version.strip():
            raise _MediaAuthorityError(
                reason="legacy_voice_selection_requires_migration",
                impact="历史未版本化声音选择不会用于新的配音或渲染。",
                repair_action="在人物与声音中选择已授权的声音版本后重新开始该步骤。",
            )
        return str(row["voice_profile_id"]), version

    def resolve_episode_narration_inputs(
        self,
        workspace_id: str,
        episode_id: str,
    ) -> VersionedNarrationInputs:
        identity = self.selected_episode_versioned_voice_identity(
            workspace_id,
            episode_id,
        )
        if identity is None:
            raise _MediaAuthorityError(
                reason="voice_selection_required",
                impact="当前创作任务未生成配音，未调用声音模型。",
                repair_action="为该创作任务选择已授权的声音版本后重试。",
            )
        voice_profile_id, voice_version = identity
        inputs = self.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=episode_id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
        authorization_failure = profile_use_failure(inputs.voice,
            content_type="voice",
            usage_scope="narration",
        )
        if authorization_failure is not None:
            raise _MediaAuthorityError(
                reason=f"voice_authorization_{authorization_failure}",
                impact="声音授权当前不可用，未调用声音模型或复用旧配音。",
                repair_action="恢复或更新该声音的 narration 授权后重试。",
            )
        return inputs

    def resolve_creation_task_narration_inputs(
        self,
        *,
        workspace_id: str,
        task_id: str,
        voice_profile_id: str,
        voice_version: str | None = None,
    ) -> VersionedNarrationInputs:
        inputs = self.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=task_id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
        task = self.task_records.get_creation_task(workspace_id, task_id)
        self.profile_reference_input(inputs.voice, viewer_user_id=task.owner_user_id)
        return inputs
