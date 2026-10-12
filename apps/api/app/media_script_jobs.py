"""Single-PNG task media adapter; scheduling remains owned by TaskMediaJobs."""
import json
import os
from pathlib import Path
import re
import stat
import sys
from tempfile import TemporaryDirectory
from PIL import Image

from app.media_script import (
    MediaScriptError, PngOutputContract, ScriptExecutionLimits, ScriptInputRevision,
    ScriptToolchain, MeasuredScriptOutput, prepare_media_script, run_media_script, seal_media_script,
)
from app.graphic_sources import resolve_graphic_source
from app.models import utc_now
from app.production import ArtifactRecord, DependencyEdge, TaskEvent, TransformRun
from app.task_audio import TASK_MEDIA_CONTEXT, media_digest


def read_script_toolchain(model_root: Path) -> ScriptToolchain:
    if sys.platform != "darwin":
        raise MediaScriptError("仅当前macOS隔离运行时支持媒体脚本")
    path = model_root / "media-script-runtime.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16384:
                raise MediaScriptError("运行时描述必须是有界普通文件")
            payload = json.loads(source.read(16385))
    except (OSError, ValueError) as exc:
        raise MediaScriptError("受校准的脚本运行时描述不可用") from exc
    names = {"python", "stdlib", "python_library", "pillow", "version", "digest"}
    if (not isinstance(payload, dict) or set(payload) != names
            or not isinstance(payload["version"], str) or not 1 <= len(payload["version"]) <= 128
            or not isinstance(payload["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", payload["digest"])):
        raise MediaScriptError("脚本运行时描述字段不可核验")
    paths = {}
    for name in ("python", "stdlib", "python_library", "pillow"):
        value = payload[name]
        if not isinstance(value, str) or not value or "\x00" in value or not Path(value).is_absolute():
            raise MediaScriptError("脚本运行时描述路径不可核验")
        paths[name] = Path(value)
        if paths[name].is_symlink() or not (paths[name].is_dir() if name in {"stdlib", "pillow"} else paths[name].is_file()):
            raise MediaScriptError("受校准运行时文件不可用")
    return ScriptToolchain(**paths, version=payload["version"], digest=payload["digest"])


def script_limits(budget):
    return ScriptExecutionLimits(budget.max_timeout_seconds, budget.max_stdout_bytes,
                                 budget.max_stderr_bytes, budget.max_input_bytes)


def script_inputs(repository, store, task, payload):
    inputs, revisions, facts = {}, [], []
    for item in payload.input_refs:
        record = repository.get_artifact_record(task.workspace_id, item.expected_artifact_id)
        if (record.task_id != task.id or record.kind != "graphic_source" or record.logical_ref != item.source_ref
                or record.sha256 != item.expected_sha256):
            raise MediaScriptError("输入图片不属于当前任务或登记修订已变化")
        path = resolve_graphic_source(repository, store, task.workspace_id, task.id, item.source_ref)
        if path.suffix != ".png" or path.is_symlink():
            raise MediaScriptError("首包只支持当前任务登记PNG来源")
        metadata = path.stat()
        fact = {"name": item.name, "reference": record.logical_ref, "artifact_id": record.id,
            "sha256": record.sha256, "size_bytes": metadata.st_size, "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns, "inode": metadata.st_ino, "device": metadata.st_dev}
        revisions.append(ScriptInputRevision(item.name, media_digest(fact), record.sha256))
        inputs[item.name] = path
        facts.append(fact)
    return inputs, tuple(revisions), facts


def seal_script(repository, store, task, payload, toolchain):
    paths, revisions, facts = script_inputs(repository, store, task, payload)
    outputs = tuple(PngOutputContract(item.name, item.max_bytes, item.width, item.height) for item in payload.requested_outputs)
    sealed = seal_media_script(script_source=payload.script_source, language=payload.language,
        parameters=payload.parameters, input_revisions=revisions, requested_outputs=outputs, toolchain=toolchain)
    return sealed, paths, facts


def script_definition(sealed, payload):
    definition = {"script_source": sealed.script_source, "language": sealed.language,
        "parameters": json.loads(sealed.parameters_json),
        "input_slots": [{"name": item.name, "kind": "graphic_source_png"} for item in sealed.input_revisions],
        "requested_outputs": [item.model_dump(mode="json") for item in payload.requested_outputs],
        "purpose": payload.purpose, "script_digest": sealed.digest,
        "toolchain": {"version": sealed.toolchain.version, "digest": sealed.toolchain.digest}}
    encoded = json.dumps(definition, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(encoded) > 256 * 1024:
        raise MediaScriptError("成功程序定义超过有界元数据大小")
    return encoded


def execute_script(repository, store, temporary_root, task, payload, toolchain):
    active = TASK_MEDIA_CONTEXT.get()
    if active is None:
        raise MediaScriptError("脚本执行缺少原作业许可")
    context, job, event, actor = active
    context.check()
    sealed, inputs, _facts = seal_script(repository, store, task, payload, toolchain)
    if sealed.digest != event["input_facts"]["script_digest"]:
        raise MediaScriptError("封存脚本或输入修订发生变化")
    previous = [item for item in repository.list_artifact_records(task.workspace_id, task.id)
                if item.producer_job_id == job.id and item.kind == "graphic_source"]
    if previous:
        if len(previous) != 1:
            raise MediaScriptError("原作业出现不确定的图片登记，不能重执行")
        record, = previous
        started = repository.get_task_event(task.workspace_id, f"event_{job.id}_script_started")
        contract, = sealed.requested_outputs
        if (started.payload["script_digest"] != sealed.digest
                or record.logical_ref != f"artifacts/{task.id}/{job.id}/{contract.name}"):
            raise MediaScriptError("已有发布图片不属于原封存脚本")
        context.check()
        published = store.describe(record.logical_ref)
        if (published.sha256, published.size_bytes) != (record.sha256, record.size_bytes):
            raise MediaScriptError("已有发布图片发生变化，不能恢复元数据或重执行")
        path = store.resolve_file(record.logical_ref)
        if path.is_symlink() or path.stat().st_nlink != 1 or record.size_bytes > contract.max_bytes:
            raise MediaScriptError("已有PNG发布文件不可验证")
        with Image.open(path) as image:
            if (image.format != "PNG" or getattr(image, "n_frames", 1) != 1
                    or not (0 < image.width <= contract.max_width and 0 < image.height <= contract.max_height)):
                raise MediaScriptError("已有发布图片不能通过原输出契约")
            image.load()
            measured = MeasuredScriptOutput(contract.name, record.sha256, record.size_bytes, image.width, image.height)
        context.check()
    else:
        try:
            repository.get_task_event(task.workspace_id, f"event_{job.id}_script_started")
        except KeyError:
            repository.append_task_event(TaskEvent(id=f"event_{job.id}_script_started", workspace_id=task.workspace_id,
                task_id=task.id, job_id=job.id, event_type="media_script_started",
                payload={"script_digest": sealed.digest}, created_at=job.created_at))
        else:
            raise MediaScriptError("原脚本执行结果未知，不自动重复执行，请查询原作业")
        resources = event["resources"]
        limits = ScriptExecutionLimits(resources["timeout_seconds"], resources["max_stdout_bytes"], resources["max_stderr_bytes"], resources["input_bytes"])
        temporary_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="media-script-", dir=temporary_root) as folder:
            prepared = prepare_media_script(sealed=sealed, input_paths=inputs, job_directory=Path(folder) / "attempt", limits=limits)
            context.check()
            measured, = run_media_script(prepared=prepared, scheduler=context.scheduler, grant=context.grant, cancel_event=context.cancel_event)
            context.check()
            path = prepared.directory / "out" / measured.name
            published = store.publish_file(f"artifacts/{task.id}/{job.id}/{measured.name}", path)
            if (published.sha256, published.size_bytes) != (measured.sha256, measured.bytes):
                raise MediaScriptError("发布图片与Leaf实测结果不一致")
    input_refs = [item.source_ref for item in payload.input_refs]
    if not previous:
        record = repository.create_artifact_record(ArtifactRecord(id=f"artifact_{job.id}_graphic", workspace_id=task.workspace_id,
            task_id=task.id, kind="graphic_source", logical_ref=published.logical_ref, sha256=published.sha256,
            size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=input_refs, created_at=job.created_at))
    definition = script_definition(sealed, payload)
    if len(definition) != event["resources"]["definition_bytes"]:
        raise MediaScriptError("成功程序定义与封存元数据预算不一致")
    published_definition = store.publish_bytes(f"artifacts/{task.id}/{job.id}/definition.json", definition)
    definition_record = repository.create_artifact_record(ArtifactRecord(id=f"artifact_{job.id}_definition", workspace_id=task.workspace_id,
        task_id=task.id, kind="media_script_definition", logical_ref=published_definition.logical_ref,
        sha256=published_definition.sha256, size_bytes=published_definition.size_bytes, producer_job_id=job.id, input_refs=input_refs,
        created_at=job.created_at))
    metadata = store.resolve_file(record.logical_ref).stat()
    transform_parameters = {"name": measured.name, "width": measured.width, "height": measured.height,
        "script_digest": sealed.digest, "toolchain_digest": toolchain.digest,
        "definition_ref": definition_record.logical_ref, "owner_member_id": actor.user_id or actor.member_id,
        "file_revision": {"size_bytes": metadata.st_size, "mtime_ns": metadata.st_mtime_ns, "ctime_ns": metadata.st_ctime_ns}}
    transform = next((item for item in repository.list_transform_runs(task.workspace_id, task.id)
                      if item.id == f"transform_{job.id}_script"), None)
    if transform is None:
        transform = repository.create_transform_run(TransformRun(id=f"transform_{job.id}_script", workspace_id=task.workspace_id,
        task_id=task.id, job_id=job.id, transform_type="media_script", input_digest=sealed.digest,
        input_refs=input_refs, output_refs=[record.logical_ref, definition_record.logical_ref], engine=f"python-pillow:{toolchain.version}",
        parameters=transform_parameters,
        status="succeeded", started_at=job.started_at, finished_at=utc_now()))
    elif (transform.status != "succeeded" or transform.input_digest != sealed.digest
            or transform.job_id != job.id or transform.input_refs != input_refs
            or transform.output_refs != [record.logical_ref, definition_record.logical_ref]
            or transform.engine != f"python-pillow:{toolchain.version}" or transform.parameters != transform_parameters):
        raise MediaScriptError("既有成功来源定义与原脚本不一致")
    for index, ref in enumerate(input_refs):
        repository.create_dependency_edge(DependencyEdge(id=f"edge_{job.id}_script_{index}", workspace_id=task.workspace_id,
            task_id=task.id, upstream_ref=ref, downstream_ref=record.logical_ref,
            created_by_transform_id=f"transform_{job.id}_script", created_at=job.created_at))
    return {"source_ref": record.logical_ref, "artifact_id": record.id, "sha256": record.sha256,
        "size_bytes": record.size_bytes, "name": measured.name, "width": measured.width, "height": measured.height, "applied": False}
