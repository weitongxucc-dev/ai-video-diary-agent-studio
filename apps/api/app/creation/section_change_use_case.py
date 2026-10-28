"""Compose one section on the current timeline and publish its real subtitle files."""
from copy import deepcopy
from dataclasses import replace
import hashlib

from app.creation.contracts import ArtifactRecord, TransformRun, DependencyEdge
from app.creation.editing.metadata_use_case import execution_subtitle_track
from app.creation.section_timeline import replace_section_montage, retime_section_subtitles
from app.subtitle_tracks import render_srt
from app.models import utc_now
from app.audio_timeline import AudioTimeline,narration_audio_timeline
from app.linked_timeline import splice_audio


class SectionPreparationStale(ValueError):
    """Only the derived file changed; prepare it outside the write transaction again."""



class SectionTimelineChange:
    def __init__(self, repository, artifact_store, *, max_output_bytes=None):
        self.repository, self.artifact_store = repository, artifact_store
        self.prepared = {}
        self.published_bytes = 0
        self.max_output_bytes = max_output_bytes

    @staticmethod
    def local_state(montage, tracks, section_id):
        cues = [deepcopy(cue) for cue in montage['cues'] if cue['source_section_id']==section_id]
        ids = {cue['id'] for cue in cues}
        clips = [deepcopy(clip) for clip in montage['clips'] if clip['cue_id'] in ids]
        start = min(clip['timeline_start_seconds'] for clip in clips)
        end = max(clip['timeline_end_seconds'] for clip in clips)
        local = deepcopy(montage)
        local.update(cues=cues,clips=clips,duration_seconds=round(end-start,3))
        for clip in clips:
            clip['timeline_start_seconds'] = round(clip['timeline_start_seconds']-start,3)
            clip['timeline_end_seconds'] = round(clip['timeline_end_seconds']-start,3)
        local['graphic_overlays'] = [{**deepcopy(item),'start_seconds':round(item['start_seconds']-start,3),
            'end_seconds':round(item['end_seconds']-start,3)} for item in montage.get('graphic_overlays',[])
            if start<=item['start_seconds']<item['end_seconds']<=end]
        graph = montage.get('audio_timeline')
        if graph is None and montage.get('narration_audio_path'):
            graph = narration_audio_timeline(montage['narration_audio_path'],
                sum(cue['duration_seconds'] for cue in montage['cues'])).model_dump()
        local['audio_timeline'] = splice_audio(AudioTimeline.model_validate(graph),
            [(round(start*1000),round(end*1000),0)]).model_dump() if graph is not None else None
        selected = []
        for track in tracks:
            relevant = [replace(cue,start_seconds=round(cue.start_seconds-start,3),end_seconds=round(cue.end_seconds-start,3))
                for cue in track.cues if cue.id in ids or cue.source_cue_id in ids]
            selected.append(replace(track,cues=relevant))
        return local,selected

    def compute(self,task,snapshot,section_id,local,segment,version_id,connection=None,*,desired_version=None):
        if connection is None:
            with self.repository.creation_execution_read() as conn:
                return self.compute(task,snapshot,section_id,local,segment,version_id,conn,desired_version=desired_version)
        inputs = self.repository.read_section_change_inputs(task,segment,connection=connection)
        snapshot = dict(snapshot)
        snapshot['narration_segments'] = inputs['narration_snapshot']
        snapshot['narration_cache_key'] = None
        for key in ('rendered_video_ref','rendered_artifact_id','creation_render_request_id','output_refs','cover_asset_id'):
            snapshot.pop(key,None)
        current = snapshot.get('montage')
        if not isinstance(current,dict):
            return snapshot,[],set()
        script = inputs['script_header']
        audio = inputs['audio']
        if any(row['status']!='ready' or not row['audio_reference'] or not row['duration_seconds'] for row in audio):
            raise ValueError('完整时间线的分段配音尚未就绪，原成果保留')
        tracks = self.repository.list_subtitle_tracks(task.workspace_id,task.id,
            timeline_version_id=task.current_timeline_version_id,connection=connection)
        restore = desired_version is not None and isinstance(desired_version.snapshot.get('montage'),dict)
        old_tracks = []
        if restore:
            old_tracks = self.repository.list_subtitle_tracks(task.workspace_id,task.id,
                timeline_version_id=desired_version.id,connection=connection)
            local,old_tracks = self.local_state(desired_version.snapshot['montage'],old_tracks,section_id)
        snapshot['montage'] = replace_section_montage(current,section_id,local,section_audio=audio,
            **({'restore_audio':True} if restore else {}))
        # Subtitle language belongs to the confirmed script; model locale codes
        # (zh/en) are a separate execution contract.
        language = 'en-US' if script.language=='en-US' else 'zh-CN'
        changed = retime_section_subtitles(tracks,current,section_id,local,version_id,language,
            **({'restore_tracks':old_tracks} if restore else {}))
        cue_ids = {cue['id'] for cue in current['cues'] if cue['source_section_id']==section_id}
        current_by_key = {self.track_key(track):track for track in tracks}
        materializable = set()
        for candidate in old_tracks if restore else tracks:
            key = self.track_key(candidate)
            if candidate.status!='valid' or not restore and key!=(language,'timeline',None):
                continue
            active = current_by_key.get(key)
            # Invalidity has no per-cue scope. It is resolved by a historical
            # replacement only when every known current cue belongs to target.
            if active is not None and active.status!='valid' and (not active.cues or any(
                    cue.id not in cue_ids and cue.source_cue_id not in cue_ids for cue in active.cues)):
                continue
            materializable.add(key)
        return snapshot,changed,materializable

    @staticmethod
    def track_key(track):
        return track.language,track.generation_method,track.source_language

    @staticmethod
    def content(track,languages):
        if (track.artifact_ref is None and SectionTimelineChange.track_key(track) in languages
                and track.status in {'valid','unavailable'}):
            return render_srt(execution_subtitle_track(track)).encode('utf-8')
        return None

    def prepare_tracks(self,task,tracks,languages,job):
        for track in tracks:
            content = self.content(track,languages)
            if content is None:
                continue
            key = hashlib.sha256(content).hexdigest()
            if key not in self.prepared:
                if self.max_output_bytes is not None and self.published_bytes+len(content)>self.max_output_bytes:
                    raise ValueError('实际字幕产物超过本次剩余输出范围，原成果保留')
                self.prepared[key] = self.artifact_store.publish_bytes(
                    f'artifacts/{task.id}/{job.id}/section-subtitles/{key}.srt',content)
                self.published_bytes += len(content)

    def prepare_current(self,workspace_id,task_id,section_id,local,segment,job,*,desired_version=None):
        with self.repository.creation_execution_read() as conn:
            task = self.repository.task_records.get_creation_task(workspace_id,task_id,connection=conn)
            version = self.repository.timeline_records.get_timeline_version(workspace_id,task.current_timeline_version_id,connection=conn)
            _snapshot,tracks,languages = self.compute(task,version.snapshot,section_id,local,segment,
                'section-preparation',conn,desired_version=desired_version)
        self.prepare_tracks(task,tracks,languages,job)

    def restore(self,task,snapshot,section_id,local,segment,version_id,job,connection,*,source_version,desired_version):
        return self.apply(task,snapshot,section_id,local,segment,version_id,job,connection,desired_version=desired_version)

    def __call__(self,task,snapshot,section_id,local,segment,version_id,job,connection):
        return self.apply(task,snapshot,section_id,local,segment,version_id,job,connection)

    def apply(self,task,snapshot,section_id,local,segment,version_id,job,connection,*,desired_version=None):
        snapshot,changed,languages = self.compute(task,snapshot,section_id,local,segment,version_id,connection,
            desired_version=desired_version)
        result = []
        for track in changed:
            track = replace(track, id=f'subtitle_{version_id}_{track.language.lower()}', updated_at=utc_now())
            content = self.content(track,languages)
            if content is not None:
                key = hashlib.sha256(content).hexdigest()
                publication = self.prepared.get(key)
                if publication is None:
                    raise SectionPreparationStale('当前有效字幕内容已变化，需在事务外重新准备真实文件')
                inputs = [f'timeline:{task.current_timeline_version_id}', *[row['audio_reference'] for row in snapshot['narration_segments'] if row['audio_reference']]]
                artifact = ArtifactRecord(id=f'artifact_{version_id}_{track.language}', workspace_id=task.workspace_id,
                    task_id=task.id, kind='subtitle_track', logical_ref=publication.logical_ref,
                    sha256=publication.sha256, size_bytes=publication.size_bytes, producer_job_id=job.id, input_refs=inputs)
                self.repository.execution_records.create_artifact_record(artifact, connection=connection)
                transform = TransformRun(id=f'transform_{artifact.id}', workspace_id=task.workspace_id, task_id=task.id,
                    job_id=job.id, transform_type='subtitle_section_retime', input_digest=key, input_refs=inputs,
                    output_refs=[publication.logical_ref], engine='subtitle-srt/v1', parameters={'section_id': section_id,
                    'timeline_version_id': version_id, 'language': track.language}, status='succeeded',
                    started_at=utc_now(), finished_at=utc_now())
                self.repository.lineage_records.create_transform_run(transform, connection=connection)
                for index, upstream in enumerate(inputs):
                    self.repository.lineage_records.create_dependency_edge(DependencyEdge(id=f'edge_{artifact.id}_{index}',
                        workspace_id=task.workspace_id, task_id=task.id, upstream_ref=upstream,
                        downstream_ref=publication.logical_ref, created_by_transform_id=transform.id), connection=connection)
                track = replace(track, artifact_ref=publication.logical_ref, transform_id=transform.id, status='valid',
                    failure_reason=None, impact=None, repair_action=None)
            result.append(track)
        snapshot['subtitle_track_ids'] = [track.id for track in result]
        return snapshot, result
