"""Local subtitle style validation, command compilation and rendered proof."""
from dataclasses import asdict, fields
import math
import re
from app.subtitle_tracks import SubtitleStyle


def validate_style_patch(patch: dict) -> dict:
    allowed = {item.name for item in fields(SubtitleStyle)} - {"enabled"}
    if not patch or set(patch) - allowed:
        raise ValueError("字幕样式包含未支持的字段；显示语言请使用字幕开关")
    for key, value in patch.items():
        if key in {"primary_color", "emphasis_color", "stroke_color", "background_color"}:
            if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
                raise ValueError("字幕颜色须为六位十六进制颜色，例如 #FFFFFF")
        elif key in {"font_size", "stroke_width"}:
            low, high = (1, 1920) if key == "font_size" else (0, 12)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} 必须是 {low}–{high} 的整数")
        elif key == "background_opacity":
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("背景不透明度须在0到1之间")
        elif key == "position" and (not isinstance(value, str) or value not in {"top", "middle", "bottom"}):
            raise ValueError("字幕位置须为顶部、居中或底部")
        elif key == "bilingual_order" and (not isinstance(value, str) or value not in {"source_then_translation", "translation_then_source"}):
            raise ValueError("双语顺序无效")
        elif key in {"name", "font_family"} and (not isinstance(value, str) or not value.strip() or len(value) > 120):
            raise ValueError("字体名称不能为空且最多120字")
    return dict(patch)


def compile_subtitle_style_command(command: str) -> dict | None:
    text = command.strip().rstrip("。！")
    choices = {"字幕放上面":{"position":"top"}, "字幕放中间":{"position":"middle"}, "字幕放下面":{"position":"bottom"},
        "英文放在中文下面":{"bilingual_order":"source_then_translation"}, "英文放在中文上面":{"bilingual_order":"translation_then_source"},
        "去掉字幕背景":{"background_opacity":0}, "去掉字幕描边":{"stroke_width":0}}
    if text in choices:
        return choices[text]
    match = re.fullmatch(r"(?:把)?字幕(字号|描边宽度|背景透明度|背景不透明度)(?:改成|改为|设为)(\d+(?:\.\d+)?)(%|％)?", text)
    if match:
        field, raw, percent = match.groups()
        value = float(raw)
        if field in {"字号", "描边宽度"}:
            if percent or not value.is_integer():
                raise ValueError("字号与描边宽度须为整数，不使用百分比")
            return validate_style_patch({"font_size" if field == "字号" else "stroke_width":int(value)})
        if not percent or value > 100:
            raise ValueError("背景透明度请使用0%到100%")
        return {"background_opacity":1-value/100 if field == "背景透明度" else value/100}
    match = re.fullmatch(r"(?:把)?字幕(文字颜色|颜色|描边颜色|背景颜色|强调颜色|字体)(?:改成|改为|设为)(.+)", text)
    if match:
        field, value = match.groups()
        names = {"白色":"#FFFFFF", "黑色":"#000000", "黄色":"#FFD54F", "红色":"#FF4040", "蓝色":"#3388FF", "绿色":"#33CC66"}
        key = {"文字颜色":"primary_color","颜色":"primary_color","描边颜色":"stroke_color","背景颜色":"background_color","强调颜色":"emphasis_color","字体":"font_family"}[field]
        return validate_style_patch({key:value if key == "font_family" else names.get(value, value)})
    return None


def publish_subtitle_style_preview(repository, store, temporary_root, workspace_id, task_id, caption, input_refs, *, size=(1080,1920)):
    import hashlib
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from uuid import uuid4
    from app.montage import _write_subtitle_image
    from app.models import utc_now
    from app.creation.contracts import ArtifactRecord, DependencyEdge, JobRun, TransformRun
    parameters = {**asdict(caption),"canvas":size}
    identity = uuid4().hex
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
    job = repository.create_job_run(JobRun(id=f"job_{identity}", workspace_id=workspace_id, task_id=task_id,
        kind="subtitle_style_preview", idempotency_key=f"subtitle_style_preview:{identity}", input_digest=digest))
    running = repository.claim_job_run(workspace_id, job.id)
    try:
        temporary_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="subtitle-style-", dir=temporary_root) as folder:
            image = Path(folder) / "preview.png"
            _write_subtitle_image(caption, image, size=size)
            publication = store.publish_file(f"artifacts/{task_id}/{job.id}/preview.png", image)
        record = repository.create_artifact_record(ArtifactRecord(id=f"artifact_{identity}",workspace_id=workspace_id,
            task_id=task_id,kind="subtitle_style_preview",logical_ref=publication.logical_ref,sha256=publication.sha256,
            size_bytes=publication.size_bytes,producer_job_id=job.id,input_refs=input_refs))
        transform = repository.create_transform_run(TransformRun(id=f"transform_{identity}",workspace_id=workspace_id,
            task_id=task_id,job_id=job.id,transform_type="subtitle_style_preview",input_digest=digest,input_refs=input_refs,
            output_refs=[record.logical_ref],engine="local-subtitle-style-v1",parameters=parameters,status="succeeded",
            started_at=running.started_at,finished_at=utc_now()))
        for index, reference in enumerate(input_refs):
            repository.create_dependency_edge(DependencyEdge(id=f"edge_{identity}_{index}", workspace_id=workspace_id,
                task_id=task_id,upstream_ref=reference,downstream_ref=record.logical_ref,created_by_transform_id=transform.id))
        repository.finish_job_run(workspace_id,job.id,status="succeeded",output_refs=[record.logical_ref])
        return record
    except Exception as exc:
        repository.finish_job_run(workspace_id,job.id,status="failed",failure_reason=type(exc).__name__,
            impact="字幕和成片没有改变",repair_action="检查本机字体与外接盘后重试")
        raise
