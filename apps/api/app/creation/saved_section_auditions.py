"""Adopt an already paid, private voice preview as the exact production PCM."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import wave

from app.creation.contracts import TaskEvent
from app.creation.narration_artifacts import (
    AUDITION_REFERENCE, NarrationArtifactRecord, audition_pcm_sha256, audition_source_digest,
)
from app.creation.script_contracts import CreationTaskScriptSection
from app.creation.task_input_reads import require_creation_task
from app.device_resource_configuration import narration_reference_sha256
from app.models import utc_now
from app.narration import _pronunciation_dictionary, _pronunciation_version
from app.narration_resource_preparation import narration_preparation_shape_digest
from app.profiles.contracts import profile_cache_identity, profile_use_failure


_SELECTION = 'creation_narration_audition_selected'
_LANGUAGE = {'zh-CN': 'zh', 'en-US': 'en', 'zh-CN/en-US': 'zh-en'}
_SCOPE_FIELDS = ('input_scope', 'budget_basis', 'preparation_backend_identity',
    'preparation_shape_digest', 'preparation_receipt_sha256', 'write_volumes')


class SavedSectionAuditions:
    def __init__(self, repository, preview_path_for):
        self.repository = repository
        self.preview_path_for = preview_path_for

    def _task(self, conn, workspace_id, task_id, actor):
        task = require_creation_task(conn, workspace_id, task_id)
        if actor.workspace_id != workspace_id or task.owner_user_id != (actor.user_id or actor.member_id):
            raise KeyError(task_id)
        return task

    def _section(self, conn, task, script_id, section_id):
        repo = self.repository
        header, _ = repo.script_records.read_execution_script_metadata(conn, task.workspace_id, script_id)
        if header.task_id != task.id or header.selection_status not in {'draft', 'confirmed'}:
            raise KeyError(section_id)
        if task.confirmed_script_id == header.id and header.selection_status == 'confirmed':
            section = repo.section_inputs.read_creation_section_input(task, header, section_id, connection=conn)
            return header, section['section'], section['audio']
        row = conn.execute('SELECT section_json FROM creation_task_script_sections '
            'WHERE workspace_id=? AND script_id=? AND section_id=?', (task.workspace_id, header.id, section_id)).fetchone()
        if row is None:
            raise KeyError(section_id)
        return header, CreationTaskScriptSection(**json.loads(row['section_json'])), None

    def _voice(self, conn, task, header):
        repo = self.repository
        plan = repo.plan_records.read_creation_plan(conn, task.workspace_id, task.plan_id)
        selection = plan.selected_voice
        if selection is None or plan.language != header.language:
            raise ValueError('请为当前稿选择对应语言的固定声线版本。')
        inputs = repo.profile_inputs.resolve_narration_inputs(
            workspace_id=task.workspace_id, episode_id=task.id,
            voice_profile_id=selection.profile_id, voice_version=selection.version)
        voice = inputs.voice
        language = _LANGUAGE[header.language]
        if (profile_use_failure(voice, content_type='voice', usage_scope='narration')
                or voice.authorization is None or voice.language not in {language, 'zh-en'}
                or (voice.authorization.id, voice.authorization.version, voice.model_id)
                != (selection.authorization_id, selection.authorization_version, selection.model_id)):
            raise PermissionError('当前声线、授权版本或生成模型已经变化。')
        repo.profile_reference_input(voice, viewer_user_id=task.owner_user_id)
        return inputs, language

    def preview_params(self, actor, origin, params):
        ws = actor.workspace_id
        with self.repository.execution_inputs.creation_execution_read() as conn:
            task = self._task(conn, ws, origin['task_id'], actor)
            header, section, _ = self._section(conn, task, origin['script_id'], origin['section_id'])
            inputs, language = self._voice(conn, task, header)
        voice = inputs.voice
        if (params['voice_profile_id'], params['voice_version'], params['language'], params['preview_text'].strip()) != (
                voice.id, voice.version, language, section.content.strip()):
            raise ValueError('试听输入与当前已保存段落、语言或所选声线不一致。')
        return {**params, 'saved_section': origin, 'episode_id': task.id}

    @staticmethod
    def _production_open(conn, task, header):
        if task.stage != 'narration' or task.narration_cache_key or task.current_timeline_version_id:
            return '当前作品已进入生产，段落配音选择已封存。'
        if conn.execute("SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? "
                        "AND kind='creation_task_execution' LIMIT 1", (task.workspace_id, task.id)).fetchone():
            return '原制作作业已经存在，请沿原作业继续。'
        if header.selection_status != 'confirmed' or task.confirmed_script_id != header.id:
            return '请先确认当前文案。'
        return None

    def _selected(self, conn, task, header, section_id, actor, audio):
        row = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND event_type=? '
            "AND json_extract(payload_json,'$.script_id')=? AND json_extract(payload_json,'$.section_id')=? "
            'ORDER BY rowid DESC LIMIT 1', (task.workspace_id, task.id, _SELECTION, header.id, section_id)).fetchone()
        if row is None:
            return None
        payload = json.loads(row['payload_json'])
        if (payload['actor'] != actor.identity_payload() or audio is None or audio.status != 'ready'
                or audio.audio_reference != payload['audio_reference']):
            return None
        return payload['result']['selection']

    def _verify_candidate(self, conn, task, header, section, inputs, language, actor, run):
        from app.profile_preview_execution import voice_preview_original_digest

        voice = inputs.voice
        ws = task.workspace_id
        if run.member_identity != actor.identity_payload():
            raise KeyError(run.id)
        if (run.preview_type != 'voice' or run.status != 'succeeded' or run.finished_at is None
                or not run.device_request_id or not run.output_path):
            raise ValueError('该试听还没有可核验的成功设备回执。')
        if (run.voice_profile_id, run.voice_version, run.model_id, run.language,
                run.authorization_id, run.authorization_version) != (
                voice.id, voice.version, voice.model_id, language, voice.authorization.id, voice.authorization.version):
            raise ValueError('试听的语言、声线、模型或授权版本与当前段落不一致。')
        params = run.preview_input
        if params.get('preview_text', '').strip() != section.content.strip():
            raise ValueError('试听文字与当前文案段落不一致。')
        origin = {'task_id': task.id, 'script_id': header.id, 'section_id': section.id}
        if params.get('saved_section') is not None and params['saved_section'] != origin:
            raise ValueError('原试听属于其他作品、稿件或段落。')
        if conn.execute(
                "SELECT 1 FROM task_events WHERE workspace_id=? AND event_type=? AND json_extract(payload_json,'$.preview_run_id')=? "
                "AND (task_id<>? OR json_extract(payload_json,'$.section_id')<>?) LIMIT 1",
                (ws, _SELECTION, run.id, task.id, section.id)).fetchone():
            raise ValueError('原试听已明确绑定其他作品或段落。')
        latest = conn.execute('SELECT id FROM media_preview_attempts WHERE workspace_id=? AND preview_type=? AND identity=? '
            'ORDER BY attempt DESC LIMIT 1', (ws, 'voice', run.identity)).fetchone()
        if latest is None or latest['id'] != run.id:
            raise ValueError('该试听已被另一尝试替代。')
        request = conn.execute('SELECT owner_type,owner_id,member_id,input_digest,state,checkpoint_ref,resources_json '
            'FROM device_work_requests WHERE workspace_id=? AND id=?', (ws, run.device_request_id)).fetchone()
        if (request is None or (request['owner_type'], request['owner_id'], request['member_id'],
                request['input_digest'], request['state'], request['checkpoint_ref']) != (
                'media_preview', run.id, actor.member_id, run.input_digest, 'done', run.id)):
            raise ValueError('试听缺少匹配的原设备完成回执。')

        repo = self.repository
        scope_id = params.get('episode_id', 'voice_preview')
        if scope_id != (task.id if params.get('saved_section') else 'voice_preview'):
            raise ValueError('试听的原发音词典范围不能用于该作品。')
        original = repo.profile_inputs.resolve_narration_inputs(workspace_id=ws, episode_id=scope_id,
            voice_profile_id=voice.id, voice_version=voice.version)
        dictionary = _pronunciation_dictionary(entries=inputs.pronunciation_entries,
            workspace_id=ws, person_profile_id=voice.person_profile_id, task_id=task.id)
        preview_dictionary = _pronunciation_dictionary(entries=original.pronunciation_entries,
            workspace_id=ws, person_profile_id=voice.person_profile_id, task_id=scope_id)
        if dictionary != preview_dictionary:
            raise ValueError('试听的发音词典版本与当前段落不一致。')
        reference = repo.profile_reference_input(original.voice, viewer_user_id=task.owner_user_id)
        resources = json.loads(request['resources_json'])
        scope = None
        if resources.get('input_scope') == 'single_call_prepared_v1':
            scope = {key: resources[key] for key in _SCOPE_FIELDS}
            actual_shape = narration_preparation_shape_digest(voice_identity=profile_cache_identity(voice),
                reference=asdict(reference), reference_sha256=narration_reference_sha256(reference),
                reference_text=voice.reference_text, language=language, pronunciation_dictionary=dictionary)
            if actual_shape != scope['preparation_shape_digest']:
                raise ValueError('试听原始声音参考已经变化。')
        if run.input_digest != voice_preview_original_digest(run, reference, original, scope):
            raise ValueError('试听原始声线、参考音频或词典凭证不能复核。')
        path = self.preview_path_for(ws, run.identity)
        if Path(run.output_path) != path or path.resolve() != path:
            raise ValueError('试听产物路径与私有原回执不一致。')
        try:
            with path.open('rb') as file:
                stable = os.fstat(file.fileno())
                if run.output_sha256 is None and max(stable.st_ctime_ns, stable.st_mtime_ns) > int(run.finished_at.timestamp() * 10**9):
                    raise ValueError('历史试听文件在原回执完成后发生变化。')
                with wave.open(file, 'rb') as wav:
                    samples, rate = wav.getnframes(), wav.getframerate()
                    if (wav.getcomptype(), wav.getnchannels(), wav.getsampwidth()) != ('NONE', 1, 2) or not samples or not rate:
                        raise ValueError('试听产物不是完整的单声道 PCM WAV。')
                    if len(wav.readframes(samples)) != samples * 2:
                        raise ValueError('试听 PCM 没有完整保存。')
                file.seek(0)
                digest = audition_pcm_sha256(file)
                after = path.stat()
            if (stable.st_dev, stable.st_ino, stable.st_size, stable.st_mtime_ns, stable.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('试听文件在校验期间发生变化。')
        except (wave.Error, EOFError, OSError) as exc:
            raise ValueError('原试听 PCM 缺失或无法读取。') from exc
        if run.output_sha256 is not None and run.output_sha256 != digest:
            raise ValueError('试听文件与原成功回执的校验值不一致。')
        pronunciation = _pronunciation_version(dictionary)
        return ({'preview_run_id': run.id,
            'output_url': f'/api/media/previews/voice/{run.identity}/media?run_id={run.id}&sha256={digest}', 'output_sha256': digest,
            'duration_seconds': samples / rate, 'sample_rate': rate, 'sample_count': samples},
            path, digest, pronunciation, audition_source_digest(voice, reference, pronunciation, language))

    def status(self, workspace_id, task_id, section_id, actor, *, preview_run_id=None):
        repo = self.repository
        with repo.execution_inputs.creation_execution_read() as conn:
            task = self._task(conn, workspace_id, task_id, actor)
            response = {'task_id': task_id, 'script_id': task.confirmed_script_id, 'section_id': section_id,
                'script_updated_at': None, 'available': False, 'reason': None,
                'preview_eligible': False, 'selection': None, 'candidates': []}
            if task.confirmed_script_id is None:
                return {**response, 'reason': '请先确认当前文案。'}
            header, section, audio = self._section(conn, task, task.confirmed_script_id, section_id)
            response['script_updated_at'] = header.updated_at.isoformat()
            selection = self._selected(conn, task, header, section_id, actor, audio)
            response['selection'] = selection
            reason = self._production_open(conn, task, header)
            if reason is None and audio is not None and audio.status == 'ready' and selection is None:
                reason = '当前段已存在正式配音。'
            if reason is not None:
                return {**response, 'reason': reason}
            try:
                inputs, language = self._voice(conn, task, header)
            except KeyError:
                return {**response, 'reason': '当前稿所选声线或参考已不可用。'}
            except (ValueError, PermissionError) as exc:
                return {**response, 'reason': str(exc)}
            response['available'] = True
            runs = repo.preview_records.private_voice_candidates(workspace_id, actor.identity_payload(),
                inputs.voice.id, inputs.voice.version, language, section.content.strip(), connection=conn)
            if preview_run_id and not any(run.id == preview_run_id for run in runs):
                try:
                    runs.append(repo.preview_records.read(workspace_id, preview_run_id, connection=conn))
                except KeyError:
                    pass
            for run in runs:
                try:
                    candidate, *_ = self._verify_candidate(conn, task, header, section, inputs, language, actor, run)
                except (ValueError, KeyError, PermissionError):
                    continue
                response['preview_eligible'] |= run.id == preview_run_id
                response['candidates'].append(candidate)
            if not response['candidates']:
                response['reason'] = '没有与当前稿段和声线匹配的成功试听。'
            return response

    def select(self, workspace_id, task_id, section_id, actor, command):
        from app.creation.narration_stage_use_case import NarrationStageUseCase

        repo = self.repository
        request_id = str(command.request_id)
        event_id = f'event_narration_audition_{request_id}'
        request = command.model_dump(mode='json')
        with repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = self._task(conn, workspace_id, task_id, actor)
            try:
                prior = repo.execution_records.get_task_event(workspace_id, event_id, connection=conn)
            except KeyError:
                prior = None
            if prior is not None:
                if (prior.task_id != task.id or prior.event_type != _SELECTION or prior.payload['actor'] != actor.identity_payload()
                        or prior.payload['section_id'] != section_id or prior.payload['request'] != request):
                    raise ValueError('同一采用请求已绑定其他输入。')
                return prior.payload['result']
            if task.confirmed_script_id != command.expected_script_id:
                raise ValueError('当前确认文案已变化，请重新读取段落。')
            header, section, audio = self._section(conn, task, command.expected_script_id, section_id)
            if command.expected_script_updated_at != header.updated_at:
                raise ValueError('当前确认文案版本已变化，请重新读取段落。')
            selected = self._selected(conn, task, header, section_id, actor, audio)
            if (selected['preview_run_id'] if selected else None) != command.expected_preview_run_id:
                raise ValueError('段落选择已变化，请重新读取。')
            reason = self._production_open(conn, task, header)
            if reason is not None:
                raise ValueError(reason)
            if audio is not None and audio.status == 'ready' and selected is None:
                raise ValueError('不能覆盖已保存的正式段配音。')
            inputs, language = self._voice(conn, task, header)
            voice = inputs.voice
            audio_reference = None
            if command.preview_run_id is None:
                if selected is not None:
                    pending = replace(audio, audio_reference=None, duration_seconds=None, sample_rate=None,
                        status='pending', updated_at=utc_now())
                    repo.narration_records.replace_adopted_narration_segment(task, pending, connection=conn)
                selection = None
            else:
                run = repo.preview_records.read(workspace_id, command.preview_run_id, connection=conn)
                candidate, path, digest, pronunciation, input_digest = self._verify_candidate(
                    conn, task, header, section, inputs, language, actor, run)
                if command.expected_output_sha256 != digest:
                    raise ValueError('当前试听与刚播放的 PCM 不一致，请重新读取后试听。')
                resources = {'model_id': voice.model_id, 'voice_profile_id': voice.id, 'voice_version': voice.version,
                    'language': language, 'pronunciation_version': pronunciation}
                if audio is None:
                    pending = [NarrationStageUseCase.pending_segment(task, header, item, resources) for item, _
                        in repo.section_inputs.iter_creation_narration_sections(task, header, connection=conn)]
                    repo.narration_records.save_narration_segments(workspace_id, task_id, header.id, pending, connection=conn)
                    audio = next(item for item in pending if item.source_section_id == section_id)
                key = 'audition_' + hashlib.sha256(json.dumps([workspace_id, task.id, header.id, section.id, run.id, digest]).encode()).hexdigest()
                artifact = NarrationArtifactRecord(id=key, cache_key=key, workspace_id=workspace_id, task_id=task.id,
                    script_id=header.id, voice_profile_id=voice.id, model_id=voice.model_id, language=language,
                    pronunciation_version=pronunciation, audio_path=str(path), source_artifact_ref=AUDITION_REFERENCE + run.id,
                    source_sha256=digest, audition_input_digest=input_digest,
                    duration_seconds=candidate['duration_seconds'], sample_rate=candidate['sample_rate'])
                audio_reference = repo.narration_artifacts.narration_artifact_reference(key)
                ready = replace(audio, audio_reference=audio_reference,
                    duration_seconds=artifact.duration_seconds, sample_rate=artifact.sample_rate, status='ready',
                    updated_at=utc_now(), voice_profile_id=voice.id, voice_version=voice.version,
                    model_id=voice.model_id, language=language, pronunciation_version=pronunciation)
                repo.narration_checkpoints.commit_creation_narration_segment(artifact, ready, connection=conn)
                selection = {**candidate, 'selection_id': request_id}
            result = {'request_id': request_id, 'task_id': task.id, 'script_id': header.id,
                'script_updated_at': header.updated_at.isoformat(), 'section_id': section.id, 'selection': selection,
                'available': True, 'reason': None, 'preview_eligible': selection is not None,
                'candidates': [candidate] if selection is not None else []}
            repo.execution_records.append_task_event(TaskEvent(event_id, workspace_id, task.id, None, _SELECTION,
                {'actor': actor.identity_payload(), 'script_id': header.id, 'section_id': section.id, 'request': request,
                 'preview_run_id': command.preview_run_id, 'audio_reference': audio_reference, 'result': result}), connection=conn)
            return result
