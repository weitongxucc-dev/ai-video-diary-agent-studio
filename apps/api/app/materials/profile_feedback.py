"""Task-scoped profile feedback reconstructed from production records only."""
from contextlib import closing
import json

from app.materials.profile_candidates import ProfileCandidateReader
from app.source_policy import knowledge_item_readable


def _values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)


def _fixed_reference(value, kind, identity, version):
    """Match a complete typed pair, never independent ID/version strings."""
    if isinstance(value, dict):
        if value.get(kind + '_profile_id') == identity and value.get(kind + '_version') == version:
            return True
        if value.get(kind + '_id') == identity and value.get(kind + '_version') == version:
            return True
        selected = value.get('selected_' + kind)
        if isinstance(selected, dict) and (selected.get('profile_id'), selected.get('version')) == (identity, version):
            return True
        return any(_fixed_reference(child, kind, identity, version) for key, child in value.items()
                   if not (kind == 'person' and key in {'selected_voice', 'voice'}))
    if isinstance(value, list):
        return any(_fixed_reference(child, kind, identity, version) for child in value)
    return False


class ProfileCandidateFeedbackReader:
    """Drop-in candidate reader; optional task enables a bounded read-only history.

    Both fixed/current profile visibility and current grants are evaluated by
    the existing reader. Historical production evidence never grants reuse.
    """
    def __init__(self, repository):
        self.repository = repository
        self.candidates = ProfileCandidateReader(repository)

    def __call__(self, workspace_id, kind, profile_id, version, user_id,
                 local_diagnostic=False, *, task_id=None):
        candidate = self.candidates(workspace_id, kind, profile_id, version, user_id, local_diagnostic)
        candidate['feedback'] = (self._history(workspace_id, task_id, kind, profile_id, version,
            user_id, local_diagnostic) if task_id is not None else {'status': 'not_requested'})
        return candidate

    def _history(self, workspace_id, task_id, kind, profile_id, version, user_id, local_diagnostic):
        with closing(self.repository._connect()) as conn:
            conn.execute('BEGIN')
            row = conn.execute('SELECT payload,visibility,plan_id FROM creation_tasks WHERE workspace_id=? AND id=?',
                (workspace_id, task_id)).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = json.loads(row['payload'])
            if not knowledge_item_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                    visibility=row['visibility'], owner_user_id=task.get('owner_user_id')):
                raise KeyError(task_id)
            plan_row = conn.execute('SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?',
                (workspace_id, row['plan_id'])).fetchone()
            plan = json.loads(plan_row['payload']) if plan_row else {}
            def rows(table, order):
                return conn.execute(f'SELECT * FROM {table} WHERE workspace_id=? AND task_id=? ORDER BY {order} LIMIT 2001',
                    (workspace_id, task_id)).fetchall()
            tables = {table: rows(table, order) for table, order in (
                ('narration_segments', 'created_at,id'), ('person_renders', 'created_at,id'),
                ('task_events', 'created_at,id'), ('job_runs', 'created_at,id'),
                ('timeline_versions', 'sequence'), ('artifact_records', 'created_at,id'),
                ('transform_runs', 'started_at,id'))}
        partial = any(len(items) > 2000 for items in tables.values())
        tables = {name: items[:2000] for name, items in tables.items()}
        generations, references, record_ids, ready_ids, cache_keys = [], set(), set(), set(), set()
        for table in ('narration_segments', 'person_renders'):
            if kind == 'person' and table == 'narration_segments':
                continue
            for item in tables[table]:
                if (item[kind + '_profile_id'], item[kind + '_version']) != (profile_id, version):
                    continue
                # Recorded narration carries no generated voice claim.
                if table == 'narration_segments' and item['source_kind'] != 'generated':
                    continue
                ref = item['audio_reference'] if table == 'narration_segments' else item['artifact_ref']
                generations.append({'kind': 'narration_segment' if table == 'narration_segments' else 'person_render',
                    'record_id': item['id'], 'status': item['status'],
                    'output_reference_recorded': bool(ref),
                    'failure_reason': item['failure_reason'] if table == 'person_renders' else None})
                if table == 'person_renders':
                    record_ids.add(item['id'])
                    cache_keys.add(item['cache_key'])
                if ref and item['status'] == 'ready':
                    references.add(ref)
                    if table == 'person_renders':
                        ready_ids.add(item['id'])
        snapshots = [(row, json.loads(row['snapshot_json'])) for row in tables['timeline_versions']]
        # Narration rows may be replaced in place; frozen snapshots retain the
        # exact historical voice and source without borrowing today's segment ID.
        if kind == 'voice':
            seen = {(item['record_id'], item['status']) for item in generations
                    if item['kind'] == 'narration_segment'}
            for row, snapshot in snapshots:
                for segment in snapshot.get('narration_segments', []):
                    if ((segment.get('voice_profile_id'), segment.get('voice_version')) != (profile_id, version)
                            or segment.get('source_kind', 'generated') != 'generated'):
                        continue
                    identity = (segment['id'], segment['status'])
                    if identity not in seen:
                        generations.append({'kind': 'narration_segment_snapshot', 'record_id': segment['id'],
                            'status': segment['status'], 'output_reference_recorded': bool(segment.get('audio_reference')),
                            'timeline_version_id': row['id'], 'failure_reason': None})
                        seen.add(identity)
                    if segment['status'] == 'ready' and segment.get('audio_reference'):
                        references.add(segment['audio_reference'])
        events = [(row, json.loads(row['payload_json'])) for row in tables['task_events']]
        job_ids = {row['id'] for row in tables['job_runs']
                   if row['kind'] == 'person_render' and row['input_digest'] in cache_keys}
        for row, payload in events:
            if _fixed_reference(payload, kind, profile_id, version) or set(_values(payload)) & (record_ids | references):
                if row['job_id']:
                    job_ids.add(row['job_id'])
        # Artifact/transform edges prove downstream use, not task-level co-presence.
        related = references | {f'{kind}-profile://{profile_id}/{version}'} | {
            f'person-render://{identity}' for identity in ready_ids}
        while True:
            expanded = set(related)
            for row in tables['artifact_records']:
                if set(json.loads(row['input_refs_json'])) & related:
                    expanded.add(row['logical_ref'])
            for row in tables['transform_runs']:
                if set(json.loads(row['input_refs_json'])) & related:
                    expanded.update(json.loads(row['output_refs_json']))
            if expanded == related:
                break
            related = expanded
        timeline, presence, output_refs = [], {}, set()
        for row, snapshot in snapshots:
            # A generated record is not adopted until its ID/ref occurs in a snapshot.
            present = bool(set(_values(snapshot.get('montage', {}))) & (ready_ids | references))
            if kind == 'voice':
                present = present or any(segment.get('status') == 'ready'
                    and segment.get('source_kind', 'generated') == 'generated'
                    and segment.get('audio_reference')
                    and (segment.get('voice_profile_id'), segment.get('voice_version')) == (profile_id, version)
                    for segment in snapshot.get('narration_segments', []))
            key = snapshot.get('narration_cache_key')
            present = present or bool(key and 'narration://artifact/' + key in references)
            presence[row['id']] = present
            parent_present = presence.get(row['parent_version_id'], False)
            if present or parent_present:
                timeline.append({'timeline_version_id': row['id'], 'sequence': row['sequence'],
                    'profile_present': present, 'operation_id': row['created_by_operation_id'],
                    'transition': 'added' if present and not parent_present else 'retained' if present else 'removed'})
            if present:
                output_refs.update(snapshot.get('output_refs', []))
        # Only registered artifacts may appear as output facts.
        artifacts = [row for row in tables['artifact_records'] if row['logical_ref'] in related]
        job_ids.update(row['producer_job_id'] for row in artifacts)
        for row in tables['transform_runs']:
            if set(json.loads(row['input_refs_json'])) & related:
                job_ids.add(row['job_id'])
        by_artifact = {row['id']: row for row in artifacts}
        by_version = {row['id']: snapshot for row, snapshot in snapshots}
        observations = []
        for row, payload in events:
            result = payload.get('result')
            if not isinstance(result, dict) or not result.get('observation_id'):
                continue
            artifact = by_artifact.get(result.get('output', {}).get('artifact_id'))
            snapshot = by_version.get(result.get('timeline_version_id'))
            if (artifact is not None and snapshot is not None
                    and artifact['logical_ref'] in snapshot.get('output_refs', [])):
                observations.append({'observation_id': result['observation_id'], 'job_id': row['job_id'],
                    'timeline_version_id': result.get('timeline_version_id'),
                    'artifact_id': result['output']['artifact_id']})
                job_ids.add(row['job_id'])
        jobs = [{'job_id': row['id'], 'kind': row['kind'], 'status': row['status'],
                 'failure_reason': row['failure_reason']} for row in tables['job_runs'] if row['id'] in job_ids]
        statuses = {job['job_id']: job['status'] for job in jobs}
        for observation in observations:
            observation['job_status'] = statuses.get(observation['job_id'], 'unknown')
        selected = plan.get('selected_' + kind) or {}
        referenced = (selected.get('profile_id'), selected.get('version')) == (profile_id, version)
        evidence = bool(referenced or generations or jobs or timeline)
        return {'schema': 'profile-production-feedback/v1', 'task_id': task_id,
            'profile_id': profile_id, 'profile_version': version,
            'status': 'evidence_found' if evidence else 'unknown' if partial else 'not_used',
            'completeness': 'partial' if partial else 'complete', 'task_reference': referenced,
            'stages': {'task_reference': 'referenced' if referenced else 'not_recorded',
                'generation': sorted({item['status'] for item in generations}) or ['not_recorded'],
                'timeline': 'recorded' if timeline else 'not_recorded',
                'output': 'registered' if any(row['logical_ref'] in output_refs for row in artifacts) else 'not_recorded',
                'observation': 'recorded' if observations else 'not_recorded'},
            'generations': generations, 'jobs': jobs, 'timeline_history': timeline,
            'current_profile_present': presence.get(task.get('current_timeline_version_id')),
            'artifacts': [{'artifact_id': row['id'], 'kind': row['kind'], 'producer_job_id': row['producer_job_id'],
                'output_linked': row['logical_ref'] in output_refs} for row in artifacts],
            'observations': observations, 'media_validation': 'unknown',
            'instruction': 'not_used仅表示本次有界记录内没有使用证据，不证明从未使用。原记录分别表达引用、生成、采用与输出观察；ready或Job成功不代表人工视听验收，历史使用不授予当前复用许可。'}
