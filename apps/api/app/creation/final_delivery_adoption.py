"""Adopt a completed delivery in the original Grant's checkpoint transaction."""
from dataclasses import asdict
import hashlib
import json
from fractions import Fraction

from app.creation.contracts import TaskEvent, TimelineVersion, TransformRun
from app.models import utc_now
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.task_audio import media_digest


def adopt_delivery(delivery, conn, job, *, actor, grant, record, picture_fragments):
    from app.creation.render_stage_use_case import render_input_payload, render_input_digest_for
    from app.creation.duration_requirement import DurationRequirement
    from app.subtitle_schedule import subtitle_schedule, narration_schedule
    repo = delivery.repository
    manifest = delivery.manifest(job, actor=actor, connection=conn)
    completed = delivery._artifact(job, manifest, 'final_mux', connection=conn)
    if (completed is None or completed['parent']['binding']['artifact_id'] != record.id
            or completed['parent']['binding']['producer_request_id'] != grant.request_id
            or completed['parent']['binding']['producer_generation'] != grant.generation):
        raise ValueError('最终采用不属于本原Job与本次已结算封装许可')
    task = repo.get_creation_task(job.workspace_id, job.task_id, connection=conn)
    leaf_manifest = picture_fragments.manifest(job, actor=actor, connection=conn)
    if leaf_manifest is None or task.current_timeline_version_id != leaf_manifest['head']:
        raise ValueError('最终采用期间当前HEAD已变化，原产物保留')
    subtitle = picture_fragments.subtitle_artifact(job, actor=actor, connection=conn)
    plan = creation_task_montage_plan_from_payload(repo.get_creation_task_montage_plan(job.workspace_id, job.task_id, connection=conn))
    DurationRequirement.from_plan(repo.get_creation_plan(job.workspace_id, task.plan_id)).require(plan.duration_seconds, 30)
    tracks = picture_fragments.metadata.current_subtitle_tracks(job.workspace_id, task, montage=plan)
    parent_version = repo.get_timeline_version(job.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
    tracks = tracks if tracks or (parent_version is not None and 'subtitle_track_ids' in parent_version.snapshot) else None
    visible = bool(subtitle_schedule(tracks, plan.duration_seconds)) if tracks is not None else bool(narration_schedule(plan.cues, plan.duration_seconds))
    input_payload = render_input_payload(plan, subtitles_visible=visible,
        subtitle_tracks=json.loads(json.dumps([asdict(track) for track in tracks or []], default=str)),
        timeline_version_id=task.current_timeline_version_id, bind_timeline_version=True)
    version_id = f'timeline_delivery_{job.id}'
    output_refs = [record.logical_ref] + ([subtitle.logical_ref] if subtitle is not None else [])
    transform = TransformRun(id=f'transform_delivery_adoption_{job.id}', workspace_id=job.workspace_id,
        task_id=job.task_id, job_id=job.id, transform_type='montage_delivery', input_digest=job.input_digest,
        input_refs=record.input_refs + ([subtitle.logical_ref] if subtitle is not None else []), output_refs=output_refs,
        engine='original-job-delivery/v1', parameters={'delivery_manifest_digest': manifest['digest'],
            'render_input_digest': render_input_digest_for(input_payload),
            'final_mux_transform_id': completed['parent']['binding']['transform_id'],
            'final_mux_transform_digest': completed['parent']['binding']['transform_digest']},
        status='succeeded', started_at=utc_now(), finished_at=utc_now())
    repo.create_transform_run(transform, connection=conn)
    plan.status, plan.output_path = 'rendered', record.logical_ref
    plan.subtitle_path = subtitle.logical_ref if subtitle is not None else None
    plan.message = '成片已生成，等待检查。'
    rendered = creation_task_montage_plan_to_payload(plan)
    version = TimelineVersion(id=version_id, workspace_id=job.workspace_id, task_id=job.task_id,
        parent_version_id=task.current_timeline_version_id,
        sequence=repo.next_timeline_sequence(job.workspace_id, job.task_id, connection=conn),
        created_by_operation_id=None, snapshot={'montage': rendered,
            **picture_fragments.metadata.current_narration_snapshot(job.workspace_id, task),
            'output_refs': output_refs, 'transform_run_id': transform.id, 'audio_delivery': {
                'manifest_digest': manifest['digest'], 'manifest_event_id': delivery.manifest_id(job),
                'picture_fragment_manifest_event_id': picture_fragments.manifest_id(job),
                'job_id': job.id, 'request_id': grant.request_id, 'permit_generation': grant.generation,
                'video_artifact_id': record.id, 'subtitle_artifact_id': subtitle.id if subtitle is not None else None,
                'render_input': input_payload, 'render_input_digest': render_input_digest_for(input_payload),
                'video_parent': completed['parent'], 'video_file_identity': completed['file_identity']}})
    from app.creation.transactions import clone_subtitle_track, clone_cover_asset
    cloned_tracks = [clone_subtitle_track(track, timeline_version_id=version.id,
        track_id=f'subtitle_delivery_{job.id}_{index}', artifact_ref=subtitle.logical_ref if subtitle is not None else None,
        transform_id=f'transform_{subtitle.id}' if subtitle is not None else None) for index, track in enumerate(tracks or [])]
    if tracks is None and subtitle is not None:
        initial = picture_fragments.metadata.subtitle_track_from_montage(job.workspace_id, task, version, plan)
        if initial is not None:
            cloned_tracks.append(initial)
    version.snapshot['subtitle_track_ids'] = [track.id for track in cloned_tracks]
    cover = (picture_fragments.metadata.cover_asset_for_version(job.workspace_id, job.task_id, parent_version)
        if parent_version is not None else None)
    cloned_cover = clone_cover_asset(cover, timeline_version_id=version.id) if cover is not None else None
    if cloned_cover is not None:
        version.snapshot['cover_asset_id'] = cloned_cover.id
    repo.save_creation_task_montage_plan(job.workspace_id, job.task_id, plan.id, rendered, connection=conn,
        expected_current_version_id=task.current_timeline_version_id)
    repo.create_timeline_version(version, connection=conn, expected_current_version_id=task.current_timeline_version_id)
    for cloned in cloned_tracks:
        repo.create_subtitle_track(cloned, connection=conn)
    if cloned_cover is not None:
        repo.create_cover_asset(cloned_cover, connection=conn)
    repo.append_task_event(TaskEvent(f'event_{job.id}_delivery_adopted', job.workspace_id, job.task_id, job.id,
        'render_succeeded', {'timeline_version_id': version.id, 'output_refs': output_refs,
            'request_id': grant.request_id, 'permit_generation': grant.generation,
            'delivery_manifest_digest': manifest['digest']}), connection=conn)
    return version


def valid_delivery(repository, store, task, version, plan, *, metadata, render_context=None):
    from app.creation.render_stage_use_case import render_input_payload, render_input_digest_for
    from app.creation.audio_delivery_assembly import _identity
    saved = version.snapshot['audio_delivery']
    job = repository.get_job_run(task.workspace_id, saved['job_id'])
    transform = repository.get_transform_run(task.workspace_id, version.snapshot['transform_run_id'])
    if (job.task_id != task.id or job.kind != 'creation_task_execution' or job.status != 'succeeded'
            or transform.transform_type != 'montage_delivery' or transform.job_id != job.id or transform.status != 'succeeded'
            or job.output_refs != version.snapshot['output_refs'] or transform.output_refs != job.output_refs
            or transform.input_digest != job.input_digest or transform.engine != 'original-job-delivery/v1'):
        return False
    event = repository.get_task_event(task.workspace_id, saved['manifest_event_id'])
    manifest = event.payload
    if (event.event_type != 'audio_delivery_manifest' or event.job_id != job.id or event.task_id != task.id or manifest['input_digest'] != job.input_digest
            or manifest['digest'] != saved['manifest_digest'] or manifest['digest'] != media_digest({
                key: value for key, value in manifest.items() if key != 'digest'})
            or transform.parameters['delivery_manifest_digest'] != manifest['digest']):
        return False
    original_input = saved['render_input']
    expected = render_input_payload(creation_task_montage_plan_from_payload(plan),
        subtitles_visible=original_input['subtitles_visible'], subtitle_tracks=original_input['subtitle_tracks'],
        timeline_version_id=original_input['timeline_version_id'], bind_timeline_version=True)
    if (expected != original_input or render_input_digest_for(expected) != saved['render_input_digest']
            or transform.parameters['render_input_digest'] != saved['render_input_digest']
            or version.parent_version_id != original_input['timeline_version_id']):
        return False
    final_transform = repository.get_transform_run(task.workspace_id, saved['video_parent']['binding']['transform_id'])
    video = repository.get_artifact_record(task.workspace_id, saved['video_artifact_id'])
    if (video.producer_job_id != job.id or video.task_id != task.id or video.kind != 'rendered_video'
            or video.logical_ref != plan['output_path'] or video.sha256 != saved['video_parent']['binding']['sha256']
            or final_transform.status != 'succeeded' or final_transform.job_id != job.id
            or final_transform.output_refs != [video.logical_ref] or final_transform.transform_type != 'final_mux'
            or media_digest(asdict(final_transform)) != saved['video_parent']['binding']['transform_digest']
            or final_transform.parameters['manifest_digest'] != manifest['digest']
            or final_transform.task_id != task.id or final_transform.parameters['clock'] != saved['video_parent']['clock']):
        return False
    with repository._connect() as conn:
        request = conn.execute("SELECT * FROM device_work_requests WHERE workspace_id=? AND id=? AND owner_id=? "
            "AND owner_type='creation_job' AND state='done' AND last_grant_generation=?",
            (task.workspace_id, saved['request_id'], job.id, saved['permit_generation'])).fetchone()
        settled = conn.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? "
            "AND event_type='task_execution_unit_settled' AND json_extract(payload_json,'$.usage_id')=?",
            (task.workspace_id, job.id, f'{saved["request_id"]}:{saved["permit_generation"]}')).fetchone()
    if request is None or settled is None:
        return False
    resources = json.loads(request['resources_json'])
    if (resources['kind'] != 'creation_final_mux' or resources['manifest_digest'] != manifest['digest']
            or resources['unit_id'] != final_transform.input_digest):
        return False
    video_path = store.resolve_file(video.logical_ref)
    if _identity(video_path) != saved['video_file_identity'] or video_path.stat().st_size != video.size_bytes:
        return False
    if saved['subtitle_artifact_id'] is not None:
        subtitle = repository.get_artifact_record(task.workspace_id, saved['subtitle_artifact_id'])
        st = repository.get_transform_run(task.workspace_id, f'transform_{subtitle.id}')
        if (subtitle.producer_job_id != job.id or subtitle.task_id != task.id or subtitle.logical_ref != plan['subtitle_path']
                or st.status != 'succeeded' or st.job_id != job.id or st.output_refs != [subtitle.logical_ref]
                or _identity(store.resolve_file(subtitle.logical_ref)) != st.parameters['file_identity']):
            return False
    elif plan.get('subtitle_path') is not None:
        return False
    from app.creation_execution import CreationExecutionActor
    leaf_event = repository.get_task_event(task.workspace_id, saved['picture_fragment_manifest_event_id'])
    leaf = leaf_event.payload
    if (leaf_event.event_type != 'picture_fragment_manifest' or leaf_event.job_id != job.id or leaf_event.task_id != task.id
            or leaf['digest'] != media_digest({key: value for key, value in leaf.items() if key != 'digest'})
            or leaf['input_digest'] != job.input_digest):
        return False
    original = CreationExecutionActor.from_event(task.workspace_id,
        repository.get_task_event(task.workspace_id, f'event_{job.id}_start').payload)
    actual_plan = creation_task_montage_plan_from_payload(plan)
    files, _images = metadata.picture_renderer.qualified_picture_sources(task.workspace_id, task, actual_plan, [actual_plan], original)
    if json.loads(json.dumps(files)) != leaf['qualified_files']:
        return False
    for source in leaf['sources'].values():
        if _identity(source['path']) != source['file_identity']:
            return False
    clock = saved['video_parent']['clock']
    if (Fraction(clock['picture']['last_end_pts'], clock['picture']['time_base_den']) != Fraction(str(actual_plan.duration_seconds))
            or Fraction(clock['audio']['sample_count'], clock['audio']['sample_rate']) != Fraction(str(actual_plan.duration_seconds))):
        return False
    # Immutable producer SHA + fresh stat is sufficient for a read projection.
    # A caller that holds a real media Grant may also ask for full-byte checking.
    if render_context is not None:
        for ref, expected_sha in [(video.logical_ref, video.sha256)] + ([(subtitle.logical_ref, subtitle.sha256)]
                if saved['subtitle_artifact_id'] is not None else []):
            digest = hashlib.sha256()
            with store.resolve_file(ref).open('rb') as stream:
                while True:
                    render_context.check()
                    part = stream.read(65536)
                    if not part:
                        break
                    digest.update(part)
            if digest.hexdigest() != expected_sha:
                return False
    return True
