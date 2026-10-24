"""Creation render publication, recovery and current/historical output validity."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.creation.duration_requirement import DurationRequirement, DurationConflict
from app.canvas import canvas_size
from app.creation.contracts import ArtifactRecord, DependencyEdge, JobRun, TaskEvent, TimelineVersion, TransformRun
from app.creation.editing.metadata_use_case import InvalidMontage
from app.creation.transactions import clone_subtitle_track as _clone_subtitle_track, clone_cover_asset as _clone_cover_asset
from app.creation_execution import CpuRenderGrantContext
from app.media.picture_preview import PicturePreviewRejected
from app.models import utc_now
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload, _validate_rendered_media
from app.production import CreationTask, MediaAuthorityError
from app.task_runtime import JobFailure, JobResult

CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION = "creation-task-montage-render/v21"


class RenderRejected(Exception):
    def __init__(self, *, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(f'{status_code}: {detail}')


def stage_failure(*, status_code, error, reason, impact, repair_action, retryable):
    return RenderRejected(status_code=status_code, detail={
        'error': error, 'reason': reason, 'impact': impact,
        'repair_action': repair_action, 'retryable': retryable,
    })

def render_input_payload(
    plan, *, subtitles_visible: bool, subtitle_tracks: list[dict[str, object]],
    timeline_version_id: str | None = None, bind_timeline_version: bool = False,
    portrait_runtime_fingerprint: str | None = None,
) -> dict[str, object]:
    payload = creation_task_montage_plan_to_payload(plan)
    for transient_key in ("status", "message", "output_path", "subtitle_path", "updated_at"):
        payload.pop(transient_key, None)
    payload["render_contract_version"] = CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION
    if bind_timeline_version:
        payload["timeline_version_id"] = timeline_version_id
    payload["subtitles_visible"] = subtitles_visible
    payload["subtitle_tracks"] = subtitle_tracks
    if portrait_runtime_fingerprint is not None:
        payload["portrait_runtime_fingerprint"] = portrait_runtime_fingerprint
    return payload


def render_input_refs(task: CreationTask, plan) -> list[str]:
    return list(dict.fromkeys([
        f"montage-plan://{task.id}/{plan.id}",
        plan.narration_audio_path,
        *(clip.source_path for clip in plan.clips),
        *((clip.source_ref for track in plan.audio_timeline.tracks for clip in track.clips) if plan.audio_timeline else ()),
        *(f"person-render://{clip.person_render_id}" for clip in plan.clips if clip.person_render_id),
        *(item["source_ref"] for item in plan.graphic_overlays if item.get("kind") == "image"),
    ]))


def render_input_digest_for(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class RenderStageUseCase:
    def __init__(self, repository, metadata, person_preview, video_job_runner, settings):
        self.repository = repository
        self.metadata = metadata
        self.person_preview = person_preview
        self.video_job_runner = video_job_runner
        self.settings = settings

    def render(
        self,
        workspace_id: str, task_id: str, *, render_context: CpuRenderGrantContext | None = None,
    ) -> CreationTaskMontagePlan:
        task = self.task_or_missing(workspace_id, task_id)
        try:
            plan = creation_task_montage_plan_from_payload(
                self.repository.get_creation_task_montage_plan(workspace_id, task_id)
            )
        except KeyError as exc:
            raise RenderRejected(status_code=404, detail="creation task montage plan not found") from exc
        requirement = DurationRequirement.from_plan(self.repository.get_creation_plan(workspace_id, task.plan_id))
        try:
            requirement.require(plan.duration_seconds, 30)
        except DurationConflict as exc:
            raise RenderRejected(status_code=409, detail=str(exc)) from exc
        if any(clip.needs_confirmation for clip in plan.clips) or plan.status not in {"ready", "rendering", "rendered", "failed"}:
            raise RenderRejected(status_code=409, detail="仍有镜头等待确认，请先检查并确认这些镜头。")
        if not plan.narration_audio_path:
            raise RenderRejected(status_code=409, detail="creation task narration missing")
        if render_context is not None:
            self.graphic_source_paths(workspace_id, task_id, plan.graphic_overlays, render_context)
        if render_context is not None and any(
            clip.visual_effects.get("beauty_strength") or clip.visual_effects.get("skin_exposure")
            for clip in plan.clips
        ):
            raise RenderRejected(status_code=409, detail="本机人像效果尚无受许可执行路径，请先移除该效果")
        try:
            self.metadata.picture_renderer.validate_material_use(workspace_id,plan)
        except ValueError as exc:
            raise RenderRejected(status_code=409,detail=str(exc)) from exc
        active_subtitle_tracks = self.current_subtitle_tracks(workspace_id, task, montage=plan)
        active_version = (
            self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
            if task.current_timeline_version_id
            else None
        )
        explicit_subtitles = active_version is not None and "subtitle_track_ids" in active_version.snapshot
        subtitles_visible = (any(track.status == "valid" and track.style.get("enabled", True) for track in active_subtitle_tracks)
            if active_subtitle_tracks or explicit_subtitles else bool(plan.cues))
        active_cover = (
            self.metadata.cover_asset_for_version(workspace_id, task.id, active_version)
            if active_version is not None
            else None
        )
        portrait_runtime_fingerprint = None
        if any(clip.visual_effects.get("beauty_strength") or clip.visual_effects.get("skin_exposure") for clip in plan.clips):
            try:
                portrait_runtime_fingerprint = self.metadata.picture_renderer.portrait_processor(workspace_id).fingerprint()
            except ValueError as exc:
                raise RenderRejected(status_code=422,detail=str(exc)) from exc
        subtitle_tracks = [
            {
                "language": track.language,
                "style": track.style,
                "cues": [asdict(cue) for cue in track.cues],
            }
            for track in active_subtitle_tracks
            if track.status == "valid"
        ]
        plan_input = render_input_payload(
            plan,
            subtitles_visible=subtitles_visible,
            subtitle_tracks=subtitle_tracks,
            timeline_version_id=task.current_timeline_version_id,
            bind_timeline_version=render_context is not None,
            portrait_runtime_fingerprint=portrait_runtime_fingerprint,
        )
        input_refs = render_input_refs(task, plan)
        input_digest = render_input_digest_for(plan_input)
        if (render_context is not None and active_version is not None
                and active_version.snapshot.get("creation_render_request_id") == render_context.grant.request_id):
            # The published version owns output subtitle tracks. They were not an
            # input to this render, so bind recovery to the input captured before
            # publication instead of hashing those newly generated tracks.
            snapshot = active_version.snapshot
            original_tracks = snapshot.get("render_input_subtitle_tracks")
            original_visible = snapshot.get("render_input_subtitles_visible")
            original_digest = snapshot.get("render_input_digest")
            if (not isinstance(original_tracks, list) or not isinstance(original_visible, bool)
                    or "render_input_timeline_version_id" not in snapshot
                    or active_version.parent_version_id != snapshot["render_input_timeline_version_id"]):
                raise ValueError("成片恢复缺少原始字幕输入")
            plan_input["timeline_version_id"] = snapshot["render_input_timeline_version_id"]
            plan_input["subtitle_tracks"] = original_tracks
            plan_input["subtitles_visible"] = original_visible
            input_digest = render_input_digest_for(plan_input)
            transform_id = snapshot.get("transform_run_id")
            if not isinstance(transform_id, str) or not transform_id.startswith("transform_job_"):
                raise ValueError("成片恢复缺少内层渲染记录")
            inner_id = transform_id.removeprefix("transform_")
            inner_job = self.repository.get_job_run(workspace_id, inner_id)
            transform = self.repository.get_transform_run(workspace_id, transform_id)
            output_refs = [plan.output_path, plan.subtitle_path]
            if (input_digest != original_digest or inner_job.task_id != task.id
                    or inner_job.kind != "creation_task_montage_render"
                    or inner_job.status != "succeeded" or inner_job.input_digest != input_digest
                    or inner_job.output_refs != output_refs
                    or transform.task_id != task.id or transform.job_id != inner_id
                    or transform.status != "succeeded" or transform.input_digest != input_digest
                    or transform.output_refs != output_refs
                    or plan.status != "rendered" or not all(isinstance(ref, str) and ref for ref in output_refs)
                    or snapshot.get("output_refs") != output_refs):
                raise ValueError("成片恢复输入或已发布产物不匹配")
            render_context.scheduler.assert_grant(render_context.grant)
            subtitle_record = self.repository.get_artifact_record(workspace_id, f"artifact_{inner_id}_subtitles")
            published_subtitle = self.metadata.artifact_store.describe(plan.subtitle_path)
            if (subtitle_record.producer_job_id != inner_id or subtitle_record.logical_ref != plan.subtitle_path
                    or subtitle_record.sha256 != published_subtitle.sha256
                    or subtitle_record.size_bytes != published_subtitle.size_bytes):
                raise ValueError("成片恢复字幕产物校验失败")
            return plan
        base_idempotency_key = f"montage-render:{task.id}:{input_digest}"
        matching_jobs = [
            existing
            for existing in self.repository.list_job_runs(workspace_id, task.id)
            if existing.kind == "creation_task_montage_render"
            and existing.input_digest == input_digest
        ]
        latest_matching_job = matching_jobs[-1] if matching_jobs else None
        if latest_matching_job is not None and latest_matching_job.status in {
            "queued",
            "running",
        }:
            raise RenderRejected(status_code=409, detail="montage render is already running")
        if (
            plan.status == "rendering"
            and (latest_matching_job is None or latest_matching_job.status != "interrupted")
        ):
            raise RenderRejected(status_code=409, detail="montage render is not resumable")
        if (render_context is None and latest_matching_job is not None
                and latest_matching_job.status == "succeeded"):
            reusable_records = [
                record
                for record in self.repository.list_artifact_records(workspace_id, task.id)
                if record.producer_job_id == latest_matching_job.id
                and record.logical_ref in latest_matching_job.output_refs
            ]
            reusable_by_kind = {record.kind: record for record in reusable_records}
            reusable_video = reusable_by_kind.get("rendered_video")
            reusable_subtitles = reusable_by_kind.get("subtitle_track")
            if reusable_video is not None and reusable_subtitles is not None:
                try:
                    self.metadata.artifact_store.describe(reusable_video.logical_ref)
                    self.metadata.artifact_store.describe(reusable_subtitles.logical_ref)
                except FileNotFoundError:
                    pass
                else:
                    plan.status = "rendered"
                    plan.output_path = reusable_video.logical_ref
                    plan.subtitle_path = reusable_subtitles.logical_ref
                    plan.message = "已复用输入一致且通过校验的成片，等待检查。"
                    rendered_payload = creation_task_montage_plan_to_payload(plan)
                    self.repository.save_creation_task_montage_plan(
                        workspace_id, task.id, plan.id, rendered_payload
                    )
                    versions = self.repository.list_timeline_versions(workspace_id, task.id)
                    current_task = self.repository.get_creation_task(workspace_id, task.id)
                    version_id = f"timeline_{uuid4().hex}"
                    inherited_cover = (
                        _clone_cover_asset(active_cover, timeline_version_id=version_id)
                        if active_cover is not None else None
                    )
                    version = self.repository.create_timeline_version(
                        TimelineVersion(
                            id=version_id,
                            workspace_id=workspace_id,
                            task_id=task.id,
                            parent_version_id=current_task.current_timeline_version_id,
                            sequence=(versions[-1].sequence + 1) if versions else 1,
                            snapshot={
                                "montage": rendered_payload,
                                **self.metadata.current_narration_snapshot(workspace_id, current_task),
                            **({"subtitle_track_ids": []} if explicit_subtitles and not active_subtitle_tracks else {}),
                                "output_refs": [
                                    reusable_video.logical_ref,
                                    reusable_subtitles.logical_ref,
                                ],
                                "reused_job_id": latest_matching_job.id,
                                **({"cover_asset_id": inherited_cover.id} if inherited_cover else {}),
                            },
                            created_by_operation_id=None,
                        )
                    )
                    if inherited_cover is not None:
                        self.repository.create_cover_asset(inherited_cover)
                    if not self.repository.list_subtitle_tracks(
                        workspace_id, task.id, timeline_version_id=version.id
                    ):
                        if active_subtitle_tracks:
                            for active_track in active_subtitle_tracks:
                                self.repository.create_subtitle_track(
                                    _clone_subtitle_track(
                                        active_track,
                                        timeline_version_id=version.id,
                                        artifact_ref=(
                                            reusable_subtitles.logical_ref
                                            if active_track.source_language is None
                                            else active_track.artifact_ref
                                        ),
                                        transform_id=(
                                            f"transform_{latest_matching_job.id}"
                                            if active_track.source_language is None
                                            else active_track.transform_id
                                        ),
                                    )
                                )
                        elif not explicit_subtitles:
                            initial_track = self.subtitle_track_from_montage(
                                workspace_id, task, version, plan
                            )
                            if initial_track is not None:
                                self.repository.create_subtitle_track(initial_track)
                    self.repository.append_task_event(
                        TaskEvent(
                            id=f"event_{version.id}_render_reused",
                            workspace_id=workspace_id,
                            task_id=task.id,
                            job_id=latest_matching_job.id,
                            event_type="render_reused",
                            payload={
                                "timeline_version_id": version.id,
                                "output_refs": [
                                    reusable_video.logical_ref,
                                    reusable_subtitles.logical_ref,
                                ],
                            },
                        )
                    )
                    return plan
        idempotency_key = (
            latest_matching_job.idempotency_key
            if latest_matching_job is not None
            and latest_matching_job.status == "interrupted"
            else f"{base_idempotency_key}:retry:{len(matching_jobs)}"
            if matching_jobs
            else base_idempotency_key
        )
        job = JobRun(
            id=f"job_{uuid4().hex}",
            workspace_id=workspace_id,
            task_id=task.id,
            kind="creation_task_montage_render",
            idempotency_key=idempotency_key,
            input_digest=input_digest,
        )

        def render_job(context) -> JobResult:
            running_job = self.repository.get_job_run(
                context.workspace_id, context.job_id
            )
            try:
                video_ref = f"artifacts/{task.id}/{context.job_id}/preview.mp4"
                subtitle_ref = f"artifacts/{task.id}/{context.job_id}/subtitles.srt"
                try:
                    published_video = self.metadata.artifact_store.describe(video_ref)
                    published_subtitles = self.metadata.artifact_store.describe(subtitle_ref)
                    rendered = plan
                    rendered.status = "rendered"
                except FileNotFoundError:
                    narration_path = self.repository.resolve_authorized_narration_audio_reference(
                        workspace_id, plan.narration_audio_path or ""
                    )
                    rendering_payload = creation_task_montage_plan_to_payload(plan)
                    rendering_payload["status"] = "rendering"
                    self.repository.save_creation_task_montage_plan(
                        workspace_id, task.id, plan.id, rendering_payload,
                        **({"expected_current_version_id": plan_input["timeline_version_id"]}
                           if render_context is not None else {}),
                    )
                    plan.status = "ready"
                    staging_root = Path(self.settings.video_output_path).expanduser() / ".job-staging"
                    staging_root.mkdir(parents=True, exist_ok=True)
                    with TemporaryDirectory(prefix=f"{context.job_id}-", dir=staging_root) as staging:
                        rendering_plan = creation_task_montage_plan_from_payload(
                            creation_task_montage_plan_to_payload(plan)
                        )
                        rendered_variant = self.render_montage(
                            workspace_id,
                            rendering_plan,
                            narration_path,
                            job_staging_path=Path(staging),
                            subtitle_tracks=active_subtitle_tracks if active_subtitle_tracks or explicit_subtitles else None,
                            **({"render_context": render_context} if render_context is not None else {}),
                        )
                        rendered = plan
                        rendered.status = rendered_variant.status
                        staged_video = Path(staging) / "preview.mp4"
                        staged_subtitles = Path(staging) / "subtitles.srt"

                        def publish_or_reuse(logical_ref: str, staged_path: Path):
                            if render_context is not None:
                                render_context.check()
                            try:
                                return self.metadata.artifact_store.describe(logical_ref)
                            except FileNotFoundError:
                                return self.metadata.artifact_store.publish_file(logical_ref, staged_path)

                        published_video = publish_or_reuse(video_ref, staged_video)
                        published_subtitles = publish_or_reuse(
                            subtitle_ref, staged_subtitles
                        )

                for artifact_id, kind, published in (
                    (f"artifact_{context.job_id}_video", "rendered_video", published_video),
                    (
                        f"artifact_{context.job_id}_subtitles",
                        "subtitle_track",
                        published_subtitles,
                    ),
                ):
                    try:
                        self.repository.get_artifact_record(workspace_id, artifact_id)
                    except KeyError:
                        self.repository.create_artifact_record(
                            ArtifactRecord(
                                id=artifact_id,
                                workspace_id=workspace_id,
                                task_id=task.id,
                                kind=kind,
                                logical_ref=published.logical_ref,
                                sha256=published.sha256,
                                size_bytes=published.size_bytes,
                                producer_job_id=context.job_id,
                                input_refs=input_refs,
                            )
                        )

                output_refs = [video_ref, subtitle_ref]
                active_transform_id = f"transform_{context.job_id}"
                try:
                    transform = self.repository.get_transform_run(
                        workspace_id, active_transform_id
                    )
                except KeyError:
                    transform = self.repository.create_transform_run(
                        TransformRun(
                            id=active_transform_id,
                            workspace_id=workspace_id,
                            task_id=task.id,
                            job_id=context.job_id,
                            transform_type="montage_render",
                            input_digest=input_digest,
                            input_refs=input_refs,
                            output_refs=output_refs,
                            engine="ffmpeg+ffprobe",
                            parameters={
                                "aspect_ratio": plan.aspect_ratio,
                                "subtitle_burned_in": subtitles_visible,
                                "render_contract_version": CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION,
                            },
                            status="succeeded",
                            started_at=running_job.started_at or utc_now(),
                            finished_at=utc_now(),
                        )
                    )
                for upstream_ref in input_refs:
                    for downstream_ref in output_refs:
                        edge_digest = hashlib.sha256(
                            f"{upstream_ref}\n{downstream_ref}".encode("utf-8")
                        ).hexdigest()[:24]
                        edge_id = f"edge_{context.job_id}_{edge_digest}"
                        try:
                            self.repository.get_dependency_edge(workspace_id, edge_id)
                        except KeyError:
                            self.repository.create_dependency_edge(
                                DependencyEdge(
                                    id=edge_id,
                                    workspace_id=workspace_id,
                                    task_id=task.id,
                                    upstream_ref=upstream_ref,
                                    downstream_ref=downstream_ref,
                                    created_by_transform_id=transform.id,
                                )
                            )

                rendered.output_path = video_ref
                rendered.subtitle_path = subtitle_ref
                rendered.message = "带字幕的成片已生成，等待检查。"
                rendered_payload = creation_task_montage_plan_to_payload(rendered)
                if render_context is not None:
                    render_context.check()
                version_id = f"timeline_{context.job_id}"
                try:
                    version = self.repository.get_timeline_version(
                        workspace_id, version_id
                    )
                except KeyError:
                    versions = self.repository.list_timeline_versions(workspace_id, task.id)
                    current_task = self.repository.get_creation_task(workspace_id, task.id)
                    parent_version_id = current_task.current_timeline_version_id
                    if (render_context is not None
                            and parent_version_id != plan_input["timeline_version_id"]):
                        raise ValueError("渲染期间当前时间线已变化，请重新安排成片")
                    if parent_version_id is None and versions:
                        parent_version_id = versions[-1].id
                    version = TimelineVersion(
                        id=version_id,
                        workspace_id=workspace_id,
                        task_id=task.id,
                        parent_version_id=parent_version_id,
                        sequence=(versions[-1].sequence + 1) if versions else 1,
                        snapshot={
                            "montage": rendered_payload,
                            **self.metadata.current_narration_snapshot(workspace_id, current_task),
                            **({"subtitle_track_ids": []} if explicit_subtitles and not active_subtitle_tracks else {}),
                            "output_refs": output_refs,
                            "transform_run_id": transform.id,
                            **({
                                "creation_render_request_id": render_context.grant.request_id,
                                "render_input_digest": input_digest,
                                "render_input_timeline_version_id": plan_input["timeline_version_id"],
                                "render_input_subtitle_tracks": plan_input["subtitle_tracks"],
                                "render_input_subtitles_visible": subtitles_visible,
                            } if render_context is not None else {}),
                            **({"cover_asset_id": f"cover_{version_id}"} if active_cover else {}),
                        },
                        created_by_operation_id=None,
                    )
                self.repository.publish_creation_task_render(
                    workspace_id, task.id, rendered.id, rendered_payload, version,
                    **({"expected_current_version_id": plan_input["timeline_version_id"]}
                       if render_context is not None else {}),
                )
                event_id = f"event_{context.job_id}_render_succeeded"
                try:
                    self.repository.get_task_event(workspace_id, event_id)
                except KeyError:
                    self.repository.append_task_event(
                        TaskEvent(
                            id=event_id,
                            workspace_id=workspace_id,
                            task_id=task.id,
                            job_id=context.job_id,
                            event_type="render_succeeded",
                            payload={
                                "transform_run_id": transform.id,
                                "timeline_version_id": version.id,
                                "output_refs": output_refs,
                            },
                        )
                    )
                # The selected cover is an immutable image with its own source reference;
                # rendering a video must not silently discard or retarget that selection.
                if active_cover is not None and not self.repository.list_cover_assets(
                    workspace_id, task.id, timeline_version_id=version.id
                ):
                    self.repository.create_cover_asset(
                        replace(
                            _clone_cover_asset(active_cover, timeline_version_id=version.id),
                            id=version.snapshot["cover_asset_id"],
                        )
                    )
                persisted_tracks = self.repository.list_subtitle_tracks(
                    workspace_id, task.id, timeline_version_id=version.id
                )
                if not persisted_tracks:
                    if active_subtitle_tracks:
                        for active_track in active_subtitle_tracks:
                            self.repository.create_subtitle_track(
                                _clone_subtitle_track(
                                    active_track,
                                    timeline_version_id=version.id,
                                    artifact_ref=(
                                        subtitle_ref
                                        if active_track.source_language is None
                                        else active_track.artifact_ref
                                    ),
                                    transform_id=(
                                        transform.id
                                        if active_track.source_language is None
                                        else active_track.transform_id
                                    ),
                                )
                            )
                    elif not explicit_subtitles:
                        initial_track = self.subtitle_track_from_montage(
                            workspace_id, task, version, rendered
                        )
                        if initial_track is not None:
                            self.repository.create_subtitle_track(initial_track)
                context.report_progress(100)
                return JobResult(tuple(output_refs))
            except JobFailure:
                raise
            except MediaAuthorityError as exc:
                raise JobFailure(exc.reason, exc.impact, exc.repair_action) from exc
            except (OSError, ValueError) as exc:
                raise JobFailure(
                    str(exc) or type(exc).__name__,
                    "当前任务仍没有可播放的可信成片。",
                    "检查素材、旁白和 FFmpeg 后重新渲染。",
                ) from exc

        result = self.video_job_runner.run(job, render_job)
        if result.status != "succeeded":
            transform_id = f"transform_{result.id}"
            try:
                self.repository.get_transform_run(workspace_id, transform_id)
            except KeyError:
                self.repository.create_transform_run(
                    TransformRun(
                        id=transform_id,
                        workspace_id=workspace_id,
                        task_id=task.id,
                        job_id=result.id,
                        transform_type="montage_render",
                        input_digest=input_digest,
                        input_refs=input_refs,
                        output_refs=[],
                        engine="ffmpeg+ffprobe",
                        parameters={"aspect_ratio": plan.aspect_ratio, "subtitle_burned_in": subtitles_visible},
                        status="failed",
                        started_at=result.started_at or result.created_at,
                        finished_at=result.finished_at or utc_now(),
                        failure_reason=result.failure_reason,
                        remediation=result.repair_action,
                    )
                )
            plan.status = "failed"
            plan.output_path = None
            plan.subtitle_path = None
            plan.message = f"成片渲染失败：{result.failure_reason or '未知错误'}"
            self.repository.save_failed_creation_task_render_if_current(
                workspace_id, task.id, plan.id, creation_task_montage_plan_to_payload(plan),
                **({"expected_current_version_id": plan_input["timeline_version_id"]}
                   if render_context is not None else {}),
            )
            raise stage_failure(
                status_code=500,
                error="montage_render_failed",
                reason=result.failure_reason or "FFmpeg 没有生成通过校验的成片。",
                impact=result.impact or "当前任务仍没有可播放 MP4。",
                repair_action=result.repair_action or "检查输入后重新渲染。",
                retryable=result.status in {"failed", "interrupted"},
            )

        rendered = creation_task_montage_plan_from_payload(
            self.repository.get_creation_task_montage_plan(workspace_id, task.id)
        )
        self.record_usage(workspace_id, rendered)
        return rendered

    def valid(
        self,
        workspace_id: str, task_id: str, *, verify_media: bool = True,
        render_context: CpuRenderGrantContext | None = None,
        timeline_version: TimelineVersion | None = None,
    ) -> bool:
        try:
            task = self.repository.get_creation_task(workspace_id, task_id)
            if not task.current_timeline_version_id:
                return False
            version = timeline_version or self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id)
            plan = version.snapshot["montage"] if timeline_version is not None else self.repository.get_creation_task_montage_plan(workspace_id, task_id)
            ref = plan.get("output_path")
            if (plan.get("status") != "rendered" or not isinstance(ref, str)
                    or plan.get("script_id") != task.confirmed_script_id):
                return False
            if version.task_id != task_id or ref not in version.snapshot.get("output_refs", []):
                return False
            transform_id = version.snapshot.get("transform_run_id")
            if not isinstance(transform_id, str):
                return False
            transform = self.repository.get_transform_run(workspace_id, transform_id)
            if (transform.task_id != task_id or transform.status != "succeeded"
                    or ref not in transform.output_refs
                    or transform.output_refs != version.snapshot.get("output_refs")):
                return False
            render_input_digest = version.snapshot.get("render_input_digest")
            if render_input_digest is not None:
                input_version_id = version.snapshot.get("render_input_timeline_version_id")
                if (not isinstance(render_input_digest, str) or not render_input_digest
                        or not isinstance(input_version_id, str)
                        or version.parent_version_id != input_version_id
                        or not isinstance(version.snapshot.get("creation_render_request_id"), str)
                        or not version.snapshot.get("creation_render_request_id")
                        or not transform_id.startswith("transform_job_")
                        or version.id != f"timeline_{transform_id.removeprefix('transform_')}"
                        or not isinstance(version.snapshot.get("render_input_subtitles_visible"), bool)
                        or not isinstance(version.snapshot.get("render_input_subtitle_tracks"), list)
                        or transform.input_digest != render_input_digest):
                    return False
                input_version = self.repository.get_timeline_version(workspace_id, input_version_id)
                if input_version.task_id != task_id:
                    return False
            input_plan = creation_task_montage_plan_from_payload(plan)
            input_refs = None
            if timeline_version is not None:
                if not isinstance(render_input_digest, str) or not render_input_digest:
                    return False
                input_version_id = version.snapshot.get("render_input_timeline_version_id")
                if not isinstance(input_version_id, str) or version.parent_version_id != input_version_id:
                    return False
                input_version = self.repository.get_timeline_version(workspace_id, input_version_id)
                input_plan_payload = input_version.snapshot.get("montage")
                subtitle_tracks = version.snapshot.get("render_input_subtitle_tracks")
                subtitles_visible = version.snapshot.get("render_input_subtitles_visible")
                if (input_version.task_id != task_id or not isinstance(input_plan_payload, dict)
                        or not isinstance(subtitle_tracks, list) or not isinstance(subtitles_visible, bool)):
                    return False
                input_plan = creation_task_montage_plan_from_payload(input_plan_payload)
                if any(clip.visual_effects.get("beauty_strength") or clip.visual_effects.get("skin_exposure")
                       for clip in input_plan.clips):
                    # The fingerprint is produced by the licensed renderer and is not sealed separately.
                    return False
                input_payload = render_input_payload(
                    input_plan,
                    subtitles_visible=subtitles_visible,
                    subtitle_tracks=subtitle_tracks,
                    timeline_version_id=input_version_id,
                    bind_timeline_version=True,
                )
                expected_digest = render_input_digest_for(input_payload)
                input_refs = render_input_refs(task, input_plan)
                if expected_digest != render_input_digest:
                    return False
                person_clips = [clip for clip in input_plan.clips if clip.person_render_id]
                if person_clips:
                    narration_segments = self.metadata.narration_segments_from_snapshot(version.snapshot)
                    if narration_segments is None:
                        return False
                    segments_by_id = {segment.id: segment for segment in narration_segments}
                    cues_by_id = {cue.id: cue for cue in input_plan.cues}
                    person_transforms = self.repository.list_transform_runs(workspace_id, task_id)
                    for clip in person_clips:
                        cue = cues_by_id.get(clip.cue_id)
                        segment = segments_by_id.get(cue.narration_segment_id) if cue and cue.narration_segment_id else None
                        if segment is None:
                            return False
                        person_render = self.repository.get_person_render(workspace_id, task_id, clip.person_render_id)
                        if (person_render.status != "ready"
                                or person_render.narration_segment_id != segment.id
                                or not self.person_preview.render_matches_narration(person_render, segment, person_transforms)):
                            return False
            registered = next(
                (artifact for artifact in self.repository.list_artifact_records(workspace_id, task_id)
                 if artifact.logical_ref == ref and artifact.kind == "rendered_video"),
                None,
            )
            if registered is None or registered.producer_job_id != transform.job_id:
                return False
            job = self.repository.get_job_run(workspace_id, registered.producer_job_id)
            if (job.status != "succeeded" or job.task_id != task_id
                    or (render_input_digest is not None and job.kind != "creation_task_montage_render")
                    or job.output_refs != transform.output_refs
                    or job.input_digest != transform.input_digest):
                return False
            if render_input_digest is not None and job.input_digest != render_input_digest:
                return False
            if input_refs is not None and (
                transform.transform_type != "montage_render"
                or transform.input_refs != input_refs
                or registered.input_refs != input_refs
            ):
                return False
            path = self.metadata.artifact_store.resolve_file(ref)
            self.metadata.picture_renderer.validate_material_use(workspace_id, input_plan)
            if render_context is not None:
                render_context.scheduler.assert_grant(render_context.grant)
            published = self.metadata.artifact_store.describe(ref)
            if published.sha256 != registered.sha256 or published.size_bytes != registered.size_bytes:
                return False
            if not verify_media:
                return True
            media_options = (
                {"scheduler": render_context.scheduler, "grant": render_context.grant,
                 "cancel_event": render_context.cancel_event,
                 "deadline_monotonic": render_context.deadline_monotonic}
                if render_context is not None else {}
            )
            _validate_rendered_media(path, expected_size=canvas_size(str(plan["aspect_ratio"])),
                                     expected_duration_seconds=float(plan["duration_seconds"]),
                                     duration_requirement=DurationRequirement.from_plan(
                                         self.repository.get_creation_plan(workspace_id, task.plan_id)),
                                     **media_options)
            return True
        except (KeyError, FileNotFoundError, ValueError, OSError):
            return False

    def valid_with_grant(self, workspace_id: str, task_id: str, grant: CpuRenderGrantContext) -> bool:
        valid = self.valid(workspace_id, task_id, render_context=grant)
        if valid:
            plan = self.repository.get_creation_task_montage_plan(workspace_id, task_id)
            self.graphic_source_paths(workspace_id, task_id, plan.get("graphic_overlays", []), grant)
        return valid

    def record_usage(self, workspace_id: str, plan) -> None:
        for clip in plan.clips:
            if clip.person_render_id:
                continue
            self.repository.record_segment_usage(
                workspace_id,
                clip.asset_id,
                clip.segment_id,
                plan_id=plan.id,
                clip_id=clip.id,
            )

    def task_or_missing(self, workspace_id: str, task_id: str) -> CreationTask:
        try:
            return self.repository.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise RenderRejected(status_code=404, detail="creation task not found") from exc

    def current_subtitle_tracks(self, workspace_id, task, *, current_version=None, montage=None):
        try:
            return self.metadata.current_subtitle_tracks(workspace_id, task, current_version=current_version, montage=montage)
        except InvalidMontage as exc:
            raise RenderRejected(status_code=422, detail=str(exc)) from exc

    def subtitle_track_from_montage(self, workspace_id, task, version, montage):
        try:
            return self.metadata.subtitle_track_from_montage(workspace_id, task, version, montage)
        except InvalidMontage as exc:
            raise RenderRejected(status_code=422, detail=str(exc)) from exc

    def graphic_source_paths(self, workspace_id, task_id, overlays, context):
        try:
            return self.metadata.picture_renderer.graphic_source_paths(workspace_id, task_id, overlays, context)
        except PicturePreviewRejected as exc:
            raise RenderRejected(status_code=exc.status_code, detail=exc.detail) from exc

    def render_montage(self, workspace_id, plan, narration_audio_path, *, job_staging_path=None, subtitle_tracks=None, render_context=None):
        original_requirement = plan.duration_requirement
        task = self.repository.get_creation_task(workspace_id, plan.task_id)
        plan.duration_requirement = DurationRequirement.from_plan(
            self.repository.get_creation_plan(workspace_id, task.plan_id)).payload()
        try:
            return self.metadata.picture_renderer.render_montage(workspace_id, plan, narration_audio_path, job_staging_path=job_staging_path, subtitle_tracks=subtitle_tracks, render_context=render_context)
        except PicturePreviewRejected as exc:
            raise RenderRejected(status_code=exc.status_code, detail=exc.detail) from exc
        finally:
            plan.duration_requirement = original_requirement
