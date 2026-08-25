from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.models import Episode, EpisodeStatus
from app.production import ProductionProfile
from app.voice_clone import VoiceCloneProvider, VoiceCloneResult


@dataclass(frozen=True)
class NarrationArtifact:
    episode_id: str
    audio_path: str
    duration_seconds: float
    sample_rate: int
    model_id: str


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
        if not profile.voice_consent_confirmed:
            raise ValueError("尚未确认声音生成与发布授权")
        if not profile.voice_reference_path:
            raise ValueError("尚未设置声音参考文件")
        if not profile.voice_reference_text.strip():
            raise ValueError("尚未填写声音参考文字")
        if not self.provider.ready:
            raise ValueError(self.provider.message)
        narration_text = _episode_narration_text(episode)
        safe_workspace = _safe_component(workspace_id)
        output_path = (
            self.output_root
            / "narration"
            / safe_workspace
            / f"{_safe_component(episode.id)}.wav"
        )
        result: VoiceCloneResult = self.provider.generate(
            text=narration_text,
            reference_audio_path=profile.voice_reference_path,
            reference_text=profile.voice_reference_text,
            output_path=output_path,
        )
        return NarrationArtifact(
            episode_id=episode.id,
            audio_path=result.audio_path,
            duration_seconds=result.duration_seconds,
            sample_rate=result.sample_rate,
            model_id=self.provider.model_id,
        )


def _episode_narration_text(episode: Episode) -> str:
    if episode.script is None:
        return ""
    parts = [episode.script.hook.strip()]
    parts.extend(section.content.strip() for section in episode.script.sections)
    return "\n".join(part for part in parts if part)


def _safe_component(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return clean[:80] or "item"
