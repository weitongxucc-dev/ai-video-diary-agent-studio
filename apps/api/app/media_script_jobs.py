"""Single-PNG task media adapter; scheduling remains owned by TaskMediaJobs."""
import json
import os
from dataclasses import asdict
from pathlib import Path
import re
import stat
import sys
from tempfile import TemporaryDirectory
from PIL import Image

from app.media_script import (
    MediaScriptError, PngOutputContract, ScriptExecutionLimits, ScriptInputRevision,
    ScriptToolchain, MeasuredScriptOutput, prepare_media_script, run_media_script, seal_media_script,
    media_script_resource_facts,
)
from app.graphic_sources import resolve_graphic_source
from app.models import utc_now
from app.creation.contracts import ArtifactRecord, DependencyEdge, TaskEvent, TransformRun
from app.task_audio import media_digest
from app.schemas import MediaScriptPreviewRequest, MediaScriptToolPreviewRequest
from app.media_script_tools import MediaScriptTools, ScriptToolError


MEDIA_SCRIPT_ACTIONS = frozenset({"media_script_preview", "media_script_tool_preview"})


class MediaScriptPreviewRejected(ValueError):
    """Preserve the former worker HTTP conflict without a transport dependency."""

    def __init__(self, detail):
        super().__init__(detail)
        self.status_code = 409
        self.detail = detail


class MediaScriptPreviewUseCase:
    """Own script source sealing and the existing single-PNG execution path."""

    def __init__(self, repository, store, temporary_root, recipe_tools: MediaScriptTools):
        self.repository = repository
        self.store = store
        self.temporary_root = temporary_root
        self.recipe_tools = recipe_tools

    def expand_recipe(self, actor, tool_id, payload):
        value = self.recipe_tools.get(actor, tool_id, version=payload.tool_version)
        instantiated = self.recipe_tools.instantiate_request(actor, tool_id, version=payload.tool_version, parameters=payload.parameters)
        slots = {item["name"] for item in instantiated.pop("input_slots")}
        if {item.name for item in payload.input_refs} != slots or len(payload.input_refs) != len(slots):
            raise ScriptToolError("本次新任务图片输入须与配方输入槽一一匹配")
        expanded = MediaScriptPreviewRequest.model_validate({**instantiated,
            **payload.model_dump(mode="json", exclude_unset=True, exclude={"tool_version", "parameters"})})
        ref = {"tool_id": value["tool_id"], "version": value["version"], "definition_ref": value["definition_ref"],
               "code_sha256": value["code_sha256"]}
        return expanded, ref

    def prepare_source(self, workspace_id, task_id, operation, arguments, actor):
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        if arguments.get('_media_kind') == 'clip_preview':
            raise ValueError('历史短预览没有独立的封存执行输入，请查询原记录并为当前版本重新预览')
        source = arguments.get('_typed_source')
        if source is not None:
            version = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
            if (source['workspace_id'], source['task_id'], source['member_id'], source['expected_timeline_version_id'], source['expected_timeline_sequence']) != (workspace_id, task_id, actor.user_id or actor.member_id, task.current_timeline_version_id, version.sequence if version else 0):
                raise ValueError('封存的类型化编辑输入或成员已失效')
        if operation == 'media_script_tool_preview':
            invocation = MediaScriptToolPreviewRequest.model_validate(arguments['payload'])
            payload, recipe_ref = self.expand_recipe(actor, arguments['tool_id'], invocation)
            if recipe_ref != arguments['_recipe_ref']:
                raise ValueError('封存配方版本或来源发生变化')
        else:
            payload = MediaScriptPreviewRequest.model_validate(arguments['payload'])
        version = self.repository.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if (payload.expected_timeline_version_id, payload.expected_timeline_sequence) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise ValueError('脚本输入时间线已变化')
        return task, payload, version

    def seal(self, prepared, toolchain, budget, frozen_descriptor):
        task, payload, version = prepared
        descriptor = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(toolchain).items()}
        if frozen_descriptor != descriptor:
            raise ValueError('脚本受校准工具链描述已变化')
        sealed, _paths, files = seal_script(self.repository, self.store, task, payload, toolchain)
        resources = media_script_resource_facts(sealed=sealed, limits=script_limits(budget), input_sizes={item['name']: item['size_bytes'] for item in files}, threads=budget.max_threads)
        resources['definition_bytes'] = len(script_definition(sealed, payload))
        if resources['definition_bytes'] > budget.additional_disk_bytes:
            raise ValueError('成功程序元数据超过本次磁盘预留，不安排脚本')
        return ({'base_version_id': task.current_timeline_version_id, 'timeline_sequence': version.sequence if version else 0, 'script_digest': sealed.digest, 'toolchain': descriptor, 'files': files}, resources)

    def execute(self, job, event, actor, context):
        arguments = event['arguments']
        schema = MediaScriptToolPreviewRequest if event['operation'] == 'media_script_tool_preview' else MediaScriptPreviewRequest
        payload = schema.model_validate(arguments['payload'])
        if payload.assistant_turn_id is not None:
            payload = schema.model_validate(payload.model_dump(mode='json', exclude_unset=True, exclude={'assistant_turn_id'}))
        if event['operation'] == 'media_script_tool_preview':
            payload, ref = self.expand_recipe(actor, arguments['tool_id'], payload)
            if ref != arguments['_recipe_ref']:
                raise MediaScriptPreviewRejected("当前配方与原封存版本不一致")
        task = self.repository.task_records.get_creation_task(job.workspace_id, job.task_id)
        descriptor = dict(arguments['_script_toolchain'])
        toolchain = ScriptToolchain(**{key: Path(value) if key in {"python", "stdlib", "python_library", "pillow"} else value
                                    for key, value in descriptor.items()})
        context.check()
        return execute_script(self.repository, self.store, self.temporary_root, task, payload, toolchain,
                              context=context, job=job, event=event, actor=actor)

    def validate_result(self, job, event, result, context):
        outputs = []
        for record in self.repository.execution_records.list_artifact_records(job.workspace_id, job.task_id):
            if record.producer_job_id != job.id:
                continue
            context.check()
            publication = self.store.describe(record.logical_ref)
            if (publication.sha256, publication.size_bytes) != (record.sha256, record.size_bytes):
                raise ValueError('已提交预览产物内容发生变化')
            path = self.store.resolve_file(record.logical_ref)
            metadata = path.stat()
            outputs.append({'reference': record.logical_ref, 'size_bytes': metadata.st_size, 'mtime_ns': metadata.st_mtime_ns, 'ctime_ns': metadata.st_ctime_ns})
            if record.kind == 'media_script_definition':
                if metadata.st_size != event['resources']['definition_bytes']:
                    raise ValueError('成功程序定义大小与封存事实不一致')
                definition = json.loads(path.read_bytes())
                if definition['script_digest'] != event['input_facts']['script_digest']:
                    raise ValueError('成功程序定义摘要不属于原作业')
                continue
            if record.kind != 'graphic_source' or path.suffix != '.png':
                raise ValueError('脚本产物不是登记PNG来源')
            with Image.open(path) as image:
                image.load()
                if image.format != 'PNG' or (image.width, image.height) != (result['width'], result['height']):
                    raise ValueError('脚本PNG实测与发布结果不一致')
        context.check()
        return outputs


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
        record = repository.execution_records.get_artifact_record(task.workspace_id, item.expected_artifact_id)
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


def execute_script(repository, store, temporary_root, task, payload, toolchain, *, context, job, event, actor):
    context.check()
    sealed, inputs, _facts = seal_script(repository, store, task, payload, toolchain)
    if sealed.digest != event["input_facts"]["script_digest"]:
        raise MediaScriptError("封存脚本或输入修订发生变化")
    previous = [item for item in repository.execution_records.list_artifact_records(task.workspace_id, task.id)
                if item.producer_job_id == job.id and item.kind == "graphic_source"]
    if previous:
        if len(previous) != 1:
            raise MediaScriptError("原作业出现不确定的图片登记，不能重执行")
        record, = previous
        started = repository.execution_records.get_task_event(task.workspace_id, f"event_{job.id}_script_started")
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
            repository.execution_records.get_task_event(task.workspace_id, f"event_{job.id}_script_started")
        except KeyError:
            repository.execution_records.append_task_event(TaskEvent(id=f"event_{job.id}_script_started", workspace_id=task.workspace_id,
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
        record = repository.execution_records.create_artifact_record(ArtifactRecord(id=f"artifact_{job.id}_graphic", workspace_id=task.workspace_id,
            task_id=task.id, kind="graphic_source", logical_ref=published.logical_ref, sha256=published.sha256,
            size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=input_refs, created_at=job.created_at))
    definition = script_definition(sealed, payload)
    if len(definition) != event["resources"]["definition_bytes"]:
        raise MediaScriptError("成功程序定义与封存元数据预算不一致")
    published_definition = store.publish_bytes(f"artifacts/{task.id}/{job.id}/definition.json", definition)
    definition_record = repository.execution_records.create_artifact_record(ArtifactRecord(id=f"artifact_{job.id}_definition", workspace_id=task.workspace_id,
        task_id=task.id, kind="media_script_definition", logical_ref=published_definition.logical_ref,
        sha256=published_definition.sha256, size_bytes=published_definition.size_bytes, producer_job_id=job.id, input_refs=input_refs,
        created_at=job.created_at))
    metadata = store.resolve_file(record.logical_ref).stat()
    transform_parameters = {"name": measured.name, "width": measured.width, "height": measured.height,
        "script_digest": sealed.digest, "toolchain_digest": toolchain.digest,
        "definition_ref": definition_record.logical_ref, "owner_member_id": actor.user_id or actor.member_id,
        "file_revision": {"size_bytes": metadata.st_size, "mtime_ns": metadata.st_mtime_ns, "ctime_ns": metadata.st_ctime_ns}}
    transform = next((item for item in repository.lineage_records.list_transform_runs(task.workspace_id, task.id)
                      if item.id == f"transform_{job.id}_script"), None)
    if transform is None:
        transform = repository.lineage_records.create_transform_run(TransformRun(id=f"transform_{job.id}_script", workspace_id=task.workspace_id,
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
        repository.lineage_records.create_dependency_edge(DependencyEdge(id=f"edge_{job.id}_script_{index}", workspace_id=task.workspace_id,
            task_id=task.id, upstream_ref=ref, downstream_ref=record.logical_ref,
            created_by_transform_id=f"transform_{job.id}_script", created_at=job.created_at))
    return {"source_ref": record.logical_ref, "artifact_id": record.id, "sha256": record.sha256,
        "size_bytes": record.size_bytes, "name": measured.name, "width": measured.width, "height": measured.height, "applied": False}
