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
from app.speech_timing import estimate_speech_seconds
from app.avatar_lipsync import profile_use_failure, profile_cache_identity, PronunciationEntry, VoiceProfile as VersionedVoiceProfile
from app.production import ProductionProfile, VoiceProfile
from app.voice_clone import (
    MediaRuntimeFailure,
    VoiceCloneError,
    VoiceCloneProvider,
    VoiceCloneResult,
    Qwen3TTSSubprocessProvider,
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
            authorization_failure = profile_use_failure(voice_identity,
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
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
    ) -> NarrationArtifact:
        inputs = repository.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=episode.id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
        authorization_failure = profile_use_failure(inputs.voice,
            content_type="voice",
            usage_scope="narration",
        )
        if authorization_failure is not None:
            raise ValueError(f"声音授权不可用：{authorization_failure}")
        resolved_reference_audio_path = repository.resolve_voice_reference(
            inputs.voice, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic,
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
        language: str | None = None,
        grant_context=None,
    ) -> NarrationArtifact:
        """Generate a real CreationTask WAV without constructing an Episode."""

        if grant_context is not None:
            grant_context.check()
            if not isinstance(self.provider, Qwen3TTSSubprocessProvider):
                raise ValueError("当前声音提供者没有受许可的隔离子进程执行路径")

        tempo = _delivery_tempo(delivery_instruction)
        text = narration_text.strip()
        if not text:
            raise ValueError("已确认文案没有可配音正文")
        if voice_identity.workspace_id != workspace_id:
            raise ValueError("声音版本不属于当前工作区")
        resolved_language = language or voice_identity.language
        if resolved_language not in {"zh", "en", "zh-en"}:
            raise ValueError("配音语言无效")
        if voice_identity.language not in {resolved_language, "zh-en"}:
            raise ValueError("所选声音版本未声明支持本次配音语言，请选择对应语言的声音版本")
        authorization_failure = profile_use_failure(voice_identity,
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
        if estimate_speech_seconds(text) > max_duration_seconds:
            raise ValueError("文案预计时长超过创作方案上限，请先压缩并重新确认文案")
        pronunciation_dictionary = _pronunciation_dictionary(
            entries=pronunciation_entries,
            workspace_id=workspace_id,
            person_profile_id=voice_identity.person_profile_id,
            task_id=task_id,
        )
        preflight_failure = text_to_speech_preflight(
            self.provider,
            language=resolved_language,
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
            language=resolved_language,
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
            language=resolved_language,
            pronunciation_dictionary=pronunciation_dictionary,
            **({"grant_context": grant_context} if grant_context is not None else {}),
        )
        resolved_output = output_path.resolve()
        if (
            Path(result.audio_path).resolve() != resolved_output
            or not resolved_output.is_file()
            or resolved_output.stat().st_size == 0
        ):
            raise ValueError("声音克隆没有生成当前创作任务的 WAV")
        if tempo != 1.0:
            adjusted_path = output_path.with_name(f"{output_path.stem}_adjusted.wav")
            completed = _run_narration_command(
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
                grant_context=grant_context, output_paths=(resolved_output, adjusted_path),
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
                duration_seconds=_probe_audio_duration(resolved_output, grant_context=grant_context),
                sample_rate=result.sample_rate,
            )
        if result.duration_seconds > max_duration_seconds:
            resolved_output.unlink(missing_ok=True)
            raise ValueError(
                "实际配音时长超过创作方案上限，请先压缩并重新确认文案；"
                f"实际 {result.duration_seconds:.2f} 秒，上限 {max_duration_seconds:g} 秒。"
            )
        return NarrationArtifact(
            task_id=task_id,
            script_id=script_id,
            audio_path=str(resolved_output),
            duration_seconds=result.duration_seconds,
            sample_rate=result.sample_rate,
            model_id=self.provider.model_id,
            voice_profile_id=voice_identity.id,
            cache_key=cache_key,
            language=resolved_language,
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
        grant_context=None,
    ) -> NarrationArtifact:
        if not segments:
            raise ValueError("没有可拼合的配音片段")
        if len({item.language for item in segments}) != 1:
            raise ValueError("配音片段语言不一致，请按同一目标语言重新生成")
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
            if grant_context is not None:
                grant_context.check()
                if source_paths[0].stat().st_size > grant_context.max_output_bytes:
                    raise ValueError("配音拼合超过阶段输出预算")
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
            completed = _run_narration_command(command, grant_context=grant_context, output_paths=(output_path,))
            if completed.returncode or not output_path.is_file() or output_path.stat().st_size == 0:
                output_path.unlink(missing_ok=True)
                raise ValueError(
                    "配音片段拼合失败："
                    + (completed.stderr.strip() or f"ffmpeg 退出码 {completed.returncode}")
                )
        duration = _probe_audio_duration(output_path, grant_context=grant_context)
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
        language: str | None = None,
    ) -> str:
        payload = {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "script_id": script_id,
            "text": narration_text,
            "language": language or voice_identity.language,
            "voice": profile_cache_identity(voice_identity),
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
        versioned_identity = profile_cache_identity(voice_identity) if voice_identity is not None else legacy_voice_identity

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
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
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
        reference_error = None
        try:
            reference = repository.resolve_voice_reference(
                voice, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic,
            )
        except (ValueError, KeyError) as exc:
            reference_error = str(exc)
        # Recheck eligibility and source revision before reusing any successful preview.
        reference_facts = ({"error": reference_error} if reference_error is not None else {
            "size": reference.stat().st_size, "mtime_ns": reference.stat().st_mtime_ns,
            "ctime_ns": reference.stat().st_ctime_ns,
        })
        identity = hashlib.sha256((identity + json.dumps(reference_facts, sort_keys=True)).encode()).hexdigest()
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
            authorization_id=voice.authorization.id if voice.authorization else "",
            authorization_version=voice.authorization.version if voice.authorization else "",
        )
        running, claimed = repository.claim_media_preview_run(
            candidate,
            retry=retry,
            succeeded_reusable=succeeded_reusable,
        )
        if not claimed:
            return running
        if reference_error is not None:
            return self._finish(repository, running, status="blocked", reason="reference_audio_invalid",
                                impact="原参考不可用于本次预览，未生成或复用音频。",
                                repair_action=reference_error)
        if existing is not None and existing.status == "succeeded":
            return self._finish(
                repository,
                running,
                status="failed",
                reason="output_missing",
                impact="已记录的声音预览输出不可用，不能作为成功结果复用。",
                repair_action="确认输出目录后显式重试声音预览。",
            )
        authorization_failure = profile_use_failure(voice,
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
        if voice.language not in {resolved_language, "zh-en"}:
            return self._finish(
                repository,
                running,
                status="unavailable",
                reason="language_mismatch",
                impact="请求语言与当前声音版本不一致，未生成音频。",
                repair_action="选择与声音版本一致的语言后重试。",
            )
        if not voice.reference_text.strip():
            return self._finish(
                repository, running, status="blocked", reason="reference_text_missing",
                impact="原参考已保存，但缺少对应文字，未调用声音模型。",
                repair_action="为该参考填写实际对应文字并保存新版本后再生成预览。",
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


def compile_narration_command(instruction: str) -> float:
    """Compile only delivery changes the local generation pipeline can execute."""
    clauses = re.split(r"[,，;；。.!！]|同时|并且|然后|\band\b|\bthen\b", instruction.casefold())
    tempos: set[float] = set()
    matched = False
    for clause in clauses:
        value = clause.strip()
        if not value:
            continue
        value = re.sub(r"^(?:请|麻烦)(?:你)?\s*", "", value)
        value = re.sub(r"^(?:这句|这一句|这段|当前配音|所选配音|配音)\s*", "", value)
        value = re.sub(r"^please\s+", "", value)
        if re.fullmatch(r"重新配音|重新读(?:一遍)?|重读(?:一遍)?|regenerate(?: (?:this |the )?(?:narration|sentence|segment))?", value):
            matched = True
            continue
        if re.fullmatch(r"(?:读|说|语速)?慢(?:一点|一些|些)|(?:read |speak )?(?:a little )?slower", value):
            tempos.add(0.9)
        elif re.fullmatch(r"(?:读|说|语速)?快(?:一点|一些|些)|(?:read |speak )?(?:a little )?faster", value):
            tempos.add(1.1)
        else:
            raise ValueError("配音要求尚不支持或含有歧义，请使用“这句重新配音”“这句读慢一点”或“这句读快一点”；其他修改请单独预览。")
        matched = True
    if not matched or len(tempos) > 1:
        raise ValueError("请指定一种配音操作，不能同时要求读快和读慢。")
    return next(iter(tempos), 1.0)


def _delivery_tempo(instruction: str | None) -> float:
    return 1.0 if instruction is None else compile_narration_command(instruction)


def _run_narration_command(command, *, grant_context=None, output_paths=()):
    if grant_context is None:
        return subprocess.run(command, capture_output=True, text=True)
    if command[0] == "ffmpeg":
        command = [command[0], "-threads", str(grant_context.threads),
                   "-filter_threads", str(grant_context.threads),
                   "-filter_complex_threads", str(grant_context.threads), *command[1:-1],
                   "-threads", str(grant_context.threads), command[-1]]
    import os
    result = grant_context.run(command, output_paths=output_paths,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    return subprocess.CompletedProcess(command, result.returncode,
        result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace"))


def _probe_audio_duration(path: Path, *, grant_context=None) -> float:
    completed = _run_narration_command(
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
        grant_context=grant_context,
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
        "identity": profile_cache_identity(voice),
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
