"""Workspace production foundation conditions from finite metadata read ports."""
from __future__ import annotations

from collections.abc import Callable

from app.materials.contracts import MaterialAsset, selectable_segment_usage_role
from app.profiles.contracts import VoiceProfile
from app.profiles.workspace_profile import ProductionProfile


class ProductionFoundationReadiness:
    def __init__(
        self, *, get_profile: Callable[[str], ProductionProfile],
        list_assets: Callable[..., list[MaterialAsset]],
        list_versioned_voices: Callable[[str], list[VoiceProfile]],
        profile_readable: Callable[..., bool],
        profile_reference_input: Callable[..., object],
        material_segment_eligible: Callable[..., bool],
    ) -> None:
        self._get_profile = get_profile
        self._list_assets = list_assets
        self._list_versioned_voices = list_versioned_voices
        self._profile_readable = profile_readable
        self._profile_reference_input = profile_reference_input
        self._material_segment_eligible = material_segment_eligible

    def readiness(
        self,
        workspace_id: str,
        *,
        profile: ProductionProfile | None = None,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
        topic_sources_configured: bool = False,
    ) -> dict[str, object]:
        profile = profile or self._get_profile(workspace_id)
        assets = self._list_assets(
            workspace_id, include_private=viewer_user_id is not None or local_diagnostic,
            viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic,
        )
        qualified = [
            (asset, [segment for segment in asset.segments
                     if self._material_segment_eligible(asset, segment, workspace_id)])
            for asset in assets
        ]
        usable_assets = [asset for asset, segments in qualified if segments]
        usable_segments = [
            segment
            for asset, segments in qualified
            for segment in segments
            if selectable_segment_usage_role(asset, segment) == "b_roll"
        ]
        blockers: list[dict[str, str]] = []
        versioned_voices = self._list_versioned_voices(workspace_id)
        usable_voice = None
        reference_issue = None
        reference_issue_code = "voice_reference_unavailable"
        for voice in versioned_voices:
            if not self._profile_readable(voice, viewer_user_id, local_diagnostic):
                continue
            try:
                self._profile_reference_input(voice, viewer_user_id=viewer_user_id,
                                             local_diagnostic=local_diagnostic)
            except (ValueError, PermissionError, KeyError) as exc:
                reference_issue = str(exc)
                reference_issue_code = "voice_reference_unavailable"
            else:
                if not voice.reference_audio_path or not voice.reference_text.strip():
                    reference_issue = "声音档案缺少工作区参考音频或对应文字；仍可先讨论需求或编写文案。"
                    reference_issue_code = "voice_reference_missing"
                    continue
                usable_voice = voice
                break
        if usable_voice is None:
            blockers.append(
                {
                    "code": reference_issue_code if reference_issue else "versioned_voice_authorization_missing",
                    "message": reference_issue or "尚未登记可用于配音的版本化声音；仍可先讨论需求或编写文案。",
                }
            )
        if profile.voice_model_status != "ready":
            blockers.append(
                {
                    "code": "voice_model_not_ready",
                    "message": profile.voice_model_message,
                }
            )
        if not topic_sources_configured:
            blockers.append(
                {
                    "code": "source_pool_empty",
                    "message": "尚未配置可尝试的选题来源；仍可先讨论需求或编写文案。",
                }
            )
        if not usable_segments:
            blockers.append(
                {
                    "code": "approved_materials_empty",
                    "message": "可用素材库中还没有明确可作补充画面的镜头。",
                }
            )
        return {
            "ready": not blockers,
            "blockers": blockers,
            "asset_count": len(assets),
            "approved_asset_count": len(usable_assets),
            "approved_segment_count": len(usable_segments),
            "total_duration_seconds": round(
                sum(asset.duration_seconds or 0 for asset in assets), 3
            ),
        }
