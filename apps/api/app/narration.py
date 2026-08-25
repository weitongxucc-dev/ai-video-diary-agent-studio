from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.models import Episode, EpisodeStatus
from app.production import ProductionProfile, VoiceProfile
from app.voice_clone import VoiceCloneProvider, VoiceCloneResult


@dataclass(frozen=True)
class NarrationArtifact:
    episode_id: str
    audio_path: str
    duration_seconds: float
    sample_rate: int
    model_id: str
    voice_profile_id: str | None = None
    cache_key: str = ""


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
        if not reference_path:
            raise ValueError("尚未设置声音参考文件")
        if not reference_text.strip():
            raise ValueError("尚未填写声音参考文字")
        if not self.provider.ready:
            raise ValueError(self.provider.message)
        estimated_seconds = _estimated_narration_seconds(episode)
        if estimated_seconds > profile.target_duration_max_seconds:
            raise ValueError(
                "文案预计时长"
                f" {estimated_seconds:.0f} 秒，超过生产档案上限"
                f" {profile.target_duration_max_seconds} 秒；请先压缩并重新确认文案"
            )
        narration_text = _episode_narration_text(episode)
        cache_key = self.cache_key(
            episode=episode,
            profile=profile,
            voice_profile=voice_profile,
        )
        safe_workspace = _safe_component(workspace_id)
        output_name = f"{_safe_component(episode.id)}.wav"
        if voice_profile is not None:
            output_name = (
                f"{_safe_component(episode.id)}_"
                f"{_safe_component(voice_profile.id)}_{cache_key[:10]}.wav"
            )
        output_path = (
            self.output_root
            / "narration"
            / safe_workspace
            / output_name
        )
        result: VoiceCloneResult = self.provider.generate(
            text=narration_text,
            reference_audio_path=reference_path,
            reference_text=reference_text,
            output_path=output_path,
        )
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
            voice_profile_id=voice_profile.id if voice_profile is not None else None,
            cache_key=cache_key,
        )

    def cache_key(
        self,
        *,
        episode: Episode,
        profile: ProductionProfile,
        voice_profile: VoiceProfile | None = None,
    ) -> str:
        if episode.script is None:
            raise ValueError("Episode 尚未生成文案")
        voice_identity = (
            f"{voice_profile.id}|{voice_profile.updated_at.isoformat()}|"
            f"{voice_profile.reference_audio_path}|{voice_profile.reference_text}"
            if voice_profile is not None
            else f"legacy|{profile.updated_at.isoformat()}|{profile.voice_reference_path}|{profile.voice_reference_text}"
        )
        payload = (
            f"{episode.script.id}|{_episode_narration_text(episode)}|"
            f"{voice_identity}|{self.provider.model_id}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _safe_component(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return clean[:80] or "item"
