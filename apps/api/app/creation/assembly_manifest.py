"""Content-bound audio reduction units; only the final unit applies the master limiter."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from app.audio_timeline import AUDIO_MIX_CONTRACT_VERSION, AudioTimeline


AUDIO_ASSEMBLY_VERSION = f'linear-audio-assembly/v2:{AUDIO_MIX_CONTRACT_VERSION}'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class AudioAssemblyUnit:
    id: str
    graph_json: str
    input_refs: tuple[str, ...]
    dependencies: tuple[str, ...]
    master: bool
    normalize_voice_sources: bool

    def output_ref(self, task_id):
        return f'artifacts/{task_id}/assembly/audio/{self.id}.wav'

    @property
    def graph(self):
        return AudioTimeline.model_validate_json(self.graph_json)


@dataclass(frozen=True)
class AudioAssemblyManifest:
    digest: str
    duration_ms: int
    source_hashes: tuple[tuple[str, str], ...]
    units: tuple[AudioAssemblyUnit, ...]
    workspace_id: str
    task_id: str
    producer_job_id: str
    recipe_digest: str
    version: str = AUDIO_ASSEMBLY_VERSION

    @property
    def instance_scope(self):
        return {'workspace_id': self.workspace_id, 'task_id': self.task_id,
            'producer_job_id': self.producer_job_id, 'recipe_digest': self.recipe_digest}

    def validate_scope(self, workspace_id, task_id, producer_job_id):
        if (self.workspace_id, self.task_id, self.producer_job_id) != (workspace_id, task_id, producer_job_id):
            raise PermissionError('组装实例不属于当前工作区、任务与原生产作业')

    @property
    def final(self):
        return self.units[-1]

    def payload(self):
        from dataclasses import asdict
        return asdict(self)

    def validate_unit(self, unit):
        hashes = dict(self.source_hashes)
        identity = {**self.instance_scope, 'version': self.version, 'duration_ms': self.duration_ms,
            'graph': unit.graph_json, 'sources': {ref: hashes[ref] for ref in unit.input_refs if ref in hashes},
            'dependencies': unit.dependencies, 'master': unit.master,
            'normalize_voice_sources': unit.normalize_voice_sources}
        if self.version != AUDIO_ASSEMBLY_VERSION or _digest(identity) != unit.id:
            raise ValueError('音频组装单元内容与封存摘要不一致')

    @classmethod
    def from_payload(cls, data):
        if data['version'] != AUDIO_ASSEMBLY_VERSION:
            raise ValueError('音频组装清单版本不受支持')
        units = tuple(AudioAssemblyUnit(**{**item, 'input_refs': tuple(item['input_refs']),
            'dependencies': tuple(item['dependencies'])}) for item in data['units'])
        result = cls(data['digest'], data['duration_ms'], tuple(tuple(pair) for pair in data['source_hashes']), units,
            data['workspace_id'], data['task_id'], data['producer_job_id'], data['recipe_digest'])
        for unit in units:
            result.validate_unit(unit)
        expected = _digest({**result.instance_scope, 'version': result.version, 'duration_ms': result.duration_ms,
            'sources': result.source_hashes, 'units': [unit.id for unit in units]})
        if expected != result.digest or not units or not units[-1].master or any(unit.master for unit in units[:-1]):
            raise ValueError('音频组装清单摘要或统一master归属无效')
        return result


def plan_audio_assembly(timeline: AudioTimeline, duration_ms: int, *, workspace_id: str, task_id: str, producer_job_id: str,
                        source_hashes: dict[str, str], max_input_count: int,
                        normalize_voice_sources: bool = False) -> AudioAssemblyManifest:
    if type(duration_ms) is not int or duration_ms <= 0 or type(max_input_count) is not int or max_input_count <= 0:
        raise ValueError('音频归约需要正整数时长与有限输入槽位')
    if not all(isinstance(value, str) and value for value in (workspace_id, task_id, producer_job_id)):
        raise ValueError('组装实例缺少原工作区、任务或生产作业')
    snapshot = AudioTimeline.model_validate(timeline.model_dump())
    active = [(track, clip) for track in snapshot.tracks if not track.muted
        for clip in track.clips if not clip.muted]
    if any(track.duck_under_voice for track, _ in active):
        raise ValueError('侧链闪避需要完整人声状态，不能作为线性音轨分组归约')
    if any(clip.timeline_start_ms + clip.duration_ms > duration_ms for _, clip in active):
        raise ValueError('归约音轨超出已批准时间线')
    references = {clip.source_ref for _, clip in active}
    if references != source_hashes.keys() or any(not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value) for value in source_hashes.values()):
        raise ValueError('归约清单需要每个有效原始来源的真实SHA256')
    recipe_digest = _digest({'graph': snapshot.model_dump(), 'sources': sorted(source_hashes.items()),
        'duration_ms': duration_ms, 'normalize_voice_sources': normalize_voice_sources,
        'max_input_count': max_input_count, 'version': AUDIO_ASSEMBLY_VERSION})
    instance_scope = {'workspace_id': workspace_id, 'task_id': task_id, 'producer_job_id': producer_job_id,
        'recipe_digest': recipe_digest}
    units = []

    def add(graph, dependencies=(), *, master=False, normalize=False):
        refs = tuple(dict.fromkeys(clip.source_ref for track in graph.tracks for clip in track.clips))
        graph_json = _json(graph.model_dump())
        identity = {**instance_scope, 'version': AUDIO_ASSEMBLY_VERSION, 'duration_ms': duration_ms,
            'graph': graph_json, 'sources': {ref: source_hashes[ref] for ref in refs if ref in source_hashes},
            'dependencies': dependencies, 'master': master, 'normalize_voice_sources': normalize}
        unit = AudioAssemblyUnit(_digest(identity), graph_json, refs, tuple(dependencies), master, normalize)
        units.append(unit)
        return unit

    def graph_for(items):
        selected = {}
        for track, clip in items:
            selected.setdefault(track.id, (track, []))[1].append(clip)
        return AudioTimeline(tracks=[track.model_copy(update={'clips': clips}) for track, clips in selected.values()])

    # Match the existing declaration: each audible clip consumes one mix input.
    batch, uses = [], 0
    leaves = []
    for track, clip in active:
        cost = 1
        if batch and uses + cost > max_input_count:
            leaves.append(add(graph_for(batch), normalize=normalize_voice_sources))
            batch, uses = [], 0
        batch.append((track, clip))
        uses += cost
    if batch or not active:
        leaves.append(add(graph_for(batch), normalize=normalize_voice_sources))
    if len(leaves) > 1 and max_input_count < 2:
        raise ValueError('多个混音片段的归约至少需要两个输入槽位')

    def intermediate_graph(inputs):
        from app.audio_timeline import TimelineAudioClip, TimelineAudioTrack
        return AudioTimeline(tracks=[TimelineAudioTrack(id='assembly', name='线性浮点中间轨', role='original',
            clips=[TimelineAudioClip(id=unit.id, name='浮点归约片段', source_ref=unit.output_ref(task_id),
                source_start_ms=0, source_end_ms=duration_ms, timeline_start_ms=0) for unit in inputs])])

    level = leaves
    while len(level) > 1:
        level = [add(intermediate_graph(level[start:start + max_input_count]),
            [unit.id for unit in level[start:start + max_input_count]])
            for start in range(0, len(level), max_input_count)]
    add(intermediate_graph(level), [level[0].id], master=True)
    frozen_hashes = tuple(sorted(source_hashes.items()))
    digest = _digest({**instance_scope, 'version': AUDIO_ASSEMBLY_VERSION, 'duration_ms': duration_ms,
        'sources': frozen_hashes, 'units': [unit.id for unit in units]})
    return AudioAssemblyManifest(digest, duration_ms, frozen_hashes, tuple(units), workspace_id, task_id, producer_job_id, recipe_digest)
