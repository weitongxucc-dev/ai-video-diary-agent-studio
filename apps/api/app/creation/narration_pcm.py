"""Original-Job PCM work facts; only a sealed result may become narration."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import wave
from contextlib import contextmanager, ExitStack
import fcntl
from dataclasses import asdict, replace

from app.device_scheduler import RecoveryBlocked

EMPTY_PCM_SHA256 = hashlib.sha256(b'').hexdigest()


def pcm_manifest_chunks(fields, segments):
    """The original sorted JSON bytes, retaining only one segment's text.

    PCM keys use json.dumps' default ensure_ascii=True, including its spaces
    and datetime str() encoding. They are not execution_input digest bytes.
    """
    def encoded(value):
        return json.dumps(value, sort_keys=True, default=str).encode()

    yield b'{'
    for index, name in enumerate(sorted((*fields, 'segments'))):
        if index:
            yield b', '
        yield encoded(name) + b': '
        if name != 'segments':
            yield encoded(fields[name])
            continue
        yield b'['
        for ordinal, segment in enumerate(segments):
            if ordinal:
                yield b', '
            yield encoded(asdict(segment))
        yield b']'
    yield b'}'


def pcm_manifest_digest(fields, segments, *, resources=None):
    digest = hashlib.sha256()
    if resources is not None:
        digest.update(b'[')
    for chunk in pcm_manifest_chunks(fields, segments):
        digest.update(chunk)
    if resources is not None:
        digest.update(b', ')
        digest.update(json.dumps(resources, sort_keys=True, default=str).encode())
        digest.update(b']')
    return digest.hexdigest()


def narration_edit_manifest_chunks(fields, script_fields, sections, segments):
    """Encode the original edit manifest, with independent ordered body streams.

    Script sections use their original ordinal; narration rows use their saved
    created_at/id order. These are the legacy default-ASCII JSON bytes, not the
    execution digest encoding or the effective/adopted script projection.
    """
    if 'script' in fields or 'segments' in fields or 'sections' in script_fields:
        raise ValueError('局部配音摘要 metadata 不能携带正文')

    def encoded(value):
        return json.dumps(value, sort_keys=True, default=str).encode()

    def records(values):
        yield b'['
        for index, value in enumerate(values):
            if index:
                yield b', '
            yield encoded(asdict(value))
        yield b']'

    yield b'{'
    for index, name in enumerate(sorted((*fields, 'script', 'segments'))):
        if index:
            yield b', '
        yield encoded(name) + b': '
        if name == 'segments':
            yield from records(segments)
        elif name == 'script':
            yield b'{'
            for ordinal, field in enumerate(sorted((*script_fields, 'sections'))):
                if ordinal:
                    yield b', '
                yield encoded(field) + b': '
                if field == 'sections':
                    yield from records(sections)
                else:
                    yield encoded(script_fields[field])
            yield b'}'
        else:
            yield encoded(fields[name])
    yield b'}'


def narration_edit_manifest_digest(fields, script_fields, sections, segments):
    digest = hashlib.sha256()
    for chunk in narration_edit_manifest_chunks(fields, script_fields, sections, segments):
        digest.update(chunk)
    return digest.hexdigest()


def _source_stat(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def prepare_narration_source(repository, task, plan, actor, *, binding=None, selected_voice=None):
    """Host-owned full-byte evidence, held until the short adoption/recovery commit."""
    from app.creation.execution_input import execution_input_digest, validate_execution_relationships
    with repository.creation_execution_read() as conn:
        snapshot = repository.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
        if (snapshot.task.confirmed_script_id != task.confirmed_script_id or snapshot.plan != plan):
            raise ValueError('文案或方案在来源准备前变化，未封存新输入')
        task, plan = snapshot.task, snapshot.plan
        validate_execution_relationships(task, snapshot.script_header, plan)
        revision = snapshot.input_digest
        if selected_voice is not None:
            # Existing voice replacement prepares a candidate before its guarded
            # adoption; the caller seals the adopted plan in the write transaction.
            plan = replace(plan, selected_voice=selected_voice)
            _, fields = repository.script_records.read_execution_script_metadata(
                conn, task.workspace_id, task.confirmed_script_id)
            revision = execution_input_digest(fields, plan,
                repository.script_records.iter_execution_script_sections(conn, task.workspace_id, task.confirmed_script_id))
        authorization = repository.get_task_authorization(task.workspace_id, task.id, connection=conn)
        goal_revision = repository.task_authorization_goal_revision(task.workspace_id, task.id, connection=conn)
    if task.owner_user_id is not None and task.owner_user_id != (actor.user_id or actor.member_id):
        raise PermissionError('配音来源采用不属于原任务成员')
    inputs, reference, qualified = narration_reference_facts(repository, task, plan)
    fd = os.open(reference.source_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or _source_stat(before) != (
                reference.device, reference.inode, reference.size_bytes, reference.mtime_ns, reference.ctime_ns)):
            raise RecoveryBlocked('配音来源在准备时被替换')
        if Path(reference.source_path).suffix.lower() == '.wav':
            try:
                with os.fdopen(os.dup(fd), 'rb') as file, wave.open(file, 'rb') as audio:
                    if (audio.getcomptype() != 'NONE' or audio.getnchannels() not in {1, 2}
                            or audio.getsampwidth() not in {1, 2, 3, 4} or audio.getframerate() <= 0 or audio.getnframes() <= 0):
                        raise ValueError('配音来源不是有效PCM WAV')
                    remaining_frames = audio.getnframes()
                    while remaining_frames:
                        count = min(remaining_frames, 16384)
                        if len(audio.readframes(count)) != count * audio.getnchannels() * audio.getsampwidth():
                            raise ValueError('配音来源PCM WAV不完整')
                        remaining_frames -= count
            except (wave.Error, EOFError) as exc:
                raise ValueError('配音来源不是完整可读PCM WAV') from exc
            os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            part = os.read(fd, min(remaining, 65536))
            if not part:
                raise RecoveryBlocked('配音来源在完整字节核验期间被截断')
            digest.update(part)
            remaining -= len(part)
        if _source_stat(os.fstat(fd)) != _source_stat(before):
            raise RecoveryBlocked('配音来源在完整字节核验期间变化')
        content = {'workspace_id': task.workspace_id, 'task_id': task.id, 'actor': actor.identity_payload(),
            'voice_id': inputs.voice.id, 'voice_version': inputs.voice.version,
            'source_asset_id': reference.asset_id, 'logical_ref': inputs.voice.reference_audio_path,
            'sha256': digest.hexdigest(), 'size_bytes': before.st_size,
            'segment_id': reference.segment_id, 'start_seconds': reference.start_seconds,
            'end_seconds': reference.end_seconds,
            'goal_revision': goal_revision}
        receipt = repository.voice_sampling.received_source_evidence(inputs.voice)
        if receipt is not None and (receipt['sha256'], receipt['size_bytes']) != (content['sha256'], content['size_bytes']):
            raise RecoveryBlocked('original_source_content_changed: 当前声音与原采样接收字节不一致')
        if binding is not None:
            if any(binding.get(key) != value for key, value in content.items()):
                raise RecoveryBlocked('original_source_content_changed: 原声音内容、版本、范围或目标已变化')
            if (binding['narration_reference']['profile'] != qualified['profile']
                    or binding['narration_reference']['source_asset_digest'] != qualified['source_asset_digest']):
                raise RecoveryBlocked('原配音档案或来源依赖已变化')
        yield {'content': content, 'narration_reference': qualified, 'fd': fd,
            'authorization': authorization, 'sampling_receipt': receipt,
            'adoption_revision_digest': revision,
            'basis': 'prior_sampling_receipt' if receipt is not None else 'current_bytes_at_adoption'}
    finally:
        os.close(fd)


def verify_narration_source(repository, task, plan, prepared, *, connection):
    """Short SQL/FD/path CAS only; full reads and qualification already happened."""
    narration_reference_facts(repository, task, plan, prepared['narration_reference'])
    reference = prepared['narration_reference']['reference']
    if _source_stat(os.fstat(prepared['fd'])) != tuple(reference[key] for key in (
            'device', 'inode', 'size_bytes', 'mtime_ns', 'ctime_ns')):
        raise RecoveryBlocked('配音来源在封存提交前变化')
    if (repository.task_authorization_goal_revision(task.workspace_id, task.id, connection=connection)
            != prepared['content']['goal_revision']
            or repository.get_task_authorization(task.workspace_id, task.id, connection=connection)
            != prepared['authorization']):
        raise RecoveryBlocked('配音目标或任务授权在来源准备后变化')


def source_binding_payload(prepared, *, input_digest, origin_event_id):
    return {'version': 1, **prepared['content'], 'business_input_digest': input_digest,
        'origin_event_id': origin_event_id, 'basis': prepared['basis'],
        'adoption_revision_digest': prepared['adoption_revision_digest'],
        'sampling_receipt': prepared['sampling_receipt'], 'narration_reference': prepared['narration_reference']}


def narration_reference_facts(repository,task,plan,prepared=None):
    """Reuse qualified reference evidence with current SQL facts and file CAS."""
    from app.profile_reference_media import ProfileReferenceInput
    def json_value(value):
        return json.loads(json.dumps(asdict(value),sort_keys=True,default=str))
    def asset_digest(identity):
        return (hashlib.sha256(json.dumps(json_value(repository.get_asset(task.workspace_id,identity)),
            sort_keys=True).encode()).hexdigest() if identity else None)
    if prepared is None:
        inputs=repository.resolve_creation_task_narration_inputs(workspace_id=task.workspace_id,task_id=task.id,
            voice_profile_id=plan.selected_voice.profile_id,voice_version=plan.selected_voice.version)
        reference=repository.profile_reference_input(inputs.voice,viewer_user_id=task.owner_user_id)
        prepared={'profile':json_value(inputs.voice),'reference':asdict(reference),'reader':task.owner_user_id,
            'source_asset_digest':asset_digest(reference.asset_id)}
    else:
        inputs=repository.resolve_narration_inputs(workspace_id=task.workspace_id,episode_id=task.id,
            voice_profile_id=plan.selected_voice.profile_id,voice_version=plan.selected_voice.version)
        reference=ProfileReferenceInput(**prepared['reference'])
        from app.profiles.contracts import profile_use_failure
        from app.source_policy import knowledge_item_readable as content_readable
        current=repository.resolve_narration_inputs(workspace_id=task.workspace_id,episode_id=task.id,
            voice_profile_id=plan.selected_voice.profile_id).voice
        for profile in (inputs.voice,current):
            failure=profile_use_failure(profile,content_type='voice',usage_scope='narration')
            if failure is not None or not content_readable(user_id=task.owner_user_id,local_diagnostic=False,
                    visibility=profile.visibility,owner_user_id=profile.owner_user_id):
                raise RecoveryBlocked('已核验声音参考的使用限制或当前读取权限已变化')
        if (json_value(inputs.voice)!=prepared['profile'] or task.owner_user_id!=prepared['reader']
                or asset_digest(reference.asset_id)!=prepared['source_asset_digest']):
            raise RecoveryBlocked('已核验声音参考的身份、许可或来源权限版本已变化')
        path=repository.resolve_workspace_media_reference(task.workspace_id,inputs.voice.reference_audio_path,
            require_registered=reference.asset_id is None)
        if str(Path(path).resolve())!=reference.source_path:
            raise RecoveryBlocked('已核验声音参考的路径绑定已变化')
        info=os.stat(path)
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=(
                reference.device,reference.inode,reference.size_bytes,reference.mtime_ns,reference.ctime_ns):
            raise RecoveryBlocked('已核验声音参考的文件身份或版本已变化')
    return inputs,reference,prepared


def read_finite_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        links_valid=before.st_nlink==1
        if before.st_nlink==2:
            formal=Path(path)
            pending=formal.with_name('.'+formal.name+'.pending')
            alias=os.stat(pending,follow_symlinks=False)
            links_valid=(stat.S_ISREG(alias.st_mode) and alias.st_nlink==2 and alias.st_size<=16384
                and (alias.st_dev,alias.st_ino)==(before.st_dev,before.st_ino))
        if not stat.S_ISREG(before.st_mode) or not links_valid or before.st_size > 16384:
            raise RecoveryBlocked('原PCM回执不是有限的独占普通文件')
        data = os.read(fd, 16385)
        after = os.fstat(fd)
        if (len(data) != before.st_size or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                != (after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise RecoveryBlocked('原PCM回执在读取时变化')
        return json.loads(data)
    finally:
        os.close(fd)


def source_facts(path):
    path = Path(path)
    info = path.stat()
    with wave.open(str(path), 'rb') as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) != (1, 2, 24000, 'NONE'):
            raise ValueError('配音组装只接受已标定的24000Hz单声道PCM16原片段')
        frames = stream.getnframes()
        if frames <= 0:
            raise ValueError('配音片段没有PCM帧')
    return {'path': str(path), 'size_bytes': info.st_size, 'mtime_ns': info.st_mtime_ns,
        'ctime_ns': info.st_ctime_ns, 'pcm_frames': frames}


def prepare_sources(paths):
    """Read finite WAV headers before taking the business writer."""
    result={}
    for path in dict.fromkeys(str(Path(path).resolve()) for path in paths):
        before=os.stat(path)
        facts=source_facts(path)
        after=os.stat(path)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(
                after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise RecoveryBlocked('PCM来源在格式准备期间变化')
        result[path]={'facts':facts,'identity':{'device':after.st_dev,'inode':after.st_ino}}
    return result


def prepared_source_facts(path, preparation):
    """Only a file identity and metadata CAS; never opens or decodes its WAV."""
    path=str(Path(path).resolve())
    if path not in preparation:
        raise RecoveryBlocked('PCM来源缺少本次动作或原请求的格式准备')
    saved=preparation[path]
    facts,identity=saved['facts'],saved['identity']
    info=os.stat(path)
    if (not stat.S_ISREG(info.st_mode) or identity!={'device':info.st_dev,'inode':info.st_ino}
            or (facts['size_bytes'],facts['mtime_ns'],facts['ctime_ns'])!=(info.st_size,info.st_mtime_ns,info.st_ctime_ns)):
        raise RecoveryBlocked('PCM来源身份或版本在格式准备后变化')
    return dict(facts)


def work_baseline(work, checkpoint, remaining, absolute, source_bytes):
    committed = checkpoint['pcm_bytes'] if checkpoint else 0
    header = 8 if checkpoint else 44
    generated = source_bytes + header
    if generated > remaining or committed + source_bytes + 44 > absolute:
        raise ValueError('原配音累计输出额度不足以追加本次PCM，未重写前缀或重置额度')
    return {'work_path': str(work), 'receipt_path': '',
        'file_identity': checkpoint['file_identity'] if checkpoint else None,
        'committed_pcm_bytes': committed,
        'committed_pcm_sha256': checkpoint['pcm_sha256'] if checkpoint else EMPTY_PCM_SHA256,
        'max_new_bytes': generated, 'header_bytes_allowance': header,
        'absolute_max_output_bytes': absolute}


def prefix_identity(baseline, *, observed_identity=None, verify_prefix=True):
    """Allow only this inode's appended tail; header writes are worker-owned."""
    path = Path(baseline['work_path'])
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if baseline['file_identity'] is None and observed_identity is None:
            return None
        raise RecoveryBlocked('原PCM工作文件已丢失')
    try:
        info = os.fstat(fd)
        identity = {'device': info.st_dev, 'inode': info.st_ino}
        expected = baseline['file_identity'] or observed_identity
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or expected is not None and identity != expected
                or baseline['file_identity'] is not None and info.st_size < baseline['committed_pcm_bytes']+44
                or info.st_size > baseline['absolute_max_output_bytes']):
            raise RecoveryBlocked('原PCM工作文件身份、链接或绝对长度已变化')
        if not verify_prefix:
            return identity
        length = baseline['committed_pcm_bytes']
        digest = hashlib.sha256()
        offset = 44
        while length:
            part = os.pread(fd, min(length, 65536), offset)
            if not part:
                raise RecoveryBlocked('原PCM已提交前缀不完整')
            digest.update(part)
            length -= len(part)
            offset += len(part)
        if digest.hexdigest() != baseline['committed_pcm_sha256']:
            raise RecoveryBlocked('原PCM已提交前缀已变化')
        return identity
    finally:
        os.close(fd)


def read_receipt(path, *, unit_id, job_id, source_digest, baseline, sources, final):
    value = read_finite_json(path)
    added = sum(item['pcm_frames'] * 2 for item in sources)
    expected = {'version': 1, 'unit_id': unit_id, 'job_id': job_id, 'source_digest': source_digest,
        'work_path': baseline['work_path'], 'previous_pcm_bytes': baseline['committed_pcm_bytes'],
        'pcm_bytes': baseline['committed_pcm_bytes'] + added, 'appended_pcm_bytes': added,
        'header_bytes_written': baseline['header_bytes_allowance'],
        'generated_bytes': added + baseline['header_bytes_allowance'],
        'file_size': baseline['committed_pcm_bytes'] + added + 44,
        'source_count': len(sources) + int(baseline['committed_pcm_bytes'] > 0),
        'finalized': final, 'format': {'channels': 1, 'sample_rate': 24000, 'sample_width': 2}}
    if any(value.get(key) != item for key, item in expected.items()):
        raise RecoveryBlocked('原PCM回执与原Job、来源、追加量不一致')
    if baseline['file_identity'] is not None and value['file_identity'] != baseline['file_identity']:
        raise RecoveryBlocked('原PCM回执更换了工作文件')
    fd = os.open(baseline['work_path'],os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        canonical = struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',value['pcm_bytes']+36,b'WAVE',b'fmt ',
            16,1,1,24000,48000,2,16,b'data',value['pcm_bytes'])
        if ({'device':info.st_dev,'inode':info.st_ino} != value['file_identity']
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != value['file_size']
                or info.st_size>baseline['absolute_max_output_bytes']
                or os.pread(fd,44,0) != canonical):
            raise RecoveryBlocked('原PCM头、身份或长度与成功回执不符')
        digest=hashlib.sha256()
        offset,remaining=44,value['pcm_bytes']
        while remaining:
            part=os.pread(fd,min(65536,remaining),offset)
            if not part:
                raise RecoveryBlocked('原PCM封存数据不完整')
            digest.update(part);offset+=len(part);remaining-=len(part)
        after=os.fstat(fd)
        if (digest.hexdigest()!=value['pcm_sha256'] or
                (after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_nlink)!=
                (info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_nlink)):
            raise RecoveryBlocked('原PCM数据摘要或读取期间的文件状态已变化')
    finally:
        os.close(fd)
    if final:
        if read_finite_json(baseline['work_path'] + '.sealed.json') != value:
            raise RecoveryBlocked('最终PCM缺少相同原单元的封存回执')
    return value


@contextmanager
def prepared_receipt(path, **arguments):
    """Hash outside the writer; retain the sealed inode locks through short CAS."""
    baseline=arguments['baseline']
    paths=[str(path),baseline['work_path']]
    if arguments['final']:
        paths.append(baseline['work_path']+'.sealed.json')
    with ExitStack() as stack:
        opened=[]
        def metadata(info):
            return (info.st_dev,info.st_ino,info.st_nlink,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        for name in dict.fromkeys(paths):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW)
            stack.callback(os.close,fd)
            fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
            opened.append((name,fd,metadata(os.fstat(fd))))
        receipt=read_receipt(path,**arguments)
        def verify():
            for name,fd,expected in opened:
                if metadata(os.fstat(fd))!=expected or metadata(os.stat(name,follow_symlinks=False))!=expected:
                    raise RecoveryBlocked('封存PCM准备后的文件身份或元数据已变化')
        verify()
        yield receipt,verify
