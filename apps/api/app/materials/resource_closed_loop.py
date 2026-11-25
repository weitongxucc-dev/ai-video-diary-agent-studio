"""Read-only reconstruction from production facts, never the manual usage ledger."""
from contextlib import closing
import json
import re

from app.source_policy import knowledge_item_readable
from app.materials.resource_governance import resource_trace_metadata


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _catalogs(value):
    if isinstance(value, dict):
        catalog = value.get('catalog_resource')
        if isinstance(catalog, dict):
            yield catalog
        for child in value.values():
            yield from _catalogs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _catalogs(child)


def _resource_requests(events, jobs, resource_id, version, user_id):
    """Project the original member's journal; a missing result stays unknown."""
    by_id = {row['id']: (row, payload) for row, payload in events}
    job_ids = {row['id'] for row in jobs}
    requests = []
    for row, payload in events:
        inputs = payload.get('inputs', {})
        if (row['event_type'] != 'edit_request_claimed' or payload.get('phase') != 'audio_resource'
                or not user_id or payload.get('member_id') != user_id
                or inputs.get('resource_id') != resource_id or inputs.get('version') != version):
            continue
        request = {'request_id': payload['request_id'], 'status': 'unknown', 'job_id': None}
        saved = by_id.get(row['id'] + '_result')
        if saved is not None and saved[0]['event_type'] == 'edit_request_result':
            result = saved[1]
            status = result.get('status_code')
            response = result.get('response', {})
            if type(status) is int and status >= 400:
                request.update(status='rejected', status_code=status)
                detail = response.get('detail', {})
                code = response.get('code') or response.get('error') or (detail.get('error') if isinstance(detail, dict) else None)
                if isinstance(code, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code):
                    request['error_code'] = code
            elif type(status) is int and 200 <= status < 300:
                job_id = response.get('job_id')
                start = by_id.get(f'event_{job_id}_start')
                identity = inputs.get('actor_identity')
                if (job_id in job_ids and start is not None and start[0]['job_id'] == job_id
                        and isinstance(identity, dict) and identity
                        and all(start[1].get(key) == value for key, value in identity.items())):
                    request.update(status='recorded', job_id=job_id)
        requests.append(request)
    return requests


class ResourceClosedLoopReader:
    """Metadata-only task-scoped history; no media access or state mutation.

    Caller supplies the verified member, never a model-reported identity. The
    task visibility rule is enforced before any resource or job is returned.
    """
    def __init__(self, connection_factory):
        self._connect = connection_factory

    def read(self, workspace_id, task_id, resource_id, version, *, user_id,
             local_diagnostic=False):
        if type(version) is not int or version < 1 or not resource_id:
            raise ValueError('资源回查必须指定ID和正整数版本')
        with closing(self._connect()) as conn:
            conn.execute('BEGIN')
            task_row = conn.execute('SELECT payload,visibility FROM creation_tasks WHERE workspace_id=? AND id=?',
                (workspace_id, task_id)).fetchone()
            if task_row is None:
                raise KeyError(task_id)
            task = json.loads(task_row['payload'])
            if not knowledge_item_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                    visibility=task_row['visibility'], owner_user_id=task.get('owner_user_id')):
                raise KeyError(task_id)
            # Bound every task history explicitly; incomplete reads never claim closure.
            def rows(table, order):
                scope = ' AND candidate_id IS NULL' if table == 'timeline_versions' else ''
                return conn.execute(f'SELECT * FROM {table} WHERE workspace_id=? AND task_id=?{scope} ORDER BY {order} LIMIT 2001',
                    (workspace_id, task_id)).fetchall()
            events = rows('task_events', 'created_at,id')
            jobs = rows('job_runs', 'created_at,id')
            artifacts = rows('artifact_records', 'created_at,id')
            transforms = rows('transform_runs', 'started_at,id')
            versions = rows('timeline_versions', 'sequence')
            operations = rows('edit_operations', 'created_at,id')
        truncated = any(len(items) > 2000 for items in (events, jobs, artifacts, transforms, versions, operations))
        events = [(row, json.loads(row['payload_json'])) for row in events[:2000]]
        requests = _resource_requests(events, jobs[:2000], resource_id, version, user_id)
        transforms = [(row, json.loads(row['parameters_json'])) for row in transforms[:2000]]

        def selected(catalog):
            return catalog.get('id') == resource_id and catalog.get('version') == version

        import_jobs = {row['job_id'] for row, payload in events if any(selected(c) for c in _catalogs(payload))}
        import_jobs.update(row['job_id'] for row, payload in transforms if any(selected(c) for c in _catalogs(payload)))
        import_jobs.discard(None)
        import_jobs.update(item['job_id'] for item in requests if item['status'] == 'recorded')
        sources = {row['logical_ref'] for row in artifacts[:2000] if row['producer_job_id'] in import_jobs}
        # Provenance graph follows registered input/output refs, not catalog labels.
        related = set(sources)
        while True:
            expanded = set(related)
            for row in artifacts[:2000]:
                if set(json.loads(row['input_refs_json'])) & related:
                    expanded.add(row['logical_ref'])
            for row, _ in transforms:
                if set(json.loads(row['input_refs_json'])) & related:
                    expanded.update(json.loads(row['output_refs_json']))
            if expanded == related:
                break
            related = expanded
        operation_types = {row['id']: row['operation_type'] for row in operations[:2000]}
        timeline = []
        presence = {}
        for row in versions[:2000]:
            snapshot = json.loads(row['snapshot_json'])
            graph = snapshot.get('montage', {}).get('audio_timeline') or snapshot.get('audio_timeline') or {}
            present = bool(set(_strings(graph)) & sources)
            presence[row['id']] = present
            parent_present = presence.get(row['parent_version_id'], False)
            if present or parent_present:
                timeline.append({'timeline_version_id': row['id'], 'sequence': row['sequence'],
                    'parent_version_id': row['parent_version_id'], 'resource_present': present,
                    'transition': 'added' if present and not parent_present else 'retained' if present else 'removed',
                    'operation_id': row['created_by_operation_id'],
                    'operation_type': operation_types.get(row['created_by_operation_id'], 'unknown')})
            if present:
                related.update(snapshot.get('output_refs', []))
        timeline_ids = {item['timeline_version_id'] for item in timeline}
        related_artifacts = {row['id']: row for row in artifacts[:2000] if row['logical_ref'] in related}
        linked_jobs = import_jobs | {row['producer_job_id'] for row in related_artifacts.values()}
        observations = []
        links = {}
        for row, payload in events:
            values = set(_strings(payload))
            matches = values & (related | timeline_ids | set(related_artifacts))
            if row['job_id'] in import_jobs or matches:
                if row['job_id']:
                    linked_jobs.add(row['job_id'])
                links[row['id']] = {'event_id': row['id'], 'job_id': row['job_id'],
                    'event_type': row['event_type'], 'matched_refs': sorted(matches),
                    'trace': resource_trace_metadata(payload.get('arguments', {}).get('_typed_source', {}),
                        job_id=row['job_id'])}
            result = payload.get('result')
            if isinstance(result, dict) and result.get('observation_id') and result.get('output', {}).get('artifact_id') in related_artifacts:
                observations.append({'event_id': row['id'], 'job_id': row['job_id'],
                    'observation_id': result['observation_id'], 'timeline_version_id': result.get('timeline_version_id'),
                    'artifact_id': result['output']['artifact_id'],
                    'validation': 'unknown', 'reason': '结果事件须结合原Job终态判断，未重新验证文件或媒体'})
        job_projection = [{'job_id': row['id'], 'kind': row['kind'], 'status': row['status'],
            'failure_reason': row['failure_reason'], 'repair_action': row['repair_action']}
            for row in jobs[:2000] if row['id'] in linked_jobs]
        statuses = {row['job_id']: row['status'] for row in job_projection}
        for observation in observations:
            observation['job_status'] = statuses.get(observation['job_id'], 'unknown')
        return {'schema': 'resource-closed-loop/v1', 'workspace_id': workspace_id, 'task_id': task_id,
            'resource_id': resource_id, 'resource_version': version,
            'completeness': 'partial' if truncated else 'complete',
            'requests': requests,
            'import_job_ids': sorted(import_jobs), 'timeline_history': timeline, 'jobs': job_projection,
            'artifacts': [{'artifact_id': row['id'], 'producer_job_id': row['producer_job_id'],
                'kind': row['kind'], 'sha256': row['sha256']} for row in related_artifacts.values()],
            'transforms': [{'transform_id': row['id'], 'job_id': row['job_id'],
                'status': row['status'], 'type': row['transform_type']}
                for row, _ in transforms if row['job_id'] in import_jobs
                or set(json.loads(row['input_refs_json'])) & related],
            'observations': observations, 'events': list(links.values()),
            'current_resource_present': presence.get(task.get('current_timeline_version_id')),
            'media_validation': 'unknown',
            'instruction': '只回读原始生产记录；未读取磁盘、执行媒体、创建采用或覆盖失败。结果事件不等于Job成功，移除不删除历史。'}
