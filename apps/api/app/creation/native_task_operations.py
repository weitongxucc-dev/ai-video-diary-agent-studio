"""Task-owned native files, grant-gated commands and immutable artifact registration."""
from __future__ import annotations

import base64
import codecs
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import subprocess
import threading
import time

from app.creation.contracts import ArtifactRecord, DependencyEdge, TaskEvent, TransformRun
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SubmissionIntent, VerifiedWork
from app.models import utc_now

KIND = 'native_task_command'
READ_BYTES = 65536
EDIT_BYTES = 262144
SCAN_BYTES = 1048576


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def file_hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def actual_mime(path):
    """Inspect bytes before selecting a model-readable text or local media path."""
    with path.open('rb') as source:
        head = source.read(READ_BYTES)
    if head.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if head.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if head.startswith((b'GIF87a',b'GIF89a')): return 'image/gif'
    if head[:4]==b'RIFF' and head[8:12]==b'WEBP': return 'image/webp'
    if head[:4]==b'RIFF' and head[8:12]==b'WAVE': return 'audio/wav'
    if head.startswith(b'fLaC'): return 'audio/flac'
    if head.startswith(b'OggS'): return 'audio/ogg'
    if head.startswith(b'ID3'): return 'audio/mpeg'
    if len(head)>12 and head[4:8]==b'ftyp': return 'video/mp4'
    if head.startswith(b'\x1aE\xdf\xa3'): return 'video/webm'
    try:
        text = codecs.getincrementaldecoder('utf-8')().decode(head,final=path.stat().st_size<=len(head))
    except UnicodeDecodeError:
        return 'application/octet-stream'
    return 'text/plain' if not any(ord(char)<32 and char not in '\n\r\t' for char in text) else 'application/octet-stream'


class NativeTaskOperations:
    def __init__(self,repository,store,root,*,budget=None,authorizations=None,python_path=None,node_path=None):
        self.repository,self.store,self.root = repository,store,Path(root).resolve()
        self.budget,self.authorizations = budget,authorizations
        self._completion = threading.Condition()
        self.tools = {'python':python_path or (sys.executable if not getattr(sys,'frozen',False) else None),
            'node':node_path or shutil.which('node'),'ffmpeg':shutil.which('ffmpeg'),'ffprobe':shutil.which('ffprobe')}

    def require_actor(self,actor,task_id):
        task = self.repository.task_records.get_creation_task(actor.workspace_id,task_id)
        if task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError('原生操作不属于当前成员的原任务')
        return task

    def task_root(self,actor,task_id):
        self.require_actor(actor,task_id)
        root = self.root/digest([actor.workspace_id,task_id])
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if root.is_symlink() or root.resolve()!=root.absolute():
            raise PermissionError('任务工作目录不能由符号链接替代')
        for name in ('work','outputs','inputs','control','tmp'):
            (root/name).mkdir(exist_ok=True,mode=0o700)
            if (root/name).is_symlink():
                raise PermissionError('任务目录结构已变化')
        return root

    def path(self,root,value,*,write=False):
        virtual = PurePosixPath(value)
        if not virtual.is_absolute() or virtual.parts[:2]!=('/','task') or '..' in virtual.parts or '\\' in value:
            raise PermissionError('原生文件路径必须位于 /task')
        relative = virtual.parts[2:]
        if not relative or relative[0] not in {'work','outputs','inputs'}:
            raise PermissionError('不能访问任务控制文件')
        if write and relative[0] not in {'work','outputs'}:
            raise PermissionError('原始输入只读')
        target = root.joinpath(*relative)
        if relative[0]=='inputs' and len(relative)==2 and target.is_symlink() and not write:
            catalog = json.loads((root/'control'/'inputs.json').read_text())
            mapped = catalog.get(relative[1])
            if mapped is None or target.resolve()!=Path(mapped['path']):
                raise PermissionError('只读输入映射已变化')
            stat = target.stat()
            if [stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]!=mapped['file_revision']:
                raise ValueError('原始输入版本已变化，须重新核实来源')
            return target.resolve()
        for parent in (target,*target.parents):
            if parent==root:
                break
            if parent.is_symlink():
                raise PermissionError('工作文件不能沿符号链接越界')
        if not target.resolve().is_relative_to(root.resolve()):
            raise PermissionError('文件路径越出原任务')
        return target

    @contextmanager
    def lock(self,root):
        with (root/'control'/'files.lock').open('a+b') as handle:
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('该任务仍有原生命令或文件修改执行中，请查询原执行') from None
            try:
                yield
            finally:
                fcntl.flock(handle,fcntl.LOCK_UN)

    def dispatch(self,operation,arguments,*,actor,origin,write_guard,runtime=None,call_id=None,cancel_event=None,deadline_monotonic=None):
        from app.schemas import (NativeWorkingContextRequest,NativeFileRequest,NativeCommandRequest,
            NativeCommandReadRequest,NativeArtifactRegisterRequest)
        schemas = {'open_task_working_context':NativeWorkingContextRequest,'native_task_file':NativeFileRequest,
            'submit_task_command':NativeCommandRequest,'read_task_command':NativeCommandReadRequest,
            'cancel_task_command':NativeCommandReadRequest,'register_task_artifacts':NativeArtifactRegisterRequest}
        if operation not in schemas:
            raise ValueError('未注册的原生操作')
        arguments = schemas[operation].model_validate(arguments).model_dump(mode='json',exclude_unset=True)
        task_id = origin['task_id']
        self.require_actor(actor,task_id)
        root = self.task_root(actor,task_id)
        if operation=='open_task_working_context':
            if self.authorizations is not None:
                self.authorizations.ensure(self.require_actor(actor,task_id),actor,interactive=True)
            inputs = self.map_inputs(root,arguments.get('input_refs',[]),actor,origin,write_guard)
            return {'task_id':task_id,'root':'/task','work':'/task/work','outputs':'/task/outputs','inputs':inputs,
                'tools':self.tool_facts(),
                'available_inputs':self.repository.native_available_inputs(actor.workspace_id,task_id,
                    offset=arguments.get('available_offset',0),limit=arguments.get('available_limit',30)),
                'capabilities':{'max_read_bytes':READ_BYTES,'max_log_page_bytes':READ_BYTES,'max_editable_file_bytes':EDIT_BYTES,
                    'max_scan_bytes':SCAN_BYTES,'shell_absolute_virtual_paths':False},
                'command_budget':{'status':'ready' if self.budget else 'unavailable',
                    'limits':asdict(self.budget) if self.budget else None}}
        if operation=='native_task_file':
            return self.file_operation(root,arguments,actor=actor,origin=origin,write_guard=write_guard,call_id=call_id)
        if operation in {'submit_task_command','register_task_artifacts'}:
            if runtime is None:
                raise ValueError('原生命令缺少唯一设备运行时')
            return self.submit(operation,arguments,actor=actor,origin=origin,root=root,runtime=runtime,write_guard=write_guard)
        if operation in {'read_task_command','cancel_task_command'}:
            job = self.original_job(actor,origin,arguments)
            if operation=='cancel_task_command':
                self.cancel_job(job,actor,runtime)
            return self.read_job(job,actor,offset=arguments.get('offset',0),limit=arguments.get('limit',READ_BYTES))
        raise ValueError('未注册的原生操作')

    def map_inputs(self,root,inputs,actor,origin,write_guard):
        with self.lock(root):
            if inputs:
                self.require_files_released(actor,origin['task_id'])
            return self._map_inputs(root,inputs,actor,origin,write_guard)

    def require_files_released(self,actor,task_id):
        _jobs,total = self.repository.list_native_jobs(actor.workspace_id,task_id,limit=1,unreleased_only=True,started_only=True)
        if total:
            raise RecoveryBlocked('原通用进程尚未确认释放，不能改写任务文件或输入映射')

    def _map_inputs(self,root,inputs,actor,origin,write_guard):
        catalog_path = root/'control'/'inputs.json'
        catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
        for item in inputs:
            name,reference = item['name'],item['source_ref']
            if Path(name).name!=name or name in {'.','..'} or '\\' in name:
                raise ValueError('输入名必须是一个真实文件名')
            record = self.repository.find_task_artifact_reference(actor.workspace_id,origin['task_id'],reference)
            if record is not None:
                path = self.store.resolve_file(record.logical_ref)
                sha = record.sha256
            else:
                authorization = self.repository.get_task_authorization(actor.workspace_id,origin['task_id'])
                if authorization is None:
                    raise PermissionError('原任务尚未核实素材读取范围')
                path = self.repository.resolve_workspace_media_reference(actor.workspace_id,reference,require_registered=True)
                allowed = self.repository.task_asset_for_path(actor.workspace_id,str(path),authorization.resource_scope.get('assets',[]))
                if not allowed:
                    raise PermissionError('输入文件不在原任务授权素材范围')
                sha = None
            stat = path.stat()
            entry = {'source_ref':reference,'path':str(path.resolve()),'sha256':sha,
                'file_revision':[stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]}
            if name in catalog and catalog[name]!=entry:
                raise ValueError('输入别名已绑定其他文件或版本')
            self.repository.record_native_input(actor,origin,name,entry,write_guard=write_guard)
            target = root/'inputs'/name
            if target.exists() or target.is_symlink():
                if not target.is_symlink() or target.resolve()!=path.resolve():
                    raise PermissionError('输入映射位置已被其他内容占用')
            else:
                target.symlink_to(path.resolve())
            catalog[name]=entry
        if inputs:
            temporary = root/'control'/'inputs.new'
            temporary.write_text(json.dumps(catalog,ensure_ascii=False))
            os.replace(temporary,catalog_path)
        return [{'path':'/task/inputs/'+name,'source_ref':value['source_ref'],'sha256':value['sha256'],
            'revision':digest(value['file_revision']),'size_bytes':value['file_revision'][0]} for name,value in catalog.items()]

    def file_operation(self,root,arguments,*,actor,origin,write_guard,call_id):
        action = arguments['action']
        if action not in {'read','access','write','mkdir'}:
            raise ValueError('文件操作必须为read/access/write/mkdir')
        if action in {'write','mkdir'} and call_id:
            original = self.find_original(actor,origin,call_id,'native_task_file',arguments)
            if original is not None:
                result = self.repository.native_operation_result(actor.workspace_id,original.id)
                if original.status=='succeeded' and result is not None:
                    return result
                if result is not None:
                    raise ValueError(f'原文件操作 {original.id} 已明确失败：'+str(result.get('error')))
                raise RecoveryBlocked(f'原文件操作 {original.id} 结果未知，须读取原记录，不重复写入')
        path = self.path(root,arguments['path'],write=action in {'write','mkdir'})
        exists = path.exists()
        size = path.stat().st_size if exists and path.is_file() else 0
        mime = actual_mime(path) if exists and path.is_file() else 'application/octet-stream'
        revision = file_hash(path) if exists and path.is_file() and size<=EDIT_BYTES else None
        if action=='access':
            return {'exists':exists,'sha256':revision,'size_bytes':size,'mime_type':mime,
                'readable':exists,'writable':path.is_relative_to(root/'work') or path.is_relative_to(root/'outputs')}
        if action=='read':
            if not mime.startswith('text/'):
                raise PermissionError('原始媒体/二进制不通过文字模型读取端口外发，请使用本地分析或已登记预览')
            offset,limit = arguments.get('offset',0),arguments.get('limit',READ_BYTES)
            if type(offset) is not int or offset<0 or type(limit) is not int or not 0<limit<=READ_BYTES:
                raise ValueError('读取窗口越界')
            line_offset,line_limit = arguments.get('line_offset',1),arguments.get('line_limit')
            if type(line_offset) is not int or line_offset<1 or line_limit is not None and (type(line_limit) is not int or line_limit<1):
                raise ValueError('原文件行范围无效')
            with path.open('rb') as source:
                source.seek(offset)
                actual_line = 1 if offset==0 else None
                scanned = 0
                while actual_line is not None and actual_line<line_offset:
                    piece = source.readline(min(SCAN_BYTES-scanned,READ_BYTES))
                    scanned += len(piece)
                    if not piece or scanned>=SCAN_BYTES:
                        raise ValueError('行定位超出本次扫描窗口，请按byte offset继续定位')
                    actual_line += piece.count(b'\n')
                begin = source.tell()
                pieces,used,lines = [],0,0
                while used<limit and (line_limit is None or lines<line_limit):
                    piece = source.readline(limit-used)
                    if not piece:
                        break
                    pieces.append(piece);used+=len(piece);lines+=piece.count(b'\n')
                content = b''.join(pieces)
                next_offset = source.tell()
                try:
                    content.decode('utf-8')
                except UnicodeDecodeError as exc:
                    if exc.reason=='unexpected end of data' and exc.end==len(content):
                        next_offset-=len(content)-exc.start
                        content=content[:exc.start]
                    else:
                        raise PermissionError('当前读取窗口不是可发送给文字模型的UTF-8正文') from exc
            return {'content_base64':base64.b64encode(content).decode(),'sha256':revision,'size_bytes':size,'mime_type':mime,
                'offset':begin,'next_offset':next_offset,'truncated':next_offset<size,'actual_line_offset':actual_line,
                'next_line_offset':actual_line+lines if actual_line is not None else None,
                'file_revision':{'size_bytes':size,'mtime_ns':path.stat().st_mtime_ns}}
        if not call_id:
            raise ValueError('文件写入缺少宿主调用身份')
        job,event = self.repository.create_native_operation(actor,origin,call_id,'native_task_file',arguments,{},write_guard=write_guard)
        if job.status!='queued':
            return self.read_job(job,actor)
        mutated = False
        try:
            with self.lock(root):
                self.require_files_released(actor,origin['task_id'])
                self.repository.job_records.claim_job_run(actor.workspace_id,job.id)
                if action=='mkdir':
                    was_present = path.exists()
                    path.mkdir(parents=True,exist_ok=True,mode=0o700)
                    mutated = not was_present
                    result = {'path':arguments['path'],'exists':True,'work_outputs':[]}
                else:
                    content = base64.b64decode(arguments['content_base64'],validate=True)
                    if len(content)>EDIT_BYTES or size>EDIT_BYTES:
                        raise ValueError('文件超过明确可编辑上限，未拉取或改写整文件')
                    if 'expected_sha256' not in arguments or arguments['expected_sha256']!=revision or exists and revision is None:
                        raise ValueError('文件内容已变化，未覆盖当前文件')
                    path.parent.mkdir(parents=True,exist_ok=True)
                    temporary = root/'control'/f'{job.id}.write'
                    with temporary.open('wb') as target:
                        target.write(content);target.flush();os.fsync(target.fileno())
                    if (file_hash(path) if path.exists() else None)!=arguments['expected_sha256']:
                        raise ValueError('提交前文件版本已变化')
                    os.replace(temporary,path)
                    mutated = True
                    output = {'path':arguments['path'],'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content)}
                    result = {**output,'work_outputs':[output]}
        except Exception as exc:
            if not mutated:
                self.repository.complete_native_operation(job,{'error':str(exc),'submission_effect':'not_submitted',
                    'process_state':'not_started','release_state':'not_acquired'},status='failed')
            raise
        result.update(job_id=job.id,process_state='not_started',release_state='not_acquired')
        self.repository.complete_native_operation(job,result,status='succeeded')
        return {key:value for key,value in result.items() if key!='work_outputs'}

    def resources(self,root,arguments):
        if self.budget is None:
            raise ValueError('native_command_budget_unavailable：通用进程有限范围尚未配置')
        budget = self.budget
        timeout = arguments.get('timeout_seconds',budget.max_timeout_seconds)
        if type(timeout) not in {int,float} or not 0<timeout<=budget.max_timeout_seconds:
            raise ValueError('命令时限必须在已配置的有限单元范围内')
        size,count = 0,0
        for folder in ('work','outputs'):
            for path in (root/folder).rglob('*'):
                if path.is_symlink():
                    size+=path.lstat().st_size;count+=1
                    continue
                if path.is_file():
                    size+=path.stat().st_size;count+=1
                    if count>budget.max_file_count or size>budget.max_input_bytes:
                        raise ValueError('任务工作文件超过通用执行已配置的有限范围')
        catalog = root/'control'/'inputs.json'
        if catalog.exists():
            for name,value in json.loads(catalog.read_text()).items():
                path = self.path(root,'/task/inputs/'+name)
                size+=path.stat().st_size;count+=1
                if count>budget.max_file_count or size>budget.max_input_bytes:
                    raise ValueError('授权输入超过通用执行已配置的有限范围')
        return {'kind':'native_command','execution_mode':'isolated_task_shell','input_bytes':size,
            'file_count':count,'timeout_seconds':timeout,'max_output_bytes':budget.max_output_bytes,
            'max_stdout_bytes':budget.max_stdout_bytes,'max_stderr_bytes':budget.max_stderr_bytes,'threads':budget.max_threads}

    def submit(self,operation,arguments,*,actor,origin,root,runtime,write_guard):
        request_id = arguments['request_id']
        original = self.find_original(actor,origin,request_id,operation,arguments)
        if original is not None:
            if original.status=='queued' and not self.repository.list_creation_job_request_states(actor.workspace_id,original.id):
                runtime.scheduler.submit(SubmissionIntent(actor.workspace_id,'creation_job',original.id,original.input_digest,
                    f'native_command:{original.input_digest}',SchedulePolicy()),actor)
                runtime.wake()
            return self.read_job(original,actor)
        authorization = getattr(runtime,'task_authorizations',None)
        if authorization is not None:
            authorization.ensure(self.require_actor(actor,origin['task_id']),actor,interactive=True)
        resources = self.resources(root,arguments)
        if operation=='submit_task_command':
            if not isinstance(arguments.get('command'),str) or not arguments['command'].strip():
                raise ValueError('命令不能为空')
            self.path(root,arguments.get('cwd','/task/work'),write=True)
            for output in arguments.get('declared_outputs',[]):
                if set(output)!={'path','role'} or not output['role'].strip():
                    raise ValueError('声明输出需要明确的任务路径和用途')
                self.path(root,output['path'],write=True)
        else:
            source = self.repository.job_records.get_job_run(actor.workspace_id,arguments['execution_job_id'])
            if source.kind not in {KIND,'native_task_file'} or source.task_id!=origin['task_id'] or source.status!='succeeded':
                raise ValueError('仅能登记本任务已完成执行的真实文件')
        with self.lock(root):
            catalog = root/'control'/'inputs.json'
            inputs = json.loads(catalog.read_text()) if catalog.exists() else {}
            fixed = {**arguments,'_file_manifest':self.file_manifest(root),'_tools':self.tool_facts(),'_inputs':inputs,
                '_input_refs':[item['source_ref'] for item in inputs.values()]}
            job,event = self.repository.create_native_operation(actor,origin,request_id,operation,fixed,resources,write_guard=write_guard)
        if job.status=='queued':
            runtime.scheduler.submit(SubmissionIntent(actor.workspace_id,'creation_job',job.id,job.input_digest,
                f'native_command:{job.input_digest}',SchedulePolicy()),actor)
            runtime.wake()
        return self.read_job(job,actor)

    def find_original(self,actor,origin,request_id,operation,arguments):
        job = self.repository.job_records.get_job_run_by_idempotency_key(actor.workspace_id,
            f'native:{origin["task_id"]}:{actor.member_id}:{request_id}')
        if job is None:
            return None
        event = self.repository.execution_records.get_task_event(actor.workspace_id,f'event_{job.id}_start').payload
        expected = {key:value for key,value in event['arguments'].items() if not key.startswith('_')}
        if (event['operation']!=operation or expected!=arguments or any(event['origin'].get(key)!=origin.get(key)
                for key in ('task_id','conversation_id','assistant_turn_id','input_id','session_id'))):
            raise ValueError('同一原生请求已绑定其他任务、输入或内容')
        return job

    def original_job(self,actor,origin,arguments):
        job = (self.repository.job_records.get_job_run(actor.workspace_id,arguments['job_id']) if arguments.get('job_id') else
            self.repository.job_records.get_job_run_by_idempotency_key(actor.workspace_id,
                f'native:{origin["task_id"]}:{actor.member_id}:{arguments["request_id"]}'))
        if job is None or job.task_id!=origin['task_id'] or job.kind not in {KIND,'native_task_file'}:
            raise PermissionError('原执行不属于当前任务')
        self.require_actor(actor,job.task_id)
        return job

    def read_job(self,job,actor,*,offset=0,limit=READ_BYTES):
        self.require_actor(actor,job.task_id)
        if type(offset) is not int or offset<0 or type(limit) is not int or not 0<limit<=READ_BYTES:
            raise ValueError('日志读取窗口越界')
        root = self.task_root(actor,job.task_id)
        log = root/'control'/f'{job.id}.jsonl'
        messages,next_offset = [],offset
        if log.exists():
            with log.open('rb') as source:
                source.seek(offset)
                while source.tell()-offset<limit:
                    line = source.readline(READ_BYTES)
                    if not line or not line.endswith(b'\n'):
                        break
                    if source.tell()-offset>limit:
                        break
                    messages.append(json.loads(line));next_offset=source.tell()
        job = self.repository.job_records.get_job_run(job.workspace_id,job.id)
        result = self.repository.native_operation_result(job.workspace_id,job.id) or {}
        requests = self.repository.list_creation_job_request_states(job.workspace_id,job.id)
        return {'job_id':job.id,'request_id':self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start').payload['request_id'],
            'task_id':job.task_id,'status':job.status,'stdout':''.join(item['text'] for item in messages if item['stream']=='stdout'),
            'stderr':''.join(item['text'] for item in messages if item['stream']=='stderr'),'output':''.join(item['text'] for item in messages),
            'offset':offset,'next_offset':next_offset,'truncated':log.exists() and next_offset<log.stat().st_size,
            'exit_code':result.get('exit_code'),'cancelled':result.get('cancelled',False),'timed_out':result.get('timed_out',False),
            'process_state':result.get('process_state','not_started' if job.attempt==0 else 'unknown'),
            'release_state':result.get('release_state','not_acquired' if job.attempt==0 else 'unknown'),
            'artifacts':result.get('artifacts',[]),'work_outputs':self.repository.native_output_window(job.workspace_id,job.id),
            'failure_reason':job.failure_reason,'repair_action':job.repair_action,'submission_effect':result.get('submission_effect'),
            'output_file_sample':result.get('output_file_sample'),
            'waiting_reason':(requests[-1]['wait_reason'] or requests[-1]['last_failure_reason']) if requests else None}

    def verify_submission(self,intent,actor,owner,*,connection=None):
        job = self.repository.job_records.get_job_run(intent.workspace_id,intent.owner_id,connection=connection)
        event = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start',connection=connection).payload
        from app.creation_execution import CreationExecutionActor
        task = self.require_actor(actor,job.task_id)
        if (job.kind!=KIND or job.status not in {'queued','running','interrupted'} or
                CreationExecutionActor.from_event(job.workspace_id,event)!=actor or
                intent.unit_key!=f'native_command:{job.input_digest}' or intent.input_digest!=job.input_digest):
            raise PermissionError('通用命令许可不属于原作业及固定输入')
        if owner.authorization_verdict(task,actor,'task_goal',connection=connection,check_scope=connection is None)!='ready':
            raise PermissionError('原任务执行授权已失效')
        if job.attempt>0 and job.status!='running' and self.repository.native_operation_result(job.workspace_id,job.id,connection=connection) is None:
            raise RecoveryBlocked('原通用命令结果未知，须沿原请求对账，不自动重放')
        return VerifiedWork(job.workspace_id,'creation_job',job.id,job.input_digest,intent.unit_key,actor.member_id,
            'normal_make',event['resources'])

    def capture_local(self,request,conn,owner):
        job = self.repository.job_records.get_job_run(request.workspace_id,request.owner_id,connection=conn)
        event = self.repository.execution_records.get_task_event(request.workspace_id,f'event_{job.id}_start',connection=conn).payload
        from app.creation_execution import CreationExecutionActor
        actor = CreationExecutionActor.from_event(request.workspace_id,event)
        def check(current):
            row = current.execute('SELECT status,input_digest FROM job_runs WHERE workspace_id=? AND id=?',
                (request.workspace_id,request.owner_id)).fetchone()
            if row is None or row['status'] not in {'queued','running','interrupted'} or row['input_digest']!=request.input_digest:
                return 'stale'
            task = self.repository.task_records.get_creation_task(request.workspace_id,job.task_id,connection=current)
            return owner.authorization_verdict(task,actor,'task_goal',connection=current,check_scope=False)
        return check

    def check_current(self,request,conn,owner):
        from app.creation_execution import CreationExecutionActor
        try:
            event = self.repository.execution_records.get_task_event(request.workspace_id,f'event_{request.owner_id}_start',connection=conn).payload
            actor = CreationExecutionActor.from_event(request.workspace_id,event)
            trusted = self.verify_submission(SubmissionIntent(request.workspace_id,'creation_job',request.owner_id,
                request.input_digest,request.unit_key,request.policy),actor,owner,connection=conn)
            return 'ready' if (trusted.resources==request.resources and trusted.member_id==request.member_id
                and trusted.work_class==request.work_class and trusted.source_refs==request.source_refs) else 'stale'
        except PermissionError:
            return 'unauthorized'
        except (KeyError,ValueError,RecoveryBlocked):
            return 'unknown'

    def cancel_job(self,job,actor,runtime):
        for row in self.repository.list_creation_job_request_states(job.workspace_id,job.id):
            if row['state'] in {'waiting','blocked','granted','yield_requested'}:
                runtime.scheduler.cancel(row['id'],actor)
                runtime.registrations['creation_job'].executor.request_cancel(row['id'])
        if job.status=='queued':
            self.repository.complete_native_operation(job,{'cancelled':True,'process_state':'not_started','release_state':'not_acquired'},status='cancelled')

    def tool_facts(self):
        values = {}
        for name,value in self.tools.items():
            if not value or not Path(value).is_file() or not os.access(value,os.X_OK):
                values[name]={'available':False,'path':None}
            else:
                path = Path(value)
                stat = path.stat()
                values[name]={'available':True,'path':str(path.resolve()),'runtime_root':str(path.parent.parent.resolve()),
                    'file_revision':[stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]}
        return values

    def sandbox(self,root,job,tools=None):
        if sys.platform!='darwin' or not Path('/usr/bin/sandbox-exec').is_file():
            raise ValueError('原生OS隔离当前不可用；没有无限制Shell回退')
        reads = [Path('/bin'),Path('/usr/bin'),Path('/usr/lib'),Path('/System/Library'),
            Path('/System/Cryptexes/OS/System/Library/dyld'),
            Path('/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld'),
            Path('/System/Volumes/Preboot/Cryptexes/OS/usr/lib')]
        catalog = root/'control'/'inputs.json'
        input_paths = [Path(item['path']) for item in json.loads(catalog.read_text()).values()] if catalog.exists() else []
        files = []
        seen = set()
        tools = tools or self.tool_facts()
        for name,item in tools.items():
            executable = item['path'] if item['available'] else None
            if executable:
                resolved = Path(executable).resolve()
                reads.append(resolved.parent.parent if resolved.parent.name=='bin' else resolved.parent)
                original = Path(executable)
                if name=='python':
                    reads.append(Path(item['runtime_root']))
                if original.is_symlink():
                    target = original.readlink()
                    target = target if target.is_absolute() else original.parent/target
                    files.append(target)
                    files.extend(parent for parent in target.parents if parent.is_symlink())
                files.extend([Path(executable),resolved])
                pending = [resolved]
                while pending:
                    binary = pending.pop()
                    if binary in seen:
                        continue
                    if len(seen)>=256:
                        raise ValueError('实际动态依赖超过本次有界工具发现范围')
                    seen.add(binary)
                    dependency = subprocess.run(['/usr/bin/otool','-L',str(binary)],capture_output=True,text=True,timeout=5)
                    if dependency.returncode:
                        raise ValueError('本地工具动态依赖不可读取，未启动通用命令')
                    for line in dependency.stdout.splitlines()[1:]:
                        library = line.strip().split(' ',1)[0]
                        if library.startswith('@loader_path/'):
                            library = str(binary.parent/library.removeprefix('@loader_path/'))
                        if library.startswith('/'):
                            path = Path(library);canonical = path.resolve()
                            files.extend([path,canonical,*(parent for parent in path.parents if parent.is_symlink())])
                            if canonical.is_file() and not str(canonical).startswith(('/usr/lib/','/System/')):
                                reads.append(canonical.parent)
                                pending.append(canonical)
        literal = lambda path:'(literal '+json.dumps(str(path),ensure_ascii=False)+')'
        subpath = lambda path:'(subpath '+json.dumps(str(path),ensure_ascii=False)+')'
        profile = '\n'.join(['(version 1)','(deny default)','(deny network*)','(allow process-fork process-exec)',
            '(allow sysctl-read)',
            '(allow file-read-metadata '+ ' '.join(literal(path) for path in {Path('/'),root,*(parent for value in [root,*reads,*files,*input_paths] for parent in value.parents)})+')',
            '(allow file-read* '+ ' '.join(subpath(path) for path in [root,*reads])+ ' '+ ' '.join(literal(path) for path in [Path('/'),*input_paths,*files])+ ' '+literal(Path('/dev/null'))+' '+literal(Path('/dev/urandom'))+')',
            '(allow file-map-executable '+ ' '.join(subpath(path) for path in reads)+' '+ ' '.join(literal(path) for path in files)+')',
            '(allow file-write* '+ ' '.join(subpath(root/name) for name in ('work','outputs','tmp'))+')',
            '(allow file-write* '+literal(Path('/dev/null'))+')'])
        path = root/'control'/f'{job.id}.sb'
        path.write_text(profile)
        return path

    def execute(self,grant,scheduler,executor):
        from app.granted_subprocess import run_granted_command,GrantedCommandCancelled,GrantedCommandTimeout
        from app.creation_execution import CreationExecutionActor
        job = self.repository.job_records.get_job_run(grant.workspace_id,grant.owner_id)
        event = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start').payload
        actor = CreationExecutionActor.from_event(job.workspace_id,event)
        root = self.task_root(actor,job.task_id)
        if job.attempt:
            raise RecoveryBlocked('已开始的通用执行须先对账，不自动重复')
        scheduler.assert_grant(grant)
        job = self.repository.job_records.claim_job_run(job.workspace_id,job.id)
        cancellation = threading.Event()
        executor.register_cancellation(grant.request_id,cancellation)
        resources = event['resources']; started=time.monotonic()
        result = {'exit_code':None,'cancelled':False,'timed_out':False,'process_state':'not_started','release_state':'unknown',
            'published_output_bytes':0,'publication_in_progress':False}
        before = None
        measurements = ExitStack()
        locked = False
        try:
            measurements.enter_context(self.lock(root))
            locked = True
            before = self.file_manifest(root)
            if before!=event['arguments']['_file_manifest']:
                raise ValueError('native_inputs_changed：排队后的工作文件已变化，未执行新的脚本内容')
            catalog = root/'control'/'inputs.json'
            inputs = json.loads(catalog.read_text()) if catalog.exists() else {}
            if inputs!=event['arguments']['_inputs']:
                raise ValueError('native_inputs_changed：排队后的输入映射已变化，未扩大原执行读取范围')
            self.resources(root,event['arguments'])
            for tool in event['arguments']['_tools'].values():
                if tool['available']:
                    stat = Path(tool['path']).stat()
                    if [stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]!=tool['file_revision']:
                        raise ValueError('排队后工具版本已变化，未执行新工具')
            if event['operation']=='register_task_artifacts':
                result['artifacts'] = self.register_files(job,event,root,actor,scheduler,grant,cancellation,started+resources['timeout_seconds'],result)
                result['exit_code']=0
            else:
                profile = self.sandbox(root,job,event['arguments']['_tools'])
                tools = [Path(item['path']).parent for item in event['arguments']['_tools'].values() if item['available']]
                environment = {'PATH':os.pathsep.join(dict.fromkeys([*(str(path) for path in tools),'/usr/bin','/bin','/usr/sbin','/sbin'])),
                    'HOME':str(root/'tmp'),'TMPDIR':str(root/'tmp'),'LANG':'en_US.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
                python = event['arguments']['_tools'].get('python',{})
                if python.get('available'):
                    packages = list((Path(python['runtime_root'])/'lib').glob('python*/site-packages'))
                    if len(packages)==1:
                        environment['PYTHONPATH']=str(packages[0])
                def on_output(stream,data):
                    with (root/'control'/f'{job.id}.jsonl').open('ab') as target:
                        target.write(json.dumps({'stream':stream,'text':data.decode('utf-8',errors='replace')},ensure_ascii=False).encode()+b'\n')
                command = run_granted_command(scheduler,grant,['/usr/bin/sandbox-exec','-f',str(profile),'/bin/bash','--noprofile','--norc','-c',event['arguments']['command']],
                    cwd=self.path(root,event['arguments'].get('cwd','/task/work'),write=True),timeout_seconds=resources['timeout_seconds'],
                    max_stdout_bytes=resources['max_stdout_bytes'],max_stderr_bytes=resources['max_stderr_bytes'],
                    cancel_event=cancellation,env=environment,on_output=on_output,
                    output_paths=(root/'work',root/'outputs',root/'tmp'),max_output_file_bytes=resources['input_bytes']+resources['max_output_bytes'],
                    max_output_files=self.budget.max_file_count)
                result['exit_code']=command.returncode
                result['process_state']='exited'
            after = self.file_manifest(root)
            result['work_outputs'] = [item for path,item in after.items() if path not in before or before[path]['sha256']!=item['sha256']]
            result['declared_outputs'] = []
            for output in event['arguments'].get('declared_outputs',[]):
                proof = after.get(output['path'])
                if proof is None or proof.get('kind')=='symlink' or not proof['size_bytes']:
                    raise ValueError('声明的输出文件未实际生成；没有登记虚构产物')
                result['declared_outputs'].append({**proof,'role':output['role']})
            status='succeeded' if result['exit_code']==0 else 'failed'
        except RecoveryBlocked:
            raise
        except Exception as exc:
            diagnostic = getattr(exc,'command_diagnostics',None)
            process_state = ('exited' if diagnostic and diagnostic.get('returncode') is not None else
                'unknown' if diagnostic else result['process_state'])
            result.update(cancelled=isinstance(exc,GrantedCommandCancelled),timed_out=isinstance(exc,GrantedCommandTimeout),
                process_state=process_state,error=str(exc))
            if diagnostic and diagnostic.get('output_file_sample') is not None:
                result['output_file_sample']=diagnostic['output_file_sample']
            try:
                if not locked or result['process_state'] not in {'exited','not_started'} or before is None or result['publication_in_progress']:
                    raise ValueError('原进程状态或同任务观测锁未确定，保留输出消耗未知')
                after = self.file_manifest(root)
                result['work_outputs'] = [item for path,item in after.items() if before is not None and
                    (path not in before or before[path]['sha256']!=item['sha256'])]
                result['output_bytes'] = sum(item['size_bytes'] for item in result['work_outputs'])+result['published_output_bytes']
            except (OSError,ValueError,PermissionError):
                result['output_bytes'] = None
            status='cancelled' if result['cancelled'] else 'failed'
        finally:
            measurements.close()
            with executor._cancellation_lock:
                executor._cancellations.pop(grant.request_id,None)
        result['elapsed_seconds']=max(0,time.monotonic()-started)
        if cancellation.is_set():
            result['cancelled']=True
            status='cancelled'
        def finish(conn,current):
            self.repository.settle_native_execution(current,elapsed_seconds=result['elapsed_seconds'],
                output_bytes=result.get('output_bytes',sum(item['size_bytes'] for item in result.get('work_outputs',[]))+result['published_output_bytes']),connection=conn)
            return self.repository.complete_native_operation(job,result,status=status,connection=conn)
        if status=='failed':
            scheduler.fail_unit(grant,reason='native_command_failed',failure_write=finish)
        else:
            scheduler.complete_unit(grant,checkpoint_database_path=self.repository.database_path,checkpoint_write=finish,
                cancelled=status=='cancelled')
        self.repository.mark_native_released(job)
        with self._completion:
            self._completion.notify_all()

    def file_manifest(self,root):
        values = {}
        count,total = 0,0
        for folder in ('work','outputs'):
            for path in (root/folder).rglob('*'):
                count+=1
                if count>self.budget.max_file_count:
                    raise ValueError('任务工作目录条目超过本次有限观测范围')
                if path.is_symlink():
                    relative = '/task/'+path.relative_to(root).as_posix()
                    values[relative]={'path':relative,'sha256':digest(os.readlink(path)),'size_bytes':path.lstat().st_size,'kind':'symlink'}
                    continue
                if path.is_file():
                    total+=path.stat().st_size
                    if total>self.budget.max_input_bytes+self.budget.max_output_bytes:
                        raise ValueError('任务工作文件超过本次有界输出观测范围')
                    if len(values)>=self.budget.max_file_count:
                        raise ValueError('任务工作文件数超过有限范围')
                    relative = '/task/'+path.relative_to(root).as_posix()
                    values[relative]={'path':relative,'sha256':file_hash(path),'size_bytes':path.stat().st_size}
        return values

    def cancel_origin(self,actor,binding,runtime,*,deadline_monotonic):
        self.require_actor(actor,binding['task_id'])
        processed = set()
        cursor = None
        condition = runtime.registrations['creation_job'].owner.native_case._completion
        while True:
            jobs,total = self.repository.list_native_jobs(actor.workspace_id,binding['task_id'],limit=100,
                turn_id=binding['turn_id'],session_id=binding.get('session_id'),unreleased_only=True,before=cursor)
            if not total and cursor is None:
                return {'jobs':[],'total_count':0,'truncated':False,'processed_count':len(processed),'cancellation_complete':True}
            new = [job for job in jobs if job.id not in processed]
            for job in new:
                if time.monotonic()>=deadline_monotonic:
                    break
                self.cancel_job(job,actor,runtime);processed.add(job.id)
            if time.monotonic()>=deadline_monotonic:
                break
            if total>len(jobs):
                cursor = (jobs[-1].created_at.isoformat(),jobs[-1].id)
                continue
            cursor = None
            with condition:
                condition.wait(timeout=min(.5,max(0,deadline_monotonic-time.monotonic())))
        jobs,total = self.repository.list_native_jobs(actor.workspace_id,binding['task_id'],limit=100,
            turn_id=binding['turn_id'],session_id=binding.get('session_id'),unreleased_only=True)
        values = []
        for job in jobs:
            value = self.read_job(job,actor)
            values.append({key:value[key] for key in ('job_id','status','process_state','release_state')})
        return {'jobs':values,'total_count':total,'truncated':len(values)<total,'processed_count':len(processed),'cancellation_complete':not total}

    def list_operations(self,actor,task_id,*,offset=0,limit=30):
        self.require_actor(actor,task_id)
        jobs,total = self.repository.list_native_jobs(actor.workspace_id,task_id,offset=offset,limit=limit)
        values = []
        for job in jobs:
            event = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{job.id}_start').payload
            result = self.repository.native_operation_result(job.workspace_id,job.id) or {}
            requests = self.repository.list_creation_job_request_states(job.workspace_id,job.id)
            values.append({'job_id':job.id,'request_id':event['request_id'],'operation':event['operation'],'status':job.status,
                'process_state':result.get('process_state','not_started' if not job.attempt else 'unknown'),
                'release_state':result.get('release_state','not_acquired' if not job.attempt else 'unknown'),
                'artifacts':result.get('artifacts',[]),'failure_reason':job.failure_reason,'repair_action':job.repair_action,
                'waiting_reason':(requests[-1]['wait_reason'] or requests[-1]['last_failure_reason']) if requests else None})
        return {'task_id':task_id,'items':values,'offset':offset,'total_count':total,'truncated':offset+len(values)<total}

    def artifact_path(self,actor,task_id,artifact_id):
        self.require_actor(actor,task_id)
        artifact = self.repository.execution_records.get_artifact_record(actor.workspace_id,artifact_id)
        if artifact.task_id!=task_id:
            raise PermissionError('产物不属于原任务')
        job = self.repository.job_records.get_job_run(actor.workspace_id,artifact.producer_job_id)
        if job.kind!=KIND or job.status!='succeeded' or artifact.logical_ref not in job.output_refs:
            raise ValueError('原产物尚未完成登记')
        actual = self.store.describe(artifact.logical_ref)
        if (actual.sha256,actual.size_bytes)!=(artifact.sha256,artifact.size_bytes):
            raise ValueError('登记产物内容已变化')
        return self.store.resolve_file(artifact.logical_ref)

    def record_stopped(self,grant,scheduler,exc):
        def stopped(conn,current):
            job = self.repository.job_records.get_job_run(current.workspace_id,current.owner_id,connection=conn)
            if job.status=='running':
                self.repository.job_records.finish_job_run(job.workspace_id,job.id,status='interrupted',failure_reason=str(exc),
                    impact='通用执行停止与消耗尚待对账，原采用成果保留',repair_action='核对原进程、文件和许可后恢复',connection=conn)
        scheduler.record_owner_stop(grant,owner_database_path=self.repository.database_path,owner_write=stopped)

    def register_files(self,job,event,root,actor,scheduler,grant,cancellation,deadline,process_result):
        from app.granted_subprocess import run_granted_command
        arguments=event['arguments']; values=[]
        for index,item in enumerate(arguments['files']):
            path = self.path(root,item['path'])
            source_output = self.repository.native_output_for_path(job.workspace_id,arguments['execution_job_id'],item['path'])
            if source_output is None or source_output['sha256']!=item['expected_sha256']:
                raise ValueError('此文件及摘要不是所指原执行记录的实际输出')
            if not path.is_file() or path.stat().st_size<=0 or path.stat().st_size>event['resources']['max_output_bytes']:
                raise ValueError('登记文件为空或超过本次有限范围')
            sha = file_hash(path)
            if sha!=item['expected_sha256']:
                raise ValueError('登记内容与所见文件摘要不同')
            mime = actual_mime(path)
            extra = {}
            if mime.startswith('image/'):
                from PIL import Image
                with Image.open(path) as image:
                    if max(image.size)>8192:
                        raise ValueError('图片超过现有图层来源的尺寸范围')
                    extra = {'width':image.width,'height':image.height}
                    image.verify()
                kind='graphic_source'
            elif mime.startswith(('video/','audio/')):
                ffprobe = event['arguments']['_tools'].get('ffprobe',{}).get('path')
                if not ffprobe:
                    raise ValueError('媒体格式核验工具不可用')
                profile = self.sandbox(root,job,event['arguments']['_tools'])
                measured = run_granted_command(scheduler,grant,['/usr/bin/sandbox-exec','-f',str(profile),ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(path)],
                    timeout_seconds=max(.001,deadline-time.monotonic()),max_stdout_bytes=READ_BYTES,max_stderr_bytes=READ_BYTES,cancel_event=cancellation)
                process_result['process_state']='exited'
                facts=json.loads(measured.stdout)
                if measured.returncode or not facts.get('streams'):
                    raise ValueError('文件没有可核对的真实媒体流')
                kind='native_video' if mime.startswith('video/') else 'native_audio'
            elif mime.startswith('text/'):
                with path.open('rb') as source:
                    source.read(READ_BYTES).decode('utf-8')
                kind='native_document'
            else:
                raise ValueError('此实际文件格式尚无可验证的登记入口')
            process_result['publication_in_progress']=True
            published = self.store.publish_file(f'artifacts/{job.task_id}/{job.id}/{index}_{sha}{path.suffix.lower()}',path)
            process_result['publication_in_progress']=False
            process_result['published_output_bytes']+=published.size_bytes
            if published.sha256!=sha or published.size_bytes!=source_output['size_bytes']:
                raise ValueError('登记期间工作文件已变化，未采用新内容')
            source_job = self.repository.job_records.get_job_run(job.workspace_id,arguments['execution_job_id'])
            source_event = self.repository.execution_records.get_task_event(job.workspace_id,f'event_{source_job.id}_start').payload
            input_refs = [*source_job.output_refs,*source_event['arguments'].get('_input_refs',[])]
            artifact = ArtifactRecord(id=f'artifact_{job.id}_{index}',workspace_id=job.workspace_id,task_id=job.task_id,
                kind=kind,logical_ref=published.logical_ref,sha256=published.sha256,size_bytes=published.size_bytes,
                producer_job_id=job.id,input_refs=input_refs,created_at=job.created_at)
            now=utc_now()
            transform = TransformRun(id=f'transform_{job.id}_{index}',workspace_id=job.workspace_id,task_id=job.task_id,
                job_id=job.id,transform_type='graphic_import' if kind=='graphic_source' else 'native_artifact_registration',
                input_digest=job.input_digest,input_refs=input_refs,output_refs=[artifact.logical_ref],engine='native_task_command',
                parameters={'source_execution_job_id':source_job.id,'source_input_digest':source_job.input_digest,'owner_member_id':actor.user_id or actor.member_id,
                    'mime_type':mime,'role':item['role'],**extra},status='succeeded',started_at=job.created_at,finished_at=now)
            self.repository.record_task_media_publication(artifact,transform,[])
            values.append({'artifact_id':artifact.id,'source_ref':artifact.logical_ref,'logical_ref':artifact.logical_ref,
                'sha256':artifact.sha256,'size_bytes':artifact.size_bytes,'media_type':mime,'role':item['role'],
                'preview_url':f'/api/creation-tasks/{job.task_id}/native-artifacts/{artifact.id}/content','adopted':False})
        return values


class NativeOperationHandler:
    """Trusted turn adapter shared by the ordinary UI turn and native lifecycle cancellation."""
    def __init__(self,case,actor,verify_binding,runtime,delegate=None):
        self.case,self.actor,self.verify_binding,self.runtime = case,actor,verify_binding,runtime
        self.delegate = delegate

    def __call__(self,operation,arguments,*,call_id,tool_call_id,source_binding,cancel_event,deadline_monotonic):
        entered = False
        try:
            if source_binding.get('node_executable'):
                self.case.tools['node']=source_binding['node_executable']
            if self.delegate is not None:
                entered = True
                return self.delegate(operation,arguments,call_id=call_id,tool_call_id=tool_call_id,source_binding=source_binding,
                    cancel_event=cancel_event,deadline_monotonic=deadline_monotonic)
            if cancel_event.is_set():
                raise PermissionError('当前原生调用已取消，未开始新操作')
            origin = self.verify_binding(source_binding,None)
            def guard(conn):
                if cancel_event.is_set():
                    raise PermissionError('提交时原生调用已取消')
                self.verify_binding(source_binding,conn)
            entered = True
            value = self.case.dispatch(operation,arguments,actor=self.actor,origin=origin,write_guard=guard,
                runtime=self.runtime() if operation in {'submit_task_command','register_task_artifacts','cancel_task_command'} else None,
                call_id=call_id,cancel_event=cancel_event,deadline_monotonic=deadline_monotonic)
            return {'ok':True,'value':value}
        except (KeyError,ValueError,PermissionError,RecoveryBlocked) as exc:
            return {'ok':False,'failure':{'status_code':403 if isinstance(exc,PermissionError) else 409,'reason':str(exc),
                'submission_effect':'unknown' if entered else 'not_submitted'}}

    def cancel(self,*,source_binding,deadline_monotonic):
        return self.case.cancel_origin(self.actor,source_binding,self.runtime(),deadline_monotonic=deadline_monotonic)
