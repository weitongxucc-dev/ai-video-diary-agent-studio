"""Read and qualify fixed profile references without deriving media or running models."""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import replace
import json
import math
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING
import wave
from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MediaAuthorityError
from app.materials.source_files import MaterialSourceFileAccess
from app.materials.workspace_references import WorkspaceMediaReferenceAccess
from app.profiles.authorizations import MediaAuthorizationStore
from app.profiles.contracts import PersonProfile, VoiceProfile as VersionedVoiceProfile, profile_use_failure
from app.profiles.records import ProfileRecordStore
from app.source_policy import knowledge_item_readable as content_readable
if TYPE_CHECKING:
    from app.profile_reference_media import ProfileReferenceInput


class ProfileReferenceAccess:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection],
                 records: ProfileRecordStore, authorizations: MediaAuthorizationStore,
                 assets: MaterialAssetStore, workspace_references: WorkspaceMediaReferenceAccess,
                 source_files: MaterialSourceFileAccess) -> None:
        self._connect = connection_factory
        self._records = records
        self._authorizations = authorizations
        self._assets = assets
        self._workspace_references = workspace_references
        self._source_files = source_files

    def resolve_profile_reference(
        self, profile: PersonProfile | VersionedVoiceProfile, *,
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
        for_generation: bool = True,
    ) -> tuple[Path, float | None, float | None]:
        path, start, end = self._profile_reference_source(profile, viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic, for_generation=for_generation)
        if for_generation and start is not None:
            if not profile.reference.asset_id:
                raise ValueError("精确派生参考需要稳定来源素材")
            asset = self._assets.get_asset(profile.workspace_id, profile.reference.asset_id)
            if start != 0 or end != asset.duration_seconds:
                raise ValueError("参考范围需要精确派生输入，不能用整片替代所选范围")
        return path, start, end

    def _profile_reference_source(
        self, profile: PersonProfile | VersionedVoiceProfile, *,
        viewer_user_id: str | None, local_diagnostic: bool, for_generation: bool,
        probe_media: bool = True,
    ) -> tuple[Path, float | None, float | None]:
        """Re-read the source, membership and range; registration never runs a model."""
        with self._connect() as conn:
            row = self._records._latest_profile_payload_row(
                conn, profile.workspace_id, profile.id, person=isinstance(profile, PersonProfile))
            current = ((self._records._person_profile_from_payload if isinstance(profile, PersonProfile)
                        else self._records._versioned_voice_profile_from_payload)(conn, profile.workspace_id, json.loads(row["payload"]))
                       if row is not None else profile)
            if row is not None:
                current = replace(current, authorization=self._authorizations._current_authorization(conn, profile.workspace_id, current.authorization))
        if not content_readable(user_id=viewer_user_id, local_diagnostic=local_diagnostic,
                                visibility=current.visibility, owner_user_id=current.owner_user_id):
            raise PermissionError("档案不属于当前成员或已取消共享")
        if not content_readable(user_id=viewer_user_id, local_diagnostic=local_diagnostic,
                                visibility=profile.visibility, owner_user_id=profile.owner_user_id):
            raise PermissionError("档案不属于当前成员或已取消共享")
        if profile.reference_issue is not None:
            raise ValueError(f"历史参考不能可靠映射，请重新定位：{profile.reference_issue}")
        reference = profile.reference
        if reference is not None:
            _validate_profile_reference_fields(profile)
        logical = (profile.avatar_reference if isinstance(profile, PersonProfile)
                   else profile.reference_audio_path)
        path = self._workspace_references.resolve_workspace_media_reference(profile.workspace_id, logical,
                                                       require_registered=reference is None or not reference.asset_id)
        asset = None
        if reference is not None and reference.asset_id:
            asset = self._assets.get_asset(profile.workspace_id, reference.asset_id)
            if self._workspace_references.workspace_media_reference_for_asset(profile.workspace_id, asset.id) != logical:
                raise ValueError("参考文件与来源素材不一致")
        else:
            with self._connect() as conn:
                row = self._assets._asset_payload_row_by_path(conn, profile.workspace_id, str(path))
            if row is not None:
                asset = self._assets._asset_from_row(row)
        if asset is not None and not content_readable(user_id=viewer_user_id, local_diagnostic=local_diagnostic,
                                                      visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError("来源素材不属于当前成员或已取消共享")
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("参考文件失联或为空，请重新定位原参考")
        if asset is not None:
            if abs(path.stat().st_mtime - asset.modified_at.timestamp()) > .00001:
                raise ValueError("原参考已变化，请重新定位并登记新版本")
            if asset.media_type == "audio" and path.suffix.lower() == ".wav":
                intact = path.stat().st_size == asset.size_bytes
                _profile_wav_duration(path)
            else:
                intact = (self._source_files.material_file_intact(asset) if probe_media else
                          not path.is_symlink() and path.stat().st_size == asset.size_bytes)
            if not intact:
                raise ValueError("原参考已变化，请重新定位并登记新版本")
            if isinstance(profile, PersonProfile) and asset.media_type not in {"video", "image"}:
                raise ValueError("人物参考需要已登记的视频或图片")
            if isinstance(profile, VersionedVoiceProfile) and asset.media_type not in {"audio", "video"}:
                raise ValueError("声音参考需要已登记音频或视频")
            if asset.use_state == "excluded" or (for_generation and asset.use_state not in {"available", "not_for_publish"}):
                raise MediaAuthorityError(reason=f"source_{asset.use_state}",
                    impact="来源素材的使用限制阻断本次预览或生成，未采用该参考。",
                    repair_action=f"来源素材使用状态限制：{asset.use_state}；请在来源详情确认允许的用途或选择其他参考。")
            duration = asset.duration_seconds
            if asset.media_type != "image" and (duration is None or not math.isfinite(duration) or duration <= 0):
                raise ValueError("来源时长未知，请重新定位有效参考范围")
            if reference is not None and reference.segment_id:
                segment = next((item for item in asset.segments if item.id == reference.segment_id), None)
                if segment is None or segment.asset_id != asset.id:
                    raise ValueError("参考片段已失联，请重新选择")
                if reference.start_seconds is None or reference.end_seconds is None:
                    raise ValueError("片段参考需要明确起止时间")
                if reference.start_seconds < segment.start_seconds or reference.end_seconds > segment.end_seconds:
                    raise ValueError("参考范围超出来源片段")
                if segment.usage_role == "exclude":
                    raise ValueError("来源片段已排除使用")
        else:
            if isinstance(profile, PersonProfile):
                raise ValueError("人物参考缺少稳定素材登记，请重新定位")
            duration = _profile_wav_duration(path)
            if not local_diagnostic:
                raise PermissionError("历史声音来源缺少成员归属，请重新导入参考")
        if isinstance(profile, VersionedVoiceProfile) and path.suffix.lower() == ".wav":
            duration = _profile_wav_duration(path)
        start = reference.start_seconds if reference else None
        end = reference.end_seconds if reference else None
        if start is not None and (duration is None or end is None or end > duration):
            raise ValueError("参考范围超出有效来源时长")
        if for_generation:
            for version in (profile, current):
                failure = profile_use_failure(version, content_type=("person_likeness" if isinstance(profile, PersonProfile) else "voice"),
                                              usage_scope=("avatar_lipsync" if isinstance(profile, PersonProfile) else "narration"))
                if failure is not None:
                    raise ValueError(f"参考使用受限：{failure}")
        return path, start, end

    def profile_reference_input(self, profile: PersonProfile | VersionedVoiceProfile, *,
                                viewer_user_id: str | None = None, local_diagnostic: bool = False,
                                for_generation: bool = True) -> ProfileReferenceInput:
        """Qualified source facts only; never decode/derive during device admission."""
        from app.profile_reference_media import ProfileReferenceInput
        path, start, end = self._profile_reference_source(profile, viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic, for_generation=for_generation, probe_media=False)
        reference = profile.reference
        asset = self._assets.get_asset(profile.workspace_id, reference.asset_id) if reference and reference.asset_id else None
        kind = "voice_audio" if isinstance(profile, VersionedVoiceProfile) else "person_visual"
        if kind == "voice_audio" and asset and asset.media_type not in {"audio", "video"}:
            raise ValueError("声音参考需要有效音频或视频的声音范围")
        duration = asset.duration_seconds if asset else _profile_wav_duration(path)
        return ProfileReferenceInput.from_source(
            workspace_id=profile.workspace_id, profile_id=profile.id, profile_version=profile.version,
            asset_id=asset.id if asset else None, segment_id=reference.segment_id if reference else None,
            source_path=path, source_type=asset.media_type if asset else "audio", kind=kind,
            start_seconds=start, end_seconds=end, source_duration_seconds=duration)


def _validate_profile_reference_fields(profile) -> None:
    if profile.use_state not in {"available", "reference_only", "excluded", "not_for_automation", "not_for_publish"}:
        raise ValueError("档案使用状态无效")
    if profile.visibility not in {"private", "shared"}:
        raise ValueError("档案可见范围无效")
    reference = profile.reference
    if reference is None:
        raise ValueError("新档案需要明确来源引用，不能猜测历史来源")
    if profile.reference_issue is not None:
        raise ValueError("历史参考未可靠映射，请先重新定位来源")
    if reference.segment_id and not reference.asset_id:
        raise ValueError("参考片段必须绑定素材")
    if (reference.start_seconds is None) != (reference.end_seconds is None):
        raise ValueError("参考范围必须同时包含起止时间")
    if reference.start_seconds is not None:
        if any(type(value) not in {int, float} or not math.isfinite(value)
               for value in (reference.start_seconds, reference.end_seconds)):
            raise ValueError("参考时间必须是有限数值")
        if reference.start_seconds < 0 or reference.end_seconds <= reference.start_seconds:
            raise ValueError("参考范围无效")


def _profile_wav_duration(path: Path) -> float:
    if path.suffix.lower() != ".wav":
        raise ValueError("声音参考需要有效 WAV；视频来源需先明确派生音频")
    try:
        with wave.open(str(path), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if rate <= 0 or frames <= 0 or audio.getnchannels() not in {1, 2} or audio.getsampwidth() not in {1, 2, 3, 4}:
                raise ValueError("声音参考 WAV 无有效音频")
            duration = frames / rate
            if not 1 <= duration <= 120:
                raise ValueError("声音参考 WAV 时长必须在 1–120 秒之间")
            if len(audio.readframes(frames)) != frames * audio.getnchannels() * audio.getsampwidth():
                raise ValueError("声音参考 WAV 已截断")
            return duration
    except (EOFError, wave.Error) as exc:
        raise ValueError("声音参考不是有效 PCM WAV") from exc
