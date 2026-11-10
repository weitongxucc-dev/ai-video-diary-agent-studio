"""Read the actual render/adoption owner without inferring success from files."""
from app.task_audio import media_digest


def rendered_execution_version(repository, task, job_id):
    if task.current_timeline_version_id is None:
        return None
    version = repository.timeline_records.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
    provenance = rendered_version_provenance(repository, version)
    if provenance is None or provenance['job_id'] != job_id or provenance['base_version_id'] is None:
        return None
    base = repository.timeline_records.get_timeline_version(task.workspace_id, provenance['base_version_id'])
    return {'job_id': job_id,
        'base_version': {'timeline_version_id': base.id, 'timeline_sequence': base.sequence},
        'result_version': {'timeline_version_id': version.id, 'timeline_sequence': version.sequence}}


def rendered_version_provenance(repository, version):
    saved = version.snapshot.get('audio_delivery')
    if saved is None:
        request_id = version.snapshot.get('creation_render_request_id')
        row = repository.goal_render_request_row(version.workspace_id, request_id)
        if (row is None or row['state'] != 'done'
                or version.snapshot.get('render_input_timeline_version_id') != version.parent_version_id):
            return None
        return {'job_id': row['owner_id'], 'request_id': request_id, 'base_version_id': version.parent_version_id}
    try:
        job = repository.job_records.get_job_run(version.workspace_id, saved['job_id'])
        transform = repository.lineage_records.get_transform_run(version.workspace_id, version.snapshot['transform_run_id'])
        event = repository.execution_records.get_task_event(version.workspace_id, saved['manifest_event_id'])
        record = repository.execution_records.get_artifact_record(version.workspace_id, saved['video_artifact_id'])
        request = repository.goal_render_request_row(version.workspace_id, saved['request_id'])
        manifest = event.payload
        if (saved['origin_timeline_version_id'] != version.id
                or saved['render_input']['timeline_version_id'] != version.parent_version_id
                or job.task_id != version.task_id or job.kind != 'creation_task_execution' or job.status != 'succeeded'
                or transform.task_id != version.task_id or transform.job_id != job.id or transform.status != 'succeeded'
                or transform.transform_type != 'montage_delivery' or transform.engine != 'original-job-delivery/v1'
                or transform.input_digest != job.input_digest or transform.output_refs != version.snapshot['output_refs']
                or event.task_id != version.task_id or event.job_id != job.id or event.event_type != 'audio_delivery_manifest'
                or manifest['input_digest'] != job.input_digest or manifest['digest'] != saved['manifest_digest']
                or manifest['digest'] != media_digest({key: value for key, value in manifest.items() if key != 'digest'})
                or transform.parameters['delivery_manifest_digest'] != manifest['digest']
                or record.task_id != version.task_id or record.producer_job_id != job.id or record.kind != 'rendered_video'
                or record.logical_ref not in transform.output_refs
                or request is None or request['owner_id'] != job.id or request['state'] != 'done'):
            return None
    except (KeyError, TypeError):
        return None  # Incomplete/old adoption metadata cannot establish this ownership.
    return {'job_id': job.id, 'request_id': saved['request_id'], 'base_version_id': version.parent_version_id}
