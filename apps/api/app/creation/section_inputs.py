"""Effective section inputs and dependency facts in their original read snapshots."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, replace
import hashlib
import json
import sqlite3

from app.creation.plan_records import CreationPlanRecordStore
from app.creation.narration_segment_records import validate_narration_segment
from app.creation.script_contracts import CreationTaskScriptSection as _CreationTaskScriptSection
from app.creation.script_records import ScriptRecordStore
from app.creation.task_input_reads import require_creation_task, narration_segment_from_row


class SectionInputReader:
    def __init__(
        self, *, connection_factory: Callable[[], sqlite3.Connection],
        read_snapshot: Callable[..., AbstractContextManager[sqlite3.Connection]],
        script_records: ScriptRecordStore, plan_records: CreationPlanRecordStore,
    ) -> None:
        self._connect = connection_factory
        self.creation_execution_read = read_snapshot
        self.script_records = script_records
        self.plan_records = plan_records

    def iter_creation_narration_sections(self, task, script, *, connection):
        """Effective text and its audio, one row at a time in the same snapshot.

        This projection never changes the original script digest. Check orphan
        audio even when the first section still needs narration.
        """
        if not connection.in_transaction:
            raise ValueError("配音段落读取必须复用已开启的事务")
        if (script.workspace_id, script.task_id, script.id) != (
                task.workspace_id, task.id, task.confirmed_script_id):
            raise ValueError('采用的段版本不属于当前确认文案')
        orphan = connection.execute(
            'SELECT 1 FROM narration_segments n WHERE n.workspace_id=? AND n.task_id=? AND n.script_id=? '
            'AND NOT EXISTS (SELECT 1 FROM creation_task_script_sections s WHERE s.workspace_id=n.workspace_id '
            'AND s.script_id=n.script_id AND s.section_id=n.source_section_id) LIMIT 1',
            (task.workspace_id, task.id, script.id)).fetchone()
        if orphan is not None:
            raise ValueError('现有配音片段集不属于当前唯一文案段落')
        for section in self.script_records.iter_execution_script_sections(connection, task.workspace_id, script.id):
            state = self.stage_segment_state(task.workspace_id, task.id, section.id, connection=connection)
            if state is not None:
                if state['script_id'] != script.id:
                    raise ValueError('采用的段版本不属于当前确认文案')
                section = replace(section, content=state['text'])
            rows = connection.execute(
                'SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? '
                'AND source_section_id=? LIMIT 2', (task.workspace_id, task.id, script.id, section.id)).fetchmany(2)
            if len(rows) > 1:
                raise ValueError('现有配音片段集不属于当前唯一文案段落')
            segment = narration_segment_from_row(rows[0]) if rows else None
            if segment is not None and segment.status == 'ready' and segment.text != section.content:
                raise ValueError('已保存配音片段的文字与采用正文不匹配，须先核对原产物')
            yield section, segment

    def read_creation_section_input(self, task, script, section_id, *, connection):
        """Read one actual effective section and its original audio in the caller snapshot."""
        if not connection.in_transaction:
            raise ValueError('段落规划读取必须复用已开启的事务')
        if (script.workspace_id, script.task_id, script.id) != (
                task.workspace_id, task.id, task.confirmed_script_id):
            raise ValueError('采用的段版本不属于当前确认文案')
        row = connection.execute(
            'SELECT ordinal,section_json FROM creation_task_script_sections '
            'WHERE workspace_id=? AND script_id=? AND section_id=?',
            (task.workspace_id, script.id, section_id)).fetchone()
        if row is None:
            raise ValueError('稳定段落ID不属于当前确认文案')
        section = _CreationTaskScriptSection(**json.loads(row['section_json']))
        adopted = self.stage_segment_state(task.workspace_id, task.id, section_id, connection=connection)
        if adopted is not None:
            if adopted['script_id'] != script.id:
                raise ValueError('采用的段版本不属于当前确认文案')
            section = replace(section, content=adopted['text'])
        audio_rows = connection.execute(
            'SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? '
            'AND source_section_id=? LIMIT 2', (task.workspace_id, task.id, script.id, section_id)).fetchmany(2)
        if len(audio_rows) > 1:
            raise ValueError('现有配音片段不属于当前唯一文案段落')
        return {'ordinal': row['ordinal'], 'section': section, 'adopted': adopted,
            'audio': narration_segment_from_row(audio_rows[0]) if audio_rows else None}

    def creation_section_ids(self, task, script, *, offset, limit, connection):
        if not connection.in_transaction:
            raise ValueError('段落读取必须复用已开启的事务')
        start, stop, _ = slice(offset, offset + limit).indices(script.sections_total_count)
        return [row[0] for row in connection.execute(
            'SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? '
            'ORDER BY ordinal LIMIT ? OFFSET ?', (task.workspace_id, script.id, max(0,stop-start), start))]

    def creation_section_neighbors(self, task, script, section_id, *, connection):
        row = connection.execute('SELECT ordinal FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? AND section_id=?',
            (task.workspace_id,script.id,section_id)).fetchone()
        ids = [item[0] for item in connection.execute(
            'SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? '
            'AND ordinal BETWEEN ? AND ? AND section_id<>? ORDER BY ordinal',
            (task.workspace_id,script.id,row[0]-1,row[0]+1,section_id))]
        return [self.read_creation_section_input(task,script,item,connection=connection)['section'] for item in ids]

    def iter_creation_section_planning_inputs(self, task, script, *, connection):
        """Yield effective text once per section, plus scalar clocks; never collect bodies."""
        if not connection.in_transaction:
            raise ValueError('段落规划读取必须复用已开启的事务')
        cursor = connection.execute(
            'SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? ORDER BY ordinal',
            (task.workspace_id, script.id))
        try:
            for row in cursor:
                current = self.read_creation_section_input(task, script, row['section_id'], connection=connection)
                audio, adopted = current['audio'], current['adopted']
                measured = (float(audio.duration_seconds) if audio is not None and audio.status == 'ready'
                    and audio.duration_seconds is not None else 0)
                window = adopted['montage'].get('delivery_window') if adopted is not None else None
                yield current['section'], measured, window
        finally:
            cursor.close()

    def creation_section_titles(self, workspace_id, task_id, section_ids, *, connection=None):
        with self.creation_execution_read(connection) as conn:
            task = require_creation_task(conn, workspace_id, task_id)
            return {row['section_id']: row['title'] for row in conn.execute(
                "SELECT section_id,json_extract(section_json,'$.title') AS title FROM creation_task_script_sections "
                'WHERE workspace_id=? AND script_id=? AND section_id IN (SELECT value FROM json_each(?))',
                (workspace_id, task.confirmed_script_id, json.dumps(section_ids)))}

    def _section_timeline_dependency_inputs(self, conn, task, section_id, adopted):
        from app.creation.section_timeline import SectionDependencyInputs, _ms, _section_bounds
        # SQLite traverses the stored document; only target bodies and global clocks
        # cross into Python. The publication fallback is already section-scoped.
        params = (task.workspace_id, task.current_timeline_version_id)
        present = conn.execute("SELECT EXISTS(SELECT 1 FROM json_each(snapshot_json,'$.montage')) "
            'FROM timeline_versions WHERE workspace_id=? AND id=?', params).fetchone()
        if present and present[0]:
            source = "SELECT json_extract(snapshot_json,'$.montage') AS montage FROM timeline_versions WHERE workspace_id=? AND id=?"
        else:
            source, params = 'SELECT ? AS montage', (json.dumps((adopted or {}).get('montage') or {}),)
        def query(sql, values=()):
            return conn.execute('WITH source AS (' + source + ') ' + sql, (*params, *values))
        meta = json.loads(query("SELECT json_remove(montage,'$.cues','$.clips','$.graphic_overlays') FROM source").fetchone()[0])
        cues, order, durations = [], [], []
        for row in query("SELECT json_extract(j.value,'$.id'),json_extract(j.value,'$.duration_seconds'), "
                "CASE WHEN json_extract(j.value,'$.source_section_id')=? THEN j.value END "
                "FROM source,json_each(source.montage,'$.cues') j ORDER BY CAST(j.key AS INTEGER)", (section_id,)):
            order.append(row[0])
            durations.append(row[1])
            if row[2] is not None:
                cues.append(json.loads(row[2]))
        clocks = [dict(row) for row in query("SELECT j.key AS _position,json_extract(j.value,'$.cue_id') AS cue_id, "
            "json_extract(j.value,'$.timeline_start_seconds') AS timeline_start_seconds, "
            "json_extract(j.value,'$.timeline_end_seconds') AS timeline_end_seconds, "
            "COALESCE(json_extract(j.value,'$.transition_in_seconds'),0) AS transition_in_seconds "
            "FROM source,json_each(source.montage,'$.clips') j ORDER BY CAST(j.key AS INTEGER)")]
        ids = {cue['id'] for cue in cues}
        selected = [clip for clip in clocks if clip['cue_id'] in ids]
        wanted = {clip['_position'] for clip in selected}
        overlays = []
        if selected:
            _, start, end = _section_bounds(order, ids, clocks, meta['duration_seconds'])
            ordered = sorted(clocks, key=lambda clip: clip['timeline_start_seconds'])
            for left, right in zip(ordered, ordered[1:]):
                if right['transition_in_seconds'] and (left['cue_id'] in ids or right['cue_id'] in ids):
                    wanted.update((left['_position'], right['_position']))
            overlay_ids = [row[0] for row in query("SELECT j.key,json_extract(j.value,'$.start_seconds'), "
                "json_extract(j.value,'$.end_seconds') FROM source,json_each(source.montage,'$.graphic_overlays') j "
                "ORDER BY CAST(j.key AS INTEGER)") if _ms(row[1]) < end and _ms(row[2]) > start]
            overlays = [json.loads(row[0]) for row in query("SELECT j.value FROM source, "
                "json_each(source.montage,'$.graphic_overlays') j WHERE j.key IN (SELECT value FROM json_each(?)) "
                "ORDER BY CAST(j.key AS INTEGER)", (json.dumps(overlay_ids),))]
        facts = {row[0]: json.loads(row[1]) for row in query("SELECT j.key,j.value FROM source, "
            "json_each(source.montage,'$.clips') j WHERE j.key IN (SELECT value FROM json_each(?)) "
            "ORDER BY CAST(j.key AS INTEGER)", (json.dumps(sorted(wanted)),))}
        return SectionDependencyInputs(cues, order, clocks, facts, meta.get('duration_seconds'),
            meta.get('audio_timeline'), meta.get('narration_audio_path'),
            sum(durations) if selected and meta.get('audio_timeline') is None and meta.get('narration_audio_path') else 0,
            meta.get('narration_linkage_created_audio_timeline', False) or meta.get('audio_timeline') is None, overlays)

    def section_dependency_snapshot(self,workspace_id,task_id,section_id,*,connection=None):
        """Actual target dependencies, in the owner's snapshot (including uncommitted edits)."""
        # Historically this entry starts a snapshot on a borrowed idle connection.
        # Leave its lifetime/commit to that caller, just as effective_creation_script did.
        if connection is not None and not connection.in_transaction:
            connection.execute('BEGIN')
        with self.creation_execution_read(connection) as conn:
            task = require_creation_task(conn,workspace_id,task_id)
            script, _ = self.script_records.read_execution_script_metadata(conn,workspace_id,task.confirmed_script_id)
            current = self.read_creation_section_input(task,script,section_id,connection=conn)
            section, audio = current['section'], current['audio']
            plan = self.plan_records.read_creation_plan(conn,workspace_id,task.plan_id)
            inputs = self._section_timeline_dependency_inputs(conn,task,section_id,current['adopted'])
            cue_ids = {item['id'] for item in inputs.cues}
            clips = [item for item in inputs.clip_clocks if item['cue_id'] in cue_ids]
            start = min((item['timeline_start_seconds'] for item in clips),default=0)
            end = max((item['timeline_end_seconds'] for item in clips),default=start)
            assets = sorted({inputs.clip_facts[item['_position']]['asset_id'] for item in clips})
            sources = [tuple(row) for row in conn.execute('SELECT id,updated_at FROM material_assets WHERE workspace_id=? '
                'AND id IN (SELECT value FROM json_each(?)) ORDER BY id',(workspace_id,json.dumps(assets)))]
            tracks = []
            if inputs.cues and task.current_timeline_version_id:
                for row in conn.execute("SELECT id,json_remove(payload_json,'$.cues') FROM subtitle_tracks WHERE workspace_id=? AND task_id=? "
                        'AND timeline_version_id=? ORDER BY id',(workspace_id,task_id,task.current_timeline_version_id)):
                    track = json.loads(row[1])
                    relevant_ids = [cue[0] for cue in conn.execute("SELECT j.key,json_extract(j.value,'$.source_cue_id'), "
                        "COALESCE(json_extract(j.value,'$.start_seconds'),0),COALESCE(json_extract(j.value,'$.end_seconds'),0) "
                        "FROM subtitle_tracks t,json_each(t.payload_json,'$.cues') j WHERE t.workspace_id=? AND t.id=? "
                        "ORDER BY CAST(j.key AS INTEGER)",(workspace_id,row[0]))
                        if cue[1] in cue_ids or cue[2]<end and cue[3]>start]
                    relevant = []
                    for value in conn.execute("SELECT j.value FROM subtitle_tracks t,json_each(t.payload_json,'$.cues') j "
                            "WHERE t.workspace_id=? AND t.id=? AND j.key IN (SELECT value FROM json_each(?)) ORDER BY CAST(j.key AS INTEGER)",
                            (workspace_id,row[0],json.dumps(relevant_ids))):
                        cue = json.loads(value[0])
                        relevant.append({**cue,'start_seconds':cue['start_seconds']-start,'end_seconds':cue['end_seconds']-start})
                    if relevant:
                        tracks.append({'language':track.get('language'),'generation_method':track.get('generation_method'),
                            'source_language':track.get('source_language'),'style':track.get('style'),'cues':relevant})
            tracks.sort(key=lambda track:(track['language'],track['generation_method'],track['source_language'] or ''))
            from app.creation.section_timeline import section_dependency_facts
            return {'script_id':script.id,'section':asdict(section),
                'audio':{key:value for key,value in asdict(audio).items() if key not in {'created_at','updated_at'}} if audio else None,
                'voice':asdict(plan.selected_voice) if plan.selected_voice else None,
                'person':asdict(plan.selected_person) if plan.selected_person else None,'aspect_ratio':plan.aspect_ratio,
                'timeline':section_dependency_facts(inputs),'source_versions':sources,'subtitles':tracks}

    def stage_section_version(self, workspace_id, task_id, section_id, *, connection=None):
        if connection is None or not connection.in_transaction:
            with self.creation_execution_read() as reader:
                return self.stage_section_version(workspace_id, task_id, section_id, connection=reader)
        adopted = self.stage_segment_state(workspace_id, task_id, section_id, connection=connection)
        if adopted is not None:
            return adopted['segment_version']
        task = require_creation_task(connection, workspace_id, task_id)
        header, _ = self.script_records.read_execution_script_metadata(connection, workspace_id, task.confirmed_script_id)
        current = self.read_creation_section_input(task, header, section_id, connection=connection)
        section, audio = current['section'], current['audio']
        return hashlib.sha256(json.dumps([header.id, section_id, section.content, audio.audio_reference if audio else None],
            ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

    def stage_segment_state(self, workspace_id, task_id, section_id, *, connection=None):
        if connection is None or not connection.in_transaction:
            with self.creation_execution_read() as reader:
                return self.stage_segment_state(workspace_id, task_id, section_id, connection=reader)
        task = require_creation_task(connection, workspace_id, task_id)
        version = connection.execute(
            "SELECT s.value AS publication_id,i.value AS invalidation_json FROM timeline_versions v "
            "JOIN json_each(v.snapshot_json,'$.stage_segments') s ON s.key=? "
            "LEFT JOIN json_each(v.snapshot_json,'$.stage_segment_invalidations') i ON i.key=s.key "
            'WHERE v.workspace_id=? AND v.task_id=? AND v.id=?',
            (section_id, workspace_id, task_id, task.current_timeline_version_id)).fetchone()
        if version is None or version['publication_id'] is None:
            return None
        reference = version['publication_id']
        row = connection.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=? '
            "AND event_type='segment_publication'", (workspace_id, task_id, reference)).fetchone()
        if row is None:
            raise ValueError('段版本引用的原产物记录不可读取')
        invalidation = json.loads(version['invalidation_json']) if version['invalidation_json'] is not None else None
        invalid = invalidation is not None and invalidation['publication_id'] == reference
        return {'segment_version': reference, **json.loads(row['payload_json']),
            'current_validity': 'invalidated' if invalid else 'current',
            'invalidation_reason': invalidation['reason'] if invalid else None}

    def effective_creation_script(self, workspace_id, task_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            task = require_creation_task(conn, workspace_id, task_id)
            row = conn.execute('SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?',
                (workspace_id, task_id, task.confirmed_script_id)).fetchone()
            if row is None:
                raise ValueError('当前任务没有确认文案')
            script = self.script_records._creation_task_script_from_payload(json.loads(row['payload']), connection=conn)
            sections = []
            for section in script.sections:
                state = self.stage_segment_state(workspace_id, task_id, section.id, connection=conn)
                if state is not None and state['script_id'] != script.id:
                    raise ValueError('采用的段版本不属于当前确认文案')
                sections.append(replace(section, content=state['text']) if state is not None else section)
            return replace(script, sections=sections)

    def iter_section_change_segments(self, task, replacement, *, connection, adopted):
        """Validate the complete set one row at a time; replace only the target."""
        if not connection.in_transaction:
            raise ValueError('局部联动必须复用已开启的事务')
        header, _ = self.script_records.read_execution_script_metadata(
            connection, task.workspace_id, task.confirmed_script_id)
        if (header.task_id != task.id or header.selection_status != 'confirmed'
                or (replacement.workspace_id, replacement.task_id, replacement.script_id) !=
                (task.workspace_id, task.id, header.id)):
            raise ValueError('配音分段与当前确认文案谱系不一致')
        count = connection.execute('SELECT COUNT(*),COUNT(DISTINCT source_section_id) FROM narration_segments '
            'WHERE workspace_id=? AND task_id=? AND script_id=?', (task.workspace_id, task.id, header.id)).fetchone()
        orphan = connection.execute('SELECT 1 FROM narration_segments n WHERE n.workspace_id=? AND n.task_id=? '
            'AND n.script_id=? AND NOT EXISTS (SELECT 1 FROM creation_task_script_sections s WHERE '
            's.workspace_id=n.workspace_id AND s.script_id=n.script_id AND s.section_id=n.source_section_id) LIMIT 1',
            (task.workspace_id, task.id, header.id)).fetchone()
        if not header.sections_total_count or tuple(count) != (header.sections_total_count, header.sections_total_count) or orphan:
            raise ValueError('已持久化的配音分段集不完整或不唯一，不能覆盖')
        target = self.read_creation_section_input(task, header, replacement.source_section_id, connection=connection)['audio']
        if target is None or target.id != replacement.id or target.created_at != replacement.created_at:
            raise ValueError('配音分段稳定 ID 或创建时间不能覆盖')
        if adopted and replacement.updated_at < target.updated_at:
            raise ValueError('不能用过期状态覆盖配音分段')
        recorded = replacement.source_kind == 'recorded' or connection.execute(
            "SELECT 1 FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? "
            "AND source_section_id<>? AND source_kind='recorded' LIMIT 1",
            (task.workspace_id, task.id, header.id, replacement.source_section_id)).fetchone() is not None
        script_digest = None
        if recorded:
            checksum = hashlib.sha256()
            checksum.update(('{"script_id": ' + json.dumps(header.id, ensure_ascii=False) + ', "sections": [').encode())
            for index, section in enumerate(self.script_records.iter_execution_script_sections(connection, task.workspace_id, header.id)):
                if index:
                    checksum.update(b', ')
                checksum.update(json.dumps({'id': section.id, 'content': section.content},
                    ensure_ascii=False, sort_keys=True).encode())
            checksum.update(b']}')
            script_digest = checksum.hexdigest()
        cursor = connection.execute('SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? '
            'ORDER BY created_at ASC,id ASC', (task.workspace_id, task.id, header.id))
        try:
            for row in cursor:
                current = narration_segment_from_row(row)
                segment = replacement if current.source_section_id == replacement.source_section_id else current
                validate_narration_segment(segment)
                section = self.read_creation_section_input(task, header, segment.source_section_id, connection=connection)['section']
                if (adopted or segment.source_section_id != replacement.source_section_id) and segment.text != section.content:
                    raise ValueError('配音分段文字必须与已采用文案段落一致')
                if segment.source_kind == 'recorded' and segment.source_script_digest != script_digest:
                    raise ValueError('电脑录音必须绑定当前确认文案内容')
                yield segment
        finally:
            cursor.close()

    def read_section_change_inputs(self, task, replacement, *, connection):
        """Keep one real final snapshot body plus ordered audio metadata, not full DTO copies."""
        header, _ = self.script_records.read_execution_script_metadata(
            connection, task.workspace_id, task.confirmed_script_id)
        snapshot_rows, audio = [], {}
        for segment in self.iter_section_change_segments(task, replacement, connection=connection, adopted=False):
            snapshot_rows.append(json.loads(json.dumps(asdict(segment), default=str)))
            audio[segment.source_section_id] = {'section_id': segment.source_section_id, 'narration_segment_id': segment.id,
                'audio_reference': segment.audio_reference, 'duration_seconds': segment.duration_seconds, 'status': segment.status}
        ordered = [audio[row[0]] for row in connection.execute('SELECT section_id FROM creation_task_script_sections '
            'WHERE workspace_id=? AND script_id=? ORDER BY ordinal', (task.workspace_id, header.id))]
        return {'script_header': header, 'narration_snapshot': snapshot_rows, 'audio': ordered}

    def iter_narration_edit_segments(self, task, replacement, *, connection):
        """Validate the whole set, then emit the real prospective rows in script order."""
        for _ in self.iter_section_change_segments(task, replacement, connection=connection, adopted=True):
            pass
        header, _ = self.script_records.read_execution_script_metadata(
            connection, task.workspace_id, task.confirmed_script_id)
        for _, segment in self.iter_creation_narration_sections(task, header, connection=connection):
            yield replacement if segment.id == replacement.id else segment
