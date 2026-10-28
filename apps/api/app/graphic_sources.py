"""Task-owned, immutable local PNG sources for image overlays."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from PIL import Image, ImageOps
from app.models import utc_now
from app.creation.contracts import ArtifactRecord, JobRun, TransformRun
from app.source_policy import knowledge_item_readable as content_readable


def import_graphic_source(repository,store,temporary_root,workspace_id,task_id,upload):
    temporary_root.mkdir(parents=True,exist_ok=True)
    name=Path(upload.filename or '图片').name[:120]
    with TemporaryDirectory(prefix='graphic-import-',dir=temporary_root) as folder:
        raw=Path(folder)/'upload'
        size=0
        digest=hashlib.sha256()
        with raw.open('wb') as handle:
            while chunk:=upload.file.read(1024*1024):
                size+=len(chunk)
                if size>20*1024*1024:
                    raise ValueError('图片不能超过20MB，请先压缩')
                digest.update(chunk)
                handle.write(chunk)
        if not size:
            raise ValueError('图片文件为空')
        with Image.open(raw) as source:
            if source.format not in {'PNG','JPEG','WEBP'} or getattr(source,'n_frames',1)!=1:
                raise ValueError('请选择静态PNG、JPEG或WebP图片；动画和SVG尚未支持')
            if max(source.size)>8192 or source.width*source.height>24_000_000:
                raise ValueError('图片尺寸过大，请缩小到8192边长和2400万像素以内')
            normalized=ImageOps.exif_transpose(source).convert('RGBA')
            normalized.info.clear()
        path=Path(folder)/'source.png'
        normalized.save(path)
        identity=uuid4().hex
        parameters={'name':name,'width':normalized.width,'height':normalized.height,'original_sha256':digest.hexdigest()}
        job=repository.job_records.create_job_run(JobRun(id=f'job_{identity}',workspace_id=workspace_id,task_id=task_id,
            kind='graphic_import',idempotency_key=f'graphic_import:{identity}',input_digest=digest.hexdigest()))
        running=repository.job_records.claim_job_run(workspace_id,job.id)
        try:
            published=store.publish_file(f'artifacts/{task_id}/{job.id}/source.png',path)
            file_stat=store.resolve_file(published.logical_ref).stat()
            parameters['file_revision']={'size_bytes':file_stat.st_size,'mtime_ns':file_stat.st_mtime_ns,'ctime_ns':file_stat.st_ctime_ns}
            record=repository.execution_records.create_artifact_record(ArtifactRecord(id=f'artifact_{identity}',workspace_id=workspace_id,task_id=task_id,
                kind='graphic_source',logical_ref=published.logical_ref,sha256=published.sha256,size_bytes=published.size_bytes,
                producer_job_id=job.id,input_refs=[]))
            repository.lineage_records.create_transform_run(TransformRun(id=f'transform_{identity}',workspace_id=workspace_id,task_id=task_id,job_id=job.id,
                transform_type='graphic_import',input_digest=digest.hexdigest(),input_refs=[],output_refs=[record.logical_ref],engine='local-pillow-v1',
                parameters=parameters,status='succeeded',started_at=running.started_at,finished_at=utc_now()))
            repository.job_records.finish_job_run(workspace_id,job.id,status='succeeded',output_refs=[record.logical_ref])
            return {'source_ref':record.logical_ref,'artifact_id':record.id,**{key:parameters[key] for key in ('name','width','height')}}
        except Exception as exc:
            repository.job_records.finish_job_run(workspace_id,job.id,status='failed',failure_reason=type(exc).__name__,
                impact='当前叠加层和成片没有改变',repair_action='检查外接盘后重新导入图片')
            raise


def _successful_graphic_transform(repository,workspace_id,task_id,record):
    job=repository.job_records.get_job_run(workspace_id,record.producer_job_id)
    if job.task_id!=task_id or job.status!='succeeded' or record.logical_ref not in job.output_refs:
        raise ValueError('图片生产作业尚未成功，不作为可编辑来源')
    transform=next((item for item in repository.lineage_records.list_transform_runs(workspace_id,task_id)
        if item.job_id==job.id and item.status=='succeeded' and item.transform_type in {'graphic_import','media_script'}
        and record.logical_ref in item.output_refs),None)
    if transform is None:
        raise ValueError('图片缺少成功来源记录')
    return transform


def graphic_sources(repository,workspace_id,task_id,store=None):
    records={record.logical_ref:record for record in repository.execution_records.list_artifact_records(workspace_id,task_id) if record.kind=='graphic_source'}
    sources=[]
    for ref,record in records.items():
        try:
            transform=_successful_graphic_transform(repository,workspace_id,task_id,record)
            if store is not None:
                resolve_graphic_source(repository,store,workspace_id,task_id,ref)
        except (KeyError,ValueError,FileNotFoundError):
            continue
        name=transform.parameters.get('name')
        if not isinstance(name,str) or not name.strip():
            name=Path(ref).name
        sources.append({'source_ref':ref,'artifact_id':record.id,'sha256':record.sha256,'size_bytes':record.size_bytes,
            'name':name,**{key:transform.parameters[key] for key in ('width','height')}})
    return sources


def resolve_graphic_source(repository,store,workspace_id,task_id,reference):
    record=next((item for item in repository.execution_records.list_artifact_records(workspace_id,task_id)
        if item.kind=='graphic_source' and item.logical_ref==reference),None)
    if record is None:
        raise ValueError('图片不是当前任务导入的来源，请重新导入或选择当前图片')
    transform=_successful_graphic_transform(repository,workspace_id,task_id,record)
    path=store.resolve_file(record.logical_ref)
    file_stat=path.stat()
    expected=transform.parameters.get('file_revision')
    actual={'size_bytes':file_stat.st_size,'mtime_ns':file_stat.st_mtime_ns,'ctime_ns':file_stat.st_ctime_ns}
    if path.is_symlink() or file_stat.st_size!=record.size_bytes or (expected is not None and actual!=expected):
        raise ValueError('登记图片文件修订已变化')
    if transform.transform_type=='media_script' and expected is None:
        raise ValueError('脚本图片缺少实测文件修订')
    return path


def graphic_source_facts(repository, store, workspace_id, task_id, reference, *, actor):
    """Read registered PNG provenance and stat only; content work needs a grant."""
    from app.creation_execution import CreationExecutionActor
    from app.task_audio import TASK_MEDIA_KIND
    if not isinstance(actor, CreationExecutionActor) or actor.workspace_id != workspace_id:
        raise PermissionError('图片来源缺少当前工作区成员身份')
    task = repository.get_creation_task(workspace_id, task_id)
    principal = actor.user_id or actor.member_id
    if ((task.owner_user_id is not None and task.owner_user_id != principal)
            or not content_readable(user_id=principal, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id)):
        raise PermissionError('当前成员不能读取此任务图片')
    record = next((item for item in repository.execution_records.list_artifact_records(workspace_id, task_id)
                   if item.kind == 'graphic_source' and item.logical_ref == reference), None)
    if record is None:
        raise ValueError('图片不是当前任务登记来源')
    transform = _successful_graphic_transform(repository, workspace_id, task_id, record)
    job = repository.job_records.get_job_run(workspace_id, record.producer_job_id)
    if ((transform.transform_type == 'graphic_import' and job.kind != 'graphic_import')
            or (transform.transform_type == 'media_script' and job.kind != TASK_MEDIA_KIND)):
        raise ValueError('PNG生产作业类型与来源记录不一致')
    if job.kind == TASK_MEDIA_KIND:
        event = repository.execution_records.get_task_event(workspace_id, f'event_{job.id}_start')
        if CreationExecutionActor.from_event(workspace_id, event.payload) != actor:
            raise PermissionError('图片生产作业不属于当前成员')
        if transform.parameters.get('owner_member_id') != principal:
            raise PermissionError('脚本PNG来源归属不属于当前成员')
    width, height = transform.parameters.get('width'), transform.parameters.get('height')
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError('登记图片缺少真实尺寸')
    path = resolve_graphic_source(repository, store, workspace_id, task_id, reference)
    stat = path.stat()
    revision = {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns}
    if path.suffix.lower() != '.png' or transform.parameters.get('file_revision') != revision:
        raise ValueError('图片缺少已登记PNG文件修订')
    return {'source_ref': reference, 'artifact_id': record.id, 'producer_job_id': job.id,
            'transform_id': transform.id, 'sha256': record.sha256, 'width': width, 'height': height,
            'path': str(path), **revision, 'inode': stat.st_ino, 'device': stat.st_dev}
