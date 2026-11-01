"""One prepared version change and one repository commit for every edit family."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from uuid import uuid4
from contextlib import nullcontext
from pathlib import Path
import json
import sqlite3
from typing import TYPE_CHECKING
from app.persistence_json import _json_object, _json_payload

if TYPE_CHECKING:
    from app.production import NarrationArtifactRecord

from app.creation.contracts import CreationTask, CoverAsset, EditIntent, EditOperation, EditProposal, NarrationSegment, SubtitleTrack, TimelineVersion
from app.models import utc_now


def changed_stage_sections(snapshot, montage, before_tracks, after_tracks):
    """Identify published section videos whose actual render inputs changed."""
    from app.creation.section_timeline import section_timeline_dependencies
    before = snapshot.get('montage')
    if not before or not montage:
        return []

    def subtitles(plan, section_id, tracks):
        cues = {cue['id'] for cue in plan['cues'] if cue['source_section_id'] == section_id}
        clips = [clip for clip in plan['clips'] if clip['cue_id'] in cues]
        if not clips:
            return []
        start = min(clip['timeline_start_seconds'] for clip in clips)
        end = max(clip['timeline_end_seconds'] for clip in clips)
        result = []
        for track in tracks:
            relevant = [replace(cue, start_seconds=cue.start_seconds-start, end_seconds=cue.end_seconds-start)
                for cue in track.cues if cue.source_cue_id in cues or cue.start_seconds < end and cue.end_seconds > start]
            if relevant:
                result.append({'language': track.language, 'generation_method': track.generation_method,
                    'source_language': track.source_language, 'style': track.style,
                    'cues': [asdict(cue) for cue in relevant]})
        return sorted(result, key=lambda item: (item['language'], item['generation_method'], item['source_language'] or ''))

    changed = []
    for section_id in snapshot.get('stage_segments', {}):
        try:
            different = (before.get('aspect_ratio') != montage.get('aspect_ratio')
                or section_timeline_dependencies(before, section_id) != section_timeline_dependencies(montage, section_id))
        except ValueError:
            # A section split across noncontiguous spans cannot reuse its old contiguous video.
            different = True
        if before_tracks is not None:
            different = different or subtitles(before, section_id, before_tracks) != subtitles(montage, section_id, after_tracks)
        if different:
            changed.append(section_id)
    return changed


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
    narration_replacement: NarrationSegment | None = None


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
                       absent_cover_value=False, source_subtitle_tracks=None,
                       narration_replacement=None, narration_snapshot=None):
    """Compute the whole immutable change before acquiring a SQLite write lock."""
    if narration_replacement is not None:
        if (operation_type != 'RegenerateNarrationSegment' or narration_segments is not None
                or narration_snapshot is None):
            raise ValueError('单段配音替换需要原编辑及真实完整快照')
    elif narration_snapshot is not None:
        raise ValueError('配音快照缺少原单段替换事实')
    now = utc_now()
    operation = EditOperation(id=f"operation_{uuid4().hex}", workspace_id=proposal.workspace_id,
        task_id=proposal.task_id, proposal_id=proposal.id, operation_type=operation_type,
        payload=operation_payload, base_version_id=base_version.id, result_version_id=result_version_id,
        status="applied", created_at=now, applied_at=now)
    snapshot = dict(base_version.snapshot if snapshot_source is None else snapshot_source)
    invalidations = dict(snapshot.get('stage_segment_invalidations', {}))
    for section_id in changed_stage_sections(snapshot, montage_payload or snapshot.get('montage'), source_subtitle_tracks, subtitle_tracks):
        invalidations[section_id] = {'publication_id': snapshot['stage_segments'][section_id],
            'operation_id': operation.id, 'reason': 'section_render_inputs_changed'}
    if operation_type in {'UndoTimelineVersion', 'RedoTimelineVersion'}:
        for section_id, value in operation_payload.get('stage_adoption_restore', {}).items():
            if value is None:
                invalidations.pop(section_id, None)
            elif value['publication_id'] == snapshot.get('stage_segments',{}).get(section_id):
                invalidations[section_id] = value
    if invalidations or 'stage_segment_invalidations' in snapshot:
        snapshot['stage_segment_invalidations'] = invalidations
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
    if narration_replacement is not None:
        snapshot["narration_segments"] = narration_snapshot
    if narration_artifact is not None:
        snapshot["narration_cache_key"] = narration_artifact.cache_key
    snapshot.update(invalidated_refs=proposal.invalidated_refs, edit_operation_id=operation.id)
    version = TimelineVersion(id=result_version_id, workspace_id=proposal.workspace_id, task_id=proposal.task_id,
        parent_version_id=base_version.id, sequence=base_version.sequence+1, snapshot=snapshot,
        created_by_operation_id=operation.id, created_at=now)
    return EditChangeSet(operation, version, montage_plan_id, montage_payload,
        subtitle_tracks, cover_asset, narration_segments, narration_artifact, narration_replacement)


def commit_change_set(repository, change: EditChangeSet, *, connection=None, verify_sources=None):
    """The repository owns the CAS and all cross-track writes, including grant checkpoints."""
    operation = change.operation
    return repository.edit_adoption.confirm(operation.workspace_id, operation.task_id, operation.proposal_id,
        operation=operation, result_version=change.version, montage_plan_id=change.montage_plan_id,
        montage_payload=change.montage_payload, subtitle_tracks=change.subtitle_tracks,
        cover_asset=change.cover_asset, narration_segments=change.narration_segments,
        narration_artifact=change.narration_artifact, narration_replacement=change.narration_replacement,
        connection=connection, verify_sources=verify_sources)


def commit_direct_change(repository, command, intent, proposal, change, *, verify_sources, connection=None):
    return repository.edit_adoption.confirm_direct(command, intent, proposal, change,
        verify_sources=verify_sources, connection=connection)


class EditAdoptionTransaction:
    """One CAS and one connection for every fact in a prepared edit adoption."""
    def __init__(self, connection_factory, *,
                 require_task, decode_proposal, create_intent, create_proposal, version_conflict, read_task,
                 read_proposal, read_operation, read_version,
                 read_narration_segment, iter_narration_edit_segments, replace_adopted_narration_segment,
                 upsert_narration_segments, validate_operation, validate_version,
                 validate_track, validate_cover):
        self._connect = connection_factory
        self.require_task = require_task
        self.decode_proposal = decode_proposal
        self.create_intent = create_intent
        self.create_proposal = create_proposal
        self.version_conflict = version_conflict
        self.read_task = read_task
        self.read_proposal = read_proposal
        self.read_operation = read_operation
        self.read_version = read_version
        self.read_narration_segment = read_narration_segment
        self.iter_narration_edit_segments = iter_narration_edit_segments
        self.replace_adopted_narration_segment = replace_adopted_narration_segment
        self.upsert_narration_segments = upsert_narration_segments
        self.validate_operation = validate_operation
        self.validate_version = validate_version
        self.validate_track = validate_track
        self.validate_cover = validate_cover

    def confirm_direct(self, command, intent: EditIntent, proposal: EditProposal, change, *, verify_sources, connection=None):
        """Save lineage and full change in the caller transaction or one owned CAS."""
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.confirm_direct(command, intent, proposal, change,
                    verify_sources=verify_sources, connection=conn)
        conn = connection
        task = self.require_task(conn, command.workspace_id, command.task_id)
        if task.current_timeline_version_id != command.base_version_id:
            raise self.version_conflict('当前时间线已变化，未保存这项修改')
        member = command.actor_identity.get('user_id') or command.actor_identity['member_id']
        if task.owner_user_id and task.owner_user_id != member and (not command.actor_identity.get('local_diagnostic')):
            raise PermissionError('当前成员不能修改此任务')
        if (intent.workspace_id, intent.task_id, proposal.intent_id, change.operation.proposal_id) != (command.workspace_id, command.task_id, intent.id, proposal.id):
            raise ValueError('直接编辑命令与变更谱系不一致')
        verify_sources(command, conn)
        self.create_intent(intent, connection=conn, source=None)
        self.create_proposal(proposal, connection=conn)
        result = self.confirm(command.workspace_id, command.task_id, proposal.id, operation=change.operation, result_version=change.version, montage_plan_id=change.montage_plan_id, montage_payload=change.montage_payload, subtitle_tracks=change.subtitle_tracks, cover_asset=change.cover_asset, narration_segments=change.narration_segments, narration_replacement=change.narration_replacement, narration_artifact=change.narration_artifact, connection=conn)
        return result

    def confirm(
        self,
        workspace_id: str,
        task_id: str,
        proposal_id: str,
        *,
        operation: EditOperation,
        result_version: TimelineVersion,
        montage_plan_id: str | None = None,
        montage_payload: dict[str, object] | None = None,
        narration_segments: list[NarrationSegment] | None = None,
        narration_replacement: NarrationSegment | None = None,
        narration_artifact: NarrationArtifactRecord | None = None,
        subtitle_tracks: list[SubtitleTrack] | None = None,
        cover_asset: CoverAsset | None = None,
        connection: sqlite3.Connection | None = None,
        verify_sources=None,
    ) -> tuple[EditProposal, EditOperation, TimelineVersion, CreationTask]:
        """Confirm applied facts; a supplied permit transaction owns the commit."""
        if narration_replacement is not None and (
                operation.operation_type != 'RegenerateNarrationSegment' or narration_segments is not None):
            raise ValueError('单段配音替换仅属于原局部配音编辑')
        self.validate_operation(operation)
        self.validate_version(result_version)
        if operation.status != "applied":
            raise ValueError("原子确认只能写入已应用编辑操作")
        if (
            operation.workspace_id != workspace_id
            or operation.task_id != task_id
            or operation.proposal_id != proposal_id
            or result_version.workspace_id != workspace_id
            or result_version.task_id != task_id
            or operation.result_version_id != result_version.id
            or result_version.parent_version_id != operation.base_version_id
            or result_version.created_by_operation_id != operation.id
        ):
            raise ValueError("提案、编辑操作与结果时间线版本谱系不一致")
        if (montage_plan_id is None) != (montage_payload is None):
            raise ValueError("混剪计划 ID 与 payload 必须同时提供")
        if operation.operation_type == "RegenerateNarrationSegment" and (
            (narration_segments is None and narration_replacement is None) or montage_payload is None
        ):
            raise ValueError("声音或字幕编辑确认必须同时保存完整配音分段与 montage/cue 事实")
        if operation.operation_type == "UpdateSubtitleCue" and not (
            subtitle_tracks or (narration_segments is not None and montage_payload is not None)
        ):
            raise ValueError("字幕编辑确认必须保存完整字幕轨或迁移前 montage/cue 事实")
        if operation.operation_type in {"ConfigureSubtitleTrack", "ReplaceSubtitleText", "AlignSubtitleTiming"} and not subtitle_tracks:
            raise ValueError("字幕轨配置确认必须保存字幕轨事实")
        if operation.operation_type == "GenerateCoverFromFrame" and cover_asset is None:
            raise ValueError("封面确认必须保存封面事实")
        for track in subtitle_tracks or []:
            self.validate_track(track)
            if (
                track.workspace_id != workspace_id
                or track.task_id != task_id
                or track.timeline_version_id != result_version.id
            ):
                raise ValueError("字幕轨与结果时间线版本谱系不一致")
        if cover_asset is not None:
            self.validate_cover(cover_asset)
            if (
                cover_asset.workspace_id != workspace_id
                or cover_asset.task_id != task_id
                or cover_asset.timeline_version_id != result_version.id
            ):
                raise ValueError("封面与结果时间线版本谱系不一致")
        if narration_artifact is not None:
            if narration_segments is None and narration_replacement is None:
                raise ValueError("整轨配音产物必须与完整分段事实同时保存")
            if (
                narration_artifact.workspace_id != workspace_id
                or narration_artifact.task_id != task_id
                or not Path(narration_artifact.audio_path).is_file()
                or Path(narration_artifact.audio_path).stat().st_size == 0
            ):
                raise ValueError("整轨配音产物与当前任务不同源或音频无效")
        if montage_payload is not None:
            allowed_montage_statuses = (
                {"waiting_for_user", "ready", "rendered"}
                if operation.operation_type in {"UndoTimelineVersion", "RedoTimelineVersion"}
                else {"waiting_for_user", "ready"}
            )
            if (
                str(montage_payload.get("task_id", "")) != task_id
                or str(montage_payload.get("status", ""))
                not in allowed_montage_statuses
            ):
                raise ValueError("确认编辑后的混剪计划状态与操作类型不一致")
            _json_object(montage_payload)

        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            task = self.require_task(conn, workspace_id, task_id)
            proposal_row = conn.execute(
                """
                SELECT * FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, proposal_id),
            ).fetchone()
            if proposal_row is None:
                raise KeyError(proposal_id)
            proposal = self.decode_proposal(proposal_row)
            if proposal.status != "proposed":
                raise ValueError("只能原子确认 proposed 编辑提案")
            base_row = conn.execute(
                """
                SELECT sequence FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, operation.base_version_id),
            ).fetchone()
            if base_row is None:
                raise ValueError("编辑操作基础版本不属于当前工作区与任务")
            if task.current_timeline_version_id != operation.base_version_id:
                raise ValueError("编辑操作基础版本已不是当前任务版本")
            if verify_sources is not None:
                verify_sources(conn)
            if result_version.sequence != int(base_row["sequence"]) + 1:
                raise ValueError("结果时间线版本序号必须连续递增")
            conflicts = (
                conn.execute(
                    "SELECT 1 FROM edit_operations WHERE id = ? OR (workspace_id = ? AND task_id = ? AND proposal_id = ?)",
                    (operation.id, workspace_id, task_id, proposal_id),
                ).fetchone(),
                conn.execute(
                    "SELECT 1 FROM timeline_versions WHERE id = ? OR (workspace_id = ? AND task_id = ? AND sequence = ?)",
                    (
                        result_version.id,
                        workspace_id,
                        task_id,
                        result_version.sequence,
                    ),
                ).fetchone(),
            )
            if any(row is not None for row in conflicts):
                raise ValueError("编辑操作或结果版本已存在，不能重复确认")
            if montage_payload is not None:
                montage_row = conn.execute(
                    """
                    SELECT id FROM creation_task_montage_plans
                    WHERE workspace_id = ? AND task_id = ?
                    """,
                    (workspace_id, task_id),
                ).fetchone()
                if montage_row is None or str(montage_row["id"]) != montage_plan_id:
                    raise ValueError("当前任务不存在同源混剪计划")
            if narration_replacement is not None:
                if (narration_artifact is None or narration_artifact.script_id != task.confirmed_script_id
                        or narration_replacement.id != proposal.operation['payload']['narration_segment_id']):
                    raise ValueError('配音替换或整轨产物不属于原提案')
                previous = self.read_narration_segment(workspace_id, task_id, narration_replacement.id, connection=conn)
                if previous.audio_reference != proposal.operation['payload']['expected_audio_reference']:
                    raise ValueError('原配音来源已变化，不能覆盖')
                rows = result_version.snapshot.get('narration_segments')
                if not isinstance(rows, list):
                    raise ValueError('局部配音必须保留真实完整最终快照')
                count = 0
                for count, segment in enumerate(self.iter_narration_edit_segments(
                        task, narration_replacement, connection=conn), 1):
                    if count > len(rows) or json.loads(_json_payload(segment)) != json.loads(_json_object(rows[count-1])):
                        raise ValueError('局部配音最终快照与原事务事实不符')
                if count != len(rows):
                    raise ValueError('局部配音最终快照存在多余分段')
                self.replace_adopted_narration_segment(task, narration_replacement, connection=conn)
            if narration_segments is not None:
                script_ids = {segment.script_id for segment in narration_segments}
                if len(script_ids) != 1:
                    raise ValueError("原子确认只能保存当前已确认文案的分段")
                narration_script_id = next(iter(script_ids))
                self.upsert_narration_segments(
                    conn,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    script_id=narration_script_id,
                    segments=narration_segments,
                )
                if (
                    narration_artifact is not None
                    and narration_artifact.script_id != narration_script_id
                ):
                    raise ValueError("整轨配音产物与分段文案谱系不一致")

            now = utc_now()
            conn.execute(
                """
                INSERT INTO edit_operations
                    (id, workspace_id, task_id, proposal_id, operation_type,
                     payload_json, base_version_id, result_version_id, status,
                     failure_reason, created_at, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.id,
                    workspace_id,
                    task_id,
                    proposal_id,
                    operation.operation_type,
                    _json_object(operation.payload),
                    operation.base_version_id,
                    operation.result_version_id,
                    operation.status,
                    operation.failure_reason,
                    operation.created_at.isoformat(),
                    operation.applied_at.isoformat() if operation.applied_at else None,
                ),
            )
            conn.execute(
                """
                INSERT INTO timeline_versions
                    (id, workspace_id, task_id, parent_version_id, sequence,
                     snapshot_json, created_by_operation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_version.id,
                    workspace_id,
                    task_id,
                    result_version.parent_version_id,
                    result_version.sequence,
                    _json_object(result_version.snapshot),
                    result_version.created_by_operation_id,
                    result_version.created_at.isoformat(),
                ),
            )
            for track in subtitle_tracks or []:
                conn.execute(
                    """
                    INSERT INTO subtitle_tracks
                        (id, workspace_id, task_id, timeline_version_id, language,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        track.id,
                        workspace_id,
                        task_id,
                        result_version.id,
                        track.language,
                        _json_payload(track),
                        track.created_at.isoformat(),
                        track.updated_at.isoformat(),
                    ),
                )
            if cover_asset is not None:
                conn.execute(
                    """
                    INSERT INTO cover_assets
                        (id, workspace_id, task_id, timeline_version_id,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cover_asset.id,
                        workspace_id,
                        task_id,
                        result_version.id,
                        _json_payload(cover_asset),
                        cover_asset.created_at.isoformat(),
                        cover_asset.updated_at.isoformat(),
                    ),
                )
            conn.execute(
                """
                UPDATE edit_proposals SET status = 'confirmed', updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'proposed'
                """,
                (now.isoformat(), workspace_id, task_id, proposal_id),
            )
            if montage_payload is not None:
                saved_montage = {**montage_payload, "updated_at": now.isoformat()}
                conn.execute(
                    """
                    UPDATE creation_task_montage_plans
                    SET payload = ?, updated_at = ?
                    WHERE workspace_id = ? AND task_id = ? AND id = ?
                    """,
                    (
                        json.dumps(saved_montage, ensure_ascii=False),
                        now.isoformat(),
                        workspace_id,
                        task_id,
                        montage_plan_id,
                    ),
                )
                task.montage_plan_id = montage_plan_id
                restored_rendered_output = bool(
                    operation.operation_type in {"UndoTimelineVersion", "RedoTimelineVersion"}
                    and montage_payload.get("status") == "rendered"
                    and montage_payload.get("output_path")
                )
                task.output_ready = restored_rendered_output
                task.stage = "review" if restored_rendered_output else "montage"
                task.stage_status = "waiting_for_user"
                task.status = "waiting_for_user"
                task.progress = 90 if restored_rendered_output else 75
                task.recovery_point = "review" if restored_rendered_output else "montage"
                task.stage_reason = None
                task.stage_impact = None
                task.stage_repair_action = None
                task.stage_retryable = None
            if narration_artifact is not None:
                conn.execute(
                    """
                    INSERT INTO narration_artifacts
                        (id, workspace_id, cache_key, payload, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(workspace_id, cache_key) DO UPDATE SET
                        id = excluded.id,
                        payload = excluded.payload,
                        created_at = excluded.created_at
                    """,
                    (
                        narration_artifact.id,
                        narration_artifact.workspace_id,
                        narration_artifact.cache_key,
                        _json_payload(narration_artifact),
                        narration_artifact.created_at.isoformat(),
                    ),
                )
                task.narration_cache_key = narration_artifact.cache_key
            if narration_segments is not None and any(
                segment.status in {"pending", "generating"}
                for segment in narration_segments
            ):
                task.stage = "narration"
                task.stage_status = (
                    "running"
                    if any(
                        segment.status == "generating"
                        for segment in narration_segments
                    )
                    else "not_started"
                )
                task.status = "running"
                task.recovery_point = "narration"
            task.current_timeline_version_id = result_version.id
            task.updated_at = now
            conn.execute(
                """
                UPDATE creation_tasks SET payload = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                """,
                (
                    _json_payload(task),
                    now.isoformat(),
                    workspace_id,
                    task_id,
                ),
            )

        if connection is not None:
            return replace(proposal, status="confirmed", updated_at=now), operation, result_version, task
        return (
            self.read_proposal(workspace_id, proposal_id),
            self.read_operation(workspace_id, operation.id),
            self.read_version(workspace_id, result_version.id),
            self.read_task(workspace_id, task_id),
        )
