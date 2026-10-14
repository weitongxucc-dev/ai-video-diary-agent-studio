"""One prepared version change and one repository commit for every edit family."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from uuid import uuid4

from app.creation.contracts import CoverAsset, EditOperation, EditProposal, SubtitleTrack, TimelineVersion
from app.models import utc_now


@dataclass(frozen=True)
class EditChangeSet:
    operation: EditOperation
    version: TimelineVersion
    montage_plan_id: str | None
    montage_payload: dict | None
    subtitle_tracks: list[SubtitleTrack]
    cover_asset: CoverAsset | None
    narration_segments: list | None = None
    narration_artifact: object | None = None


def clone_subtitle_track(track, *, timeline_version_id, track_id=None, artifact_ref=None,
                         transform_id=None, cues=None, style=None, model_id=None):
    now = utc_now()
    has_artifact = artifact_ref is not None and transform_id is not None
    return replace(track, id=track_id or f"subtitle_{timeline_version_id}_{track.language.lower()}",
        timeline_version_id=timeline_version_id, style=track.style if style is None else style,
        cues=track.cues if cues is None else cues,
        model_id=model_id if model_id is not None else track.model_id,
        artifact_ref=artifact_ref if artifact_ref is not None else track.artifact_ref,
        transform_id=transform_id if transform_id is not None else track.transform_id,
        status="valid" if has_artifact else track.status,
        failure_reason=None if has_artifact else track.failure_reason,
        impact=None if has_artifact else track.impact, repair_action=None if has_artifact else track.repair_action,
        created_at=now, updated_at=now)


def clone_cover_asset(cover, *, timeline_version_id):
    now = utc_now()
    return replace(cover, id=f"cover_{uuid4().hex}", timeline_version_id=timeline_version_id,
        created_at=now, updated_at=now)


def prepare_change_set(*, proposal: EditProposal, base_version: TimelineVersion,
                       operation_type, operation_payload, result_version_id, montage_plan_id,
                       montage_payload, subtitle_tracks, cover_asset, snapshot_source=None,
                       narration_segments=None, narration_artifact=None, include_track_ids=True,
                       absent_cover_value=False):
    """Compute the whole immutable change before acquiring a SQLite write lock."""
    now = utc_now()
    operation = EditOperation(id=f"operation_{uuid4().hex}", workspace_id=proposal.workspace_id,
        task_id=proposal.task_id, proposal_id=proposal.id, operation_type=operation_type,
        payload=operation_payload, base_version_id=base_version.id, result_version_id=result_version_id,
        status="applied", created_at=now, applied_at=now)
    snapshot = dict(base_version.snapshot if snapshot_source is None else snapshot_source)
    if montage_payload is not None:
        snapshot["montage"] = montage_payload
    if include_track_ids:
        snapshot["subtitle_track_ids"] = [track.id for track in subtitle_tracks]
    if cover_asset is not None:
        snapshot["cover_asset_id"] = cover_asset.id
    elif absent_cover_value:
        snapshot["cover_asset_id"] = None
    else:
        snapshot.pop("cover_asset_id", None)
    if narration_segments is not None:
        snapshot["narration_segments"] = [asdict(segment) for segment in narration_segments]
    if narration_artifact is not None:
        snapshot["narration_cache_key"] = narration_artifact.cache_key
    snapshot.update(invalidated_refs=proposal.invalidated_refs, edit_operation_id=operation.id)
    version = TimelineVersion(id=result_version_id, workspace_id=proposal.workspace_id, task_id=proposal.task_id,
        parent_version_id=base_version.id, sequence=base_version.sequence+1, snapshot=snapshot,
        created_by_operation_id=operation.id, created_at=now)
    return EditChangeSet(operation, version, montage_plan_id, montage_payload,
        subtitle_tracks, cover_asset, narration_segments, narration_artifact)


def commit_change_set(repository, change: EditChangeSet, *, connection=None, verify_sources=None):
    """The repository owns the CAS and all cross-track writes, including grant checkpoints."""
    operation = change.operation
    return repository.confirm_edit_proposal(operation.workspace_id, operation.task_id, operation.proposal_id,
        operation=operation, result_version=change.version, montage_plan_id=change.montage_plan_id,
        montage_payload=change.montage_payload, subtitle_tracks=change.subtitle_tracks,
        cover_asset=change.cover_asset, narration_segments=change.narration_segments,
        narration_artifact=change.narration_artifact, connection=connection, verify_sources=verify_sources)


def commit_direct_change(repository, command, intent, proposal, change, *, verify_sources, connection=None):
    return repository.confirm_direct_edit(command, intent, proposal, change,
        verify_sources=verify_sources, connection=connection)
