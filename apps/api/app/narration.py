from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.models import Episode, EpisodeStatus, utc_now
from app.avatar_lipsync import PronunciationEntry, VoiceProfile as VersionedVoiceProfile
from app.production import ProductionProfile, VoiceProfile
from app.voice_clone import (
    MediaRuntimeFailure,
    VoiceCloneError,
    VoiceCloneProvider,
    VoiceCloneResult,
    text_to_speech_preflight,
)

if TYPE_CHECKING:
    from app.production import ProductionRepository


@dataclass(frozen=True)
class NarrationArtifact:
    audio_path: str
    duration_seconds: float
    sample_rate: int
    model_id: str
    episode_id: str | None = None
    task_id: str | None = None
    script_id: str | None = None
    voice_profile_id: str | None = None
    cache_key: str = ""
    language: str = "zh"
    pronunciation_version: str = ""


class NarrationGenerator:
    def __init__(self, output_root: str | Path, provider: VoiceCloneProvider) -> None:
        self.output_root = Path(output_root).expanduser()
        self.provider = provider

    def generate(
        self,
        *,
        workspace_id: str,
        episode: Episode,
        profile: ProductionProfile,
        voice_profile: VoiceProfile | None = None,
        voice_identity: VersionedVoiceProfile | None = None,
        pronunciation_entries: tuple[PronunciationEntry, ...] = (),
        language: str | None = None,
        resolved_reference_audio_path: str | Path | None = None,
    ) -> NarrationArtifact:
        if episode.workspace_id != workspace_id or profile.workspace_id != workspace_id:
            raise ValueError("Episode 或生产档案不属于当前工作区")
        if episode.script is None:
            raise ValueError("Episode 尚未生成文案")
        if episode.status not in {
            EpisodeStatus.SCRIPT_APPROVED,
            EpisodeStatus.WAITING_VIDEO,
            EpisodeStatus.VIDEO_PROCESSING,
            EpisodeStatus.DRAFT_READY,
            EpisodeStatus.PUBLISHED,
        }:
            raise ValueError("只有已确认文案的 Episode 才能生成配音")
        if voice_profile is not None and voice_identity is not None:
            raise ValueError("一次配音只能选择一个声音版本")
        if voice_identity is not None:
            if voice_identity.workspace_id != workspace_id:
                raise ValueError("声音版本不属于当前工作区")
            if voice_identity.authorization.workspace_id != workspace_id:
                raise ValueError("声音授权不属于当前工作区")
            authorization_failure = voice_identity.authorization.failure_for(
                content_type="voice", usage_scope="narration"
            )
            if authorization_failure is not None:
                raise ValueError(f"声音授权不可用：{authorization_failure}")
            reference_path = voice_identity.reference_audio_path
            reference_text = voice_identity.reference_text
            resolved_language = voice_identity.language
        else:
            consent_confirmed = (
                voice_profile.consent_confirmed
                if voice_profile is not None
                else profile.voice_consent_confirmed
            )
            reference_path = (
                voice_profile.reference_audio_path
                if voice_profile is not None
                else profile.voice_reference_path
            )
            reference_text = (
                voice_profile.reference_text
                if voice_profile is not None
                else profile.voice_reference_text
            )
            if not consent_confirmed:
                raise ValueError("尚未确认声音生成与发布授权")
            resolved_language = language or "zh"
        if language is not None and language != resolved_language:
            raise ValueError("配音语言必须与选定声音版本一致")
        if resolved_language not in {"zh", "en", "zh-en"}:
            raise ValueError("配音语言必须是 zh、en 或 zh-en")
        if not reference_path:
            raise ValueError("尚未设置声音参考文件")
        if voice_identity is not None and resolved_reference_audio_path is None:
            raise ValueError("版本化声音必须提供已解析的参考音频路径")
        if not reference_text.strip():
            raise ValueError("尚未填写声音参考文字")
        estimated_seconds = _estimated_narration_seconds(episode)
        if estimated_seconds > profile.target_duration_max_seconds:
            raise ValueError(
                "文案预计时长"
                f" {estimated_seconds:.0f} 秒，超过生产档案上限"
                f" {profile.target_duration_max_seconds} 秒；请先压缩并重新确认文案"
            )
        narration_text = _episode_narration_text(episode)
        pronunciation_dictionary = _pronunciation_dictionary(
            entries=pronunciation_entries,
            workspace_id=workspace_id,
            person_profile_id=voice_identity.person_profile_id if voice_identity else None,
            task_id=episode.id,
        )
        preflight_failure = text_to_speech_preflight(
            self.provider,
            language=resolved_language,
            pronunciation_dictionary=pronunciation_dictionary,
        )
        if preflight_failure is not None:
            raise VoiceCloneError(preflight_failure)
        pronunciation_version = _pronunciation_version(pronunciation_dictionary)
        cache_key = self.cache_key(
            episode=episode,
            profile=profile,
            voice_profile=voice_profile,
            voice_identity=voice_identity,
            pronunciation_entries=pronunciation_entries,
            language=resolved_language,
        )
        safe_workspace = _safe_component(workspace_id)
        output_name = f"{_safe_component(episode.id)}.wav"
        selected_voice_id = (
            voice_identity.id
            if voice_identity is not None
            else voice_profile.id if voice_profile is not None else None
        )
        if selected_voice_id is not None:
            output_name = (
                f"{_safe_component(episode.id)}_"
                f"{_safe_component(selected_voice_id)}_{cache_key[:10]}.wav"
            )
        output_path = (
            self.output_root
            / "narration"
            / safe_workspace
            / output_name
        )
        provider_reference_path = (
            Path(resolved_reference_audio_path).expanduser().resolve()
            if resolved_reference_audio_path is not None
            else Path(reference_path).expanduser().resolve()
        )
        result: VoiceCloneResult = self.provider.generate(
            text=narration_text,
            reference_audio_path=provider_reference_path,
            reference_text=reference_text,
            output_path=output_path,
            language=resolved_language,
            pronunciation_dictionary=pronunciation_dictionary,
        )
        if Path(result.audio_path).resolve() != output_path.resolve() or not output_path.is_file() or output_path.stat().st_size == 0:
            raise ValueError("声音克隆没有生成当前配音文件")
        if result.duration_seconds > profile.target_duration_max_seconds:
            raise ValueError(
                "实际配音时长"
                f" {result.duration_seconds:.1f} 秒，超过生产档案上限"
                f" {profile.target_duration_max_seconds} 秒；请先压缩并重新确认文案"
            )
        return NarrationArtifact(
            episode_id=episode.id,
            audio_path=result.audio_path,
            duration_seconds=result.duration_seconds,
            sample_rate=result.sample_rate,
            model_id=self.provider.model_id,
            voice_profile_id=(
                voice_identity.id
                if voice_identity is not None
                else voice_profile.id if voice_profile is not None else None
            ),
            cache_key=cache_key,
            language=resolved_language,
            pronunciation_version=pronunciation_version,
        )

    def generate_from_repository(
        self,
        *,
        repository: ProductionRepository,
        workspace_id: str,
        episode: Episode,
        profile: ProductionProfile,
        voice_profile_id: str,
        voice_version: str | None = None,
    ) -> NarrationArtifact:
        inputs = repository.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=episode.id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
        authorization_failure = inputs.voice.authorization.failure_for(
            content_type="voice",
            usage_scope="narration",
        )
        if authorization_failure is not None:
            raise ValueError(f"声音授权不可用：{authorization_failure}")
        resolved_reference_audio_path = repository.resolve_workspace_media_reference(
            workspace_id,
            inputs.voice.reference_audio_path,
        )
        if (
            not resolved_reference_audio_path.is_file()
            or resolved_reference_audio_path.stat().st_size == 0
        ):
            raise ValueError("声音参考文件不存在或为空")
        return self.generate(
            workspace_id=workspace_id,
            episode=episode,
            profile=profile,
            voice_identity=inputs.voice,
            pronunciation_entries=inputs.pronunciation_entries,
            resolved_reference_audio_path=resolved_reference_audio_path,
        )

    def generate_for_creation_task(
        self,
        *,
        workspace_id: str,
        task_id: str,
        script_id: str,
        narration_text: str,
        max_duration_seconds: int,
        voice_identity: VersionedVoiceProfile,
        pronunciation_entries: tuple[PronunciationEntry, ...],
        resolved_reference_audio_path: str | Path,
        segment_id: str | None = None,
        delivery_instruction: str | None = None,
        generation_identity: str | None = None,
    ) -> NarrationArtifact:
        """Generate a real CreationTask WAV without constructing an Episode."""

        text = narration_text.strip()
        if not text:
            raise ValueError("已确认文案没有可配音正文")
        if voice_identity.workspace_id != workspace_id:
            raise ValueError("声音版本不属于当前工作区")
        if voice_identity.language != "zh":
            raise ValueError("本地真成片 v0.1 只支持中文声音克隆")
        authorization_failure = voice_identity.authorization.failure_for(
            content_type="voice", usage_scope="narration"
        )
        if authorization_failure is not None:
            raise ValueError(f"声音授权不可用：{authorization_failure}")
        reference_text = voice_identity.reference_text.strip()
        if not reference_text:
            raise ValueError("声音版本缺少参考文字")
        reference_path = Path(resolved_reference_audio_path).expanduser().resolve()
        if not reference_path.is_file() or reference_path.suffix.lower() != ".wav":
            raise ValueError("声音参考文件必须是当前工作区已授权的 WAV")
        if reference_path.stat().st_size == 0:
            raise ValueError("声音参考 WAV 为空")
        if _estimated_text_seconds(text) > max_duration_seconds:
            raise ValueError("文案预计时长超过创作方案上限，请先压缩并重新确认文案")
        pronunciation_dictionary = _pronunciation_dictionary(
            entries=pronunciation_entries,
            workspace_id=workspace_id,
            person_profile_id=voice_identity.person_profile_id,
            task_id=task_id,
        )
        preflight_failure = text_to_speech_preflight(
            self.provider,
            language="zh",
            pronunciation_dictionary=pronunciation_dictionary,
        )
        if preflight_failure is not None:
            raise VoiceCloneError(preflight_failure)
        cache_key = self.creation_task_cache_key(
            workspace_id=workspace_id,
            task_id=task_id,
            script_id=script_id,
            narration_text=text,
            voice_identity=voice_identity,
            pronunciation_dictionary=pronunciation_dictionary,
            segment_id=segment_id,
            delivery_instruction=delivery_instruction,
            generation_identity=generation_identity,
        )
        output_path = (
            self.output_root
            / "creation-tasks"
            / _safe_component(workspace_id)
            / _safe_component(task_id)
            / (
                f"segment_{_safe_component(segment_id)}_{cache_key[:12]}.wav"
                if segment_id
                else f"narration_{cache_key[:12]}.wav"
            )
        )
        result: VoiceCloneResult = self.provider.generate(
            text=text,
            reference_audio_path=reference_path,
            reference_text=reference_text,
            output_path=output_path,
            language="zh",
            pronunciation_dictionary=pronunciation_dictionary,
        )
        resolved_output = output_path.resolve()
        if (
            Path(result.audio_path).resolve() != resolved_output
            or not resolved_output.is_file()
            or resolved_output.stat().st_size == 0
        ):
            raise ValueError("声音克隆没有生成当前创作任务的 WAV")
        tempo = _delivery_tempo(delivery_instruction)
        if tempo != 1.0:
            adjusted_path = output_path.with_name(f"{output_path.stem}_adjusted.wav")
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(resolved_output),
                    "-filter:a",
                    f"atempo={tempo:.3f}",
                    "-c:a",
                    "pcm_s16le",
                    str(adjusted_path),
                ],
                capture_output=True,
                text=True,
            )
            if completed.returncode or not adjusted_path.is_file() or adjusted_path.stat().st_size == 0:
                adjusted_path.unlink(missing_ok=True)
                raise ValueError(
                    "配音语速调整失败："
                    + (completed.stderr.strip() or f"ffmpeg 退出码 {completed.returncode}")
                )
            adjusted_path.replace(resolved_output)
            result = VoiceCloneResult(
                audio_path=str(resolved_output),
                duration_seconds=_probe_audio_duration(resolved_output),
                sample_rate=result.sample_rate,
            )
        if result.duration_seconds > max_duration_seconds:
            resolved_output.unlink(missing_ok=True)
            raise ValueError("实际配音时长超过创作方案上限，请先压缩并重新确认文案")
        return NarrationArtifact(
            task_id=task_id,
            script_id=script_id,
            audio_path=str(resolved_output),
            duration_seconds=result.duration_seconds,
            sample_rate=result.sample_rate,
            model_id=self.provider.model_id,
            voice_profile_id=voice_identity.id,
            cache_key=cache_key,
            language="zh",
            pronunciation_version=_pronunciation_version(pronunciation_dictionary),
        )

    def combine_creation_task_segments(
        self,
        *,
        workspace_id: str,
        task_id: str,
        script_id: str,
        segments: list[NarrationArtifact],
        max_duration_seconds: int,
    ) -> NarrationArtifact:
        if not segments:
            raise ValueError("没有可拼合的配音片段")
        if any(item.task_id != task_id or item.script_id != script_id for item in segments):
            raise ValueError("配音片段与当前任务文案不一致")
        cache_key = hashlib.sha256(
            "\n".join(item.cache_key for item in segments).encode("utf-8")
        ).hexdigest()
        output_path = (
            self.output_root
            / "creation-tasks"
            / _safe_component(workspace_id)
            / _safe_component(task_id)
            / f"narration_{cache_key[:12]}.wav"
        ).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        source_paths = [Path(item.audio_path).expanduser().resolve() for item in segments]
        if any(not path.is_file() or path.stat().st_size == 0 for path in source_paths):
            raise ValueError("存在缺失或为空的配音片段")
        if len(source_paths) == 1:
            shutil.copyfile(source_paths[0], output_path)
        else:
            command = ["ffmpeg", "-y"]
            for path in source_paths:
                command.extend(["-i", str(path)])
            inputs = "".join(f"[{index}:a]" for index in range(len(source_paths)))
            command.extend(
                [
                    "-filter_complex",
                    f"{inputs}concat=n={len(source_paths)}:v=0:a=1[out]",
                    "-map",
                    "[out]",
                    "-c:a",
                    "pcm_s16le",
                    str(output_path),
                ]
            )
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode or not output_path.is_file() or output_path.stat().st_size == 0:
                output_path.unlink(missing_ok=True)
                raise ValueError(
                    "配音片段拼合失败："
                    + (completed.stderr.strip() or f"ffmpeg 退出码 {completed.returncode}")
                )
        duration = _probe_audio_duration(output_path)
        if duration > max_duration_seconds:
            output_path.unlink(missing_ok=True)
            raise ValueError("拼合后的配音超过创作方案上限，请先压缩并重新确认文案")
        first = segments[0]
        return NarrationArtifact(
            task_id=task_id,
            script_id=script_id,
            audio_path=str(output_path),
            duration_seconds=duration,
            sample_rate=first.sample_rate,
            model_id=first.model_id,
            voice_profile_id=first.voice_profile_id,
            cache_key=cache_key,
            language=first.language,
            pronunciation_version=first.pronunciation_version,
        )

    def creation_task_cache_key(
        self,
        *,
        workspace_id: str,
        task_id: str,
        script_id: str,
        narration_text: str,
        voice_identity: VersionedVoiceProfile,
        pronunciation_dictionary: dict[str, str],
        segment_id: str | None = None,
        delivery_instruction: str | None = None,
        generation_identity: str | None = None,
    ) -> str:
        payload = {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "script_id": script_id,
            "text": narration_text,
            "language": voice_identity.language,
            "voice": {
                "id": voice_identity.id,
                "version": voice_identity.version,
                "person_profile_id": voice_identity.person_profile_id,
                "person_version": voice_identity.person_version,
                "model_id": voice_identity.model_id,
                "authorization_id": voice_identity.authorization.id,
                "authorization_version": voice_identity.authorization.version,
                "authorization_status": voice_identity.authorization.status,
            },
            "pronunciation_dictionary": pronunciation_dictionary,
            "provider_model_id": self.provider.model_id,
            "segment_id": segment_id,
            "delivery_instruction": delivery_instruction,
            "generation_identity": generation_identity,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def cache_key(
        self,
        *,
        episode: Episode,
        profile: ProductionProfile,
        voice_profile: VoiceProfile | None = None,
        voice_identity: VersionedVoiceProfile | None = None,
        pronunciation_entries: tuple[PronunciationEntry, ...] = (),
        language: str | None = None,
    ) -> str:
        if episode.script is None:
            raise ValueError("Episode 尚未生成文案")
        if voice_profile is not None and voice_identity is not None:
            raise ValueError("一次配音只能选择一个声音版本")
        legacy_voice_identity = (
            f"{voice_profile.id}|{voice_profile.updated_at.isoformat()}|"
            f"{voice_profile.reference_audio_path}|{voice_profile.reference_text}"
            if voice_profile is not None
            else f"legacy|{profile.updated_at.isoformat()}|{profile.voice_reference_path}|{profile.voice_reference_text}"
        )
        resolved_language = language or (voice_identity.language if voice_identity else "zh")
        if resolved_language not in {"zh", "en", "zh-en"}:
            raise ValueError("配音语言必须是 zh、en 或 zh-en")
        pronunciation_dictionary = _pronunciation_dictionary(
            entries=pronunciation_entries,
            workspace_id=episode.workspace_id,
            person_profile_id=voice_identity.person_profile_id if voice_identity else None,
            task_id=episode.id,
        )
        versioned_identity = (
            {
                "id": voice_identity.id,
                "version": voice_identity.version,
                "person_profile_id": voice_identity.person_profile_id,
                "person_version": voice_identity.person_version,
                "model_id": voice_identity.model_id,
                "authorization": {
                    "id": voice_identity.authorization.id,
                    "version": voice_identity.authorization.version,
                    "status": voice_identity.authorization.status,
                    "expires_at": voice_identity.authorization.expires_at.isoformat()
                    if voice_identity.authorization.expires_at
                    else None,
                },
            }
            if voice_identity is not None
            else legacy_voice_identity
        )
        payload = (
            f"{episode.script.id}|{_episode_narration_text(episode)}|"
            f"{json.dumps(versioned_identity, ensure_ascii=False, sort_keys=True)}|"
            f"{resolved_language}|{json.dumps(pronunciation_dictionary, ensure_ascii=False, sort_keys=True)}|"
            f"{self.provider.model_id}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class VoicePreviewService:
    """Run a short, persisted voice preview without inventing media output."""

    capability_id = "voice_clone"

    def __init__(self, output_root: str | Path, provider: VoiceCloneProvider) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.provider = provider

    def request(
        self,
        *,
        repository: ProductionRepository,
        workspace_id: str,
        voice_profile_id: str,
        preview_text: str,
        episode_id: str = "voice_preview",
        voice_version: str | None = None,
        language: str | None = None,
        retry: bool = False,
    ):
        from app.production import MediaPreviewRunRecord

        inputs = repository.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=episode_id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
        voice = inputs.voice
        resolved_language = language or voice.language
        identity = _voice_preview_identity(
            voice=voice,
            preview_text=preview_text,
            language=resolved_language,
            pronunciation_entries=inputs.pronunciation_entries,
            provider_model_id=self.provider.model_id,
        )
        existing = repository.get_media_preview_run(workspace_id, "voice", identity)
        succeeded_reusable = not (
            existing is not None
            and existing.status == "succeeded"
            and not _valid_preview_output(existing.output_path, self.output_root)
        )

        candidate = MediaPreviewRunRecord(
            id=f"voice_preview_{identity}",
            workspace_id=workspace_id,
            preview_type="voice",
            identity=identity,
            status="running",
            capability_id=self.capability_id,
            model_id=self.provider.model_id,
            language=resolved_language,
            person_profile_id=voice.person_profile_id,
            person_version=voice.person_version,
            voice_profile_id=voice.id,
            voice_version=voice.version,
            authorization_id=voice.authorization.id,
            authorization_version=voice.authorization.version,
        )
        running, claimed = repository.claim_media_preview_run(
            candidate,
            retry=retry,
            succeeded_reusable=succeeded_reusable,
        )
        if not claimed:
            return running
        if existing is not None and existing.status == "succeeded":
            return self._finish(
                repository,
                running,
                status="failed",
                reason="output_missing",
                impact="已记录的声音预览输出不可用，不能作为成功结果复用。",
                repair_action="确认输出目录后显式重试声音预览。",
            )
        authorization_failure = voice.authorization.failure_for(
            content_type="voice",
            usage_scope="narration",
        )
        if authorization_failure is not None:
            return self._finish(
                repository,
                running,
                status="blocked",
                reason=authorization_failure,
                impact="声音预览被授权门禁阻断，不会生成或复用音频。",
                repair_action="补充有效授权后以新的预览身份重试。",
            )
        if resolved_language != voice.language:
            return self._finish(
                repository,
                running,
                status="unavailable",
                reason="language_mismatch",
                impact="请求语言与当前声音版本不一致，未生成音频。",
                repair_action="选择与声音版本一致的语言后重试。",
            )
        pronunciation_dictionary = _pronunciation_dictionary(
            entries=inputs.pronunciation_entries,
            workspace_id=workspace_id,
            person_profile_id=voice.person_profile_id,
            task_id=episode_id,
        )
        preflight_failure = text_to_speech_preflight(
            self.provider,
            language=resolved_language,
            pronunciation_dictionary=pronunciation_dictionary,
        )
        if preflight_failure is not None:
            return self._finish(
                repository,
                running,
                status="unavailable",
                reason=preflight_failure.reason,
                impact=preflight_failure.impact,
                repair_action=preflight_failure.repair_action,
            )
        try:
            reference = repository.resolve_workspace_media_reference(
                workspace_id,
                voice.reference_audio_path,
            )
        except ValueError as exc:
            return self._finish(
                repository,
                running,
                status="failed",
                reason="reference_audio_invalid",
                impact="声音预览参考音频不在当前工作区已授权媒体根目录，未生成音频。",
                repair_action=f"选择 workspace:// 媒体引用或已授权根内绝对路径后重试：{exc}",
            )
        if not reference.is_file() or reference.stat().st_size == 0:
            return self._finish(
                repository,
                running,
                status="failed",
                reason="reference_audio_missing",
                impact="声音预览缺少可用参考音频，未生成音频。",
                repair_action="选择当前工作区已授权的非空参考音频。",
            )
        if not preview_text.strip():
            return self._finish(
                repository,
                running,
                status="failed",
                reason="preview_text_empty",
                impact="声音预览没有可合成的文本。",
                repair_action="提供简短预览文本后重试。",
            )
        output_path = (
            self.output_root
            / "previews"
            / "voice"
            / _safe_component(workspace_id)
            / f"{identity}.wav"
        )
        try:
            result = self.provider.generate(
                text=preview_text.strip(),
                reference_audio_path=reference,
                reference_text=voice.reference_text,
                language=resolved_language,
                pronunciation_dictionary=pronunciation_dictionary,
                output_path=output_path,
            )
        except (VoiceCloneError, OSError) as exc:
            failure = _voice_generation_failure(exc)
            return self._finish(
                repository,
                running,
                status="failed",
                reason=failure.reason,
                impact=failure.impact,
                repair_action=failure.repair_action,
            )
        if (
            Path(result.audio_path).resolve() != output_path.resolve()
            or not _valid_preview_output(result.audio_path, self.output_root)
        ):
            return self._finish(
                repository,
                running,
                status="failed",
                reason="output_invalid",
                impact="声音预览输出缺失、为空或越出允许输出目录。",
                repair_action="检查声音 provider 输出路径与文件后重试。",
            )
        return self._finish(
            repository,
            running,
            status="succeeded",
            output_path=str(output_path),
        )

    @staticmethod
    def _finish(repository, record, *, status, output_path=None, reason=None, impact=None, repair_action=None):
        from dataclasses import replace

        completed = replace(
            record,
            status=status,
            output_path=output_path,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            finished_at=utc_now(),
        )
        return repository.save_media_preview_run(completed)


def _voice_generation_failure(exc: VoiceCloneError | OSError) -> MediaRuntimeFailure:
    if isinstance(exc, VoiceCloneError):
        return exc.failure
    return MediaRuntimeFailure(
        reason="provider_failed",
        impact="声音预览生成失败，未产生可用音频。",
        repair_action=f"检查本地声音运行环境后重试：{exc}",
        retryable=True,
    )


def _episode_narration_text(episode: Episode) -> str:
    if episode.script is None:
        return ""
    parts = [episode.script.hook.strip()]
    parts.extend(section.content.strip() for section in episode.script.sections)
    return "\n".join(part for part in parts if part)


def _estimated_narration_seconds(episode: Episode) -> float:
    if episode.script is None:
        return 0.0
    hook_seconds = 5.0 if episode.script.hook.strip() else 0.0
    return hook_seconds + sum(
        max(0.0, float(section.estimated_seconds))
        for section in episode.script.sections
        if section.content.strip()
    )


def _estimated_text_seconds(text: str) -> float:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", text))
    return chinese_chars / 4.0 + latin_words / 2.5


def _delivery_tempo(instruction: str | None) -> float:
    if not instruction:
        return 1.0
    value = instruction.casefold()
    if any(token in value for token in ("慢一点", "慢些", "slower")):
        return 0.9
    if any(token in value for token in ("快一点", "快些", "faster")):
        return 1.1
    return 1.0


def _probe_audio_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise ValueError(
            "无法验证配音时长："
            + (completed.stderr.strip() or f"ffprobe 退出码 {completed.returncode}")
        )
    try:
        duration = float(completed.stdout.strip())
    except ValueError as exc:
        raise ValueError("ffprobe 没有返回有效的配音时长") from exc
    if duration <= 0:
        raise ValueError("配音时长无效")
    return duration


def _safe_component(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return clean[:80] or "item"


def _pronunciation_dictionary(
    *,
    entries: tuple[PronunciationEntry, ...],
    workspace_id: str,
    person_profile_id: str | None,
    task_id: str,
) -> tuple[dict[str, str], ...]:
    applicable = [
        entry.task_input()
        for entry in entries
        if entry.applies_to(
            workspace_id=workspace_id,
            person_profile_id=person_profile_id,
            task_id=task_id,
        )
    ]
    return tuple(sorted(applicable, key=lambda entry: (entry["scope"], entry["id"], entry["version"])))


def _pronunciation_version(entries: tuple[dict[str, str], ...]) -> str:
    return hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _voice_preview_identity(
    *,
    voice: VersionedVoiceProfile,
    preview_text: str,
    language: str,
    pronunciation_entries: tuple[PronunciationEntry, ...],
    provider_model_id: str,
) -> str:
    payload = {
        "voice_id": voice.id,
        "voice_version": voice.version,
        "person_id": voice.person_profile_id,
        "person_version": voice.person_version,
        "authorization": {
            "id": voice.authorization.id,
            "version": voice.authorization.version,
            "status": voice.authorization.status,
            "expires_at": voice.authorization.expires_at.isoformat()
            if voice.authorization.expires_at
            else None,
        },
        "voice_model_id": voice.model_id,
        "provider_model_id": provider_model_id,
        "language": language,
        "preview_text": preview_text.strip(),
        "pronunciation": [entry.task_input() for entry in pronunciation_entries],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _valid_preview_output(output_path: str | None, output_root: Path) -> bool:
    if not output_path:
        return False
    output = Path(output_path).expanduser().resolve()
    try:
        output.relative_to(output_root)
    except ValueError:
        return False
    try:
        return output.is_file() and output.stat().st_size > 0
    except OSError:
        return False
