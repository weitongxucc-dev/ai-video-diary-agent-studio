"""Task-owned, immutable local PNG sources for image overlays."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from PIL import Image, ImageOps
from app.models import utc_now
from app.production import ArtifactRecord, JobRun, TransformRun


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
        job=repository.create_job_run(JobRun(id=f'job_{identity}',workspace_id=workspace_id,task_id=task_id,
            kind='graphic_import',idempotency_key=f'graphic_import:{identity}',input_digest=digest.hexdigest()))
        running=repository.claim_job_run(workspace_id,job.id)
        try:
            published=store.publish_file(f'artifacts/{task_id}/{job.id}/source.png',path)
            record=repository.create_artifact_record(ArtifactRecord(id=f'artifact_{identity}',workspace_id=workspace_id,task_id=task_id,
                kind='graphic_source',logical_ref=published.logical_ref,sha256=published.sha256,size_bytes=published.size_bytes,
                producer_job_id=job.id,input_refs=[]))
            repository.create_transform_run(TransformRun(id=f'transform_{identity}',workspace_id=workspace_id,task_id=task_id,job_id=job.id,
                transform_type='graphic_import',input_digest=digest.hexdigest(),input_refs=[],output_refs=[record.logical_ref],engine='local-pillow-v1',
                parameters=parameters,status='succeeded',started_at=running.started_at,finished_at=utc_now()))
            repository.finish_job_run(workspace_id,job.id,status='succeeded',output_refs=[record.logical_ref])
            return {'source_ref':record.logical_ref,'artifact_id':record.id,**{key:parameters[key] for key in ('name','width','height')}}
        except Exception as exc:
            repository.finish_job_run(workspace_id,job.id,status='failed',failure_reason=type(exc).__name__,
                impact='当前叠加层和成片没有改变',repair_action='检查外接盘后重新导入图片')
            raise


def graphic_sources(repository,workspace_id,task_id):
    records={record.logical_ref:record for record in repository.list_artifact_records(workspace_id,task_id) if record.kind=='graphic_source'}
    return [{'source_ref':ref,'artifact_id':records[ref].id,**{key:transform.parameters[key] for key in ('name','width','height')}}
        for transform in repository.list_transform_runs(workspace_id,task_id) if transform.transform_type=='graphic_import' and transform.status=='succeeded'
        for ref in transform.output_refs if ref in records]


def resolve_graphic_source(repository,store,workspace_id,task_id,reference):
    record=next((item for item in repository.list_artifact_records(workspace_id,task_id)
        if item.kind=='graphic_source' and item.logical_ref==reference),None)
    if record is None:
        raise ValueError('图片不是当前任务导入的来源，请重新导入或选择当前图片')
    return store.resolve_file(record.logical_ref)
