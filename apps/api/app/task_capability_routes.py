"""Task editing and execution capability HTTP; shared fact readers retain their owners."""
from urllib.parse import quote

from fastapi import APIRouter, Request
from app.schemas import CreationTaskExactEditRequest, EXACT_EDIT_FIELDS, EXACT_MONTAGE_REQUIRED_FIELDS

class TaskEditCapabilityResponseAdapter:
    def __init__(self, *, reader_for):
        self.reader_for = reader_for

    def facts(self, task):
        reader = self.reader_for()
        facts = reader.read(task)
        version = facts.version
        reason_text = {
            "no_storyboard": "当前任务没有故事板",
            "no_timeline_version": "先保留当前故事板并建立可撤销版本",
            "no_clip": "当前任务没有可编辑镜头",
            "no_visual_clip": "当前任务没有可调整画面的镜头",
            "no_audio_track": "当前任务没有音轨，先导入音频或采用当前作品的声音",
            "no_subtitle_tracks": "当前版本没有字幕轨",
        }
        operations = []
        for operation, fields in EXACT_EDIT_FIELDS.items():
            reason_code = reader.reason_for(facts, operation, requires_clip="clip_id" in fields)
            reason = reason_text[reason_code] if reason_code is not None else None
            operations.append({"operation_type": operation, "available": reason is None, "reason": reason,
                "parameter_fields": sorted(fields), "required_fields": sorted(EXACT_MONTAGE_REQUIRED_FIELDS.get(operation, set()) | ({"clip_id"} if "clip_id" in fields else set())),
                "requires_target_validation": True})
        return {"task_id": task.id, "timeline_version_id": task.current_timeline_version_id,
            "timeline_sequence": version.sequence if version else 0,
            "schema": CreationTaskExactEditRequest.model_json_schema(),
            "audio_actions": reader.audio_actions(facts),
            "supported_operations": list(EXACT_EDIT_FIELDS), "operations": operations,
            "direct_apply": {
                "endpoint": f"/api/creation-tasks/{quote(task.id, safe='')}/edits/exact",
                "operations": [
                    {**item, "parameter_fields": (
                        ["cue_id", "text"] if item["operation_type"] == "UpdateSubtitleCue" else
                        ["subtitle_display"] if item["operation_type"] == "ConfigureSubtitleTrack" else
                        item["parameter_fields"]),
                     "required_fields": (
                        ["cue_id", "text"] if item["operation_type"] == "UpdateSubtitleCue" else
                        ["subtitle_display"] if item["operation_type"] == "ConfigureSubtitleTrack" else
                        item["required_fields"])}
                    for item in operations if item["operation_type"] != "GenerateCoverFromFrame"
                ],
            },
            "units": {"source_start_seconds": "源片段起点，秒；TrimClip 保留镜头时长与旁白",
                      "start_seconds": "时间轴秒", "end_seconds": "时间轴秒", "split_time_seconds": "时间轴秒",
                      "playback_rate": "0.25–4，语义按operation区分。ChangeClipSpeed：绝对画面速度，每1秒timeline消耗rate秒source；source_span=timeline_span*rate。0.7减速、2加速；2.5秒槽@0.7消耗2.5*0.7=1.75秒源片段，保留时间槽、旁白和字幕。ChangeLinkedSpeed：相对当前速度倍率，联动改变时轴，不是绝对速度。",
                      "crop_x": "0–1，裁剪窗口水平位置", "crop_y": "0–1，裁剪窗口垂直位置",
                "audio_edit.action": "update修改片段，split分割，duplicate复制，delete删除并保留空隙；track修改音轨，add向已有音轨添加受权音频。删除values为空对象，其余字段按各action schema。",
                "audio_edit.target_id": "当前音轨或音频片段ID；与action对应。",
                "audio_edit.values.gain_db": "-60至12 dB，绝对目标音量；轨道与片段音量叠加。减6dB须读取原值后计算目标。",
                "audio_edit.values.muted": "布尔值；true静音，false恢复。",
                "audio_edit.values.fade_in_ms": "音频片段淡入毫秒，非负整数；淡入+淡出不能超过片段包络长度。",
                "audio_edit.values.fade_out_ms": "音频片段淡出毫秒，非负整数；不是时间线结束位置。",
                "audio_edit.values.denoise_db": "0至24 dB，片段降噪强度，0关闭。",
                "audio_edit.values.loudness_target_lufs": "-30至-10 LUFS，片段响度目标；显式null清除。",
                "audio_edit.values.duck_under_voice": "仅music轨布尔值；按当前真实旁白压低背景音乐。",
                "audio_edit.values.source_ref": "add使用已读取的受权素材/音频逻辑引用；不接本机绝对路径，不允许update换来源。",
                "audio_edit.values.source_start_ms": "源文件起点，毫秒；移动/裁切不改变画面。",
                "audio_edit.values.source_end_ms": "源文件终点，毫秒；不得越过实际源文件或成片范围。",
                "audio_edit.values.timeline_start_ms": "整条时间线起点，毫秒；duplicate必须显式给出。",
                "audio_edit.values.at_ms": "split在整条时间线上的毫秒位置，必须位于所选音频内部。",
                "audio_edit.values.gain_points": "所选片段局部毫秒位置offset_ms及相对gain_db；完整替换该片段的点列表，空列表清除；从editor_gain_points读取现有局部点，其他片段保持。",
                "audio_edit.values.locked": "片段或音轨布尔锁定；解锁只提交locked:false。",
                "effects.exposure": "-2至2 EV，绝对曝光设置。", "effects.contrast": "0.5至1.5，1为默认。",
                "effects.saturation": "0至2，1为默认。", "effects.temperature": "-1至1，负值冷、正值暖。",
                "effects.zoom": "0.1至4倍，1为默认。", "effects.offset_x": "-1至1，归一化水平位置；正值右移。",
                "effects.offset_y": "-1至1，归一化垂直位置；正值下移。", "effects.rotation": "-180至180度，绝对角度。",
                "effects.fade_in_seconds": "0至5秒黑场淡入；非零至少两输出帧，与淡出总长不超过镜头。",
                "effects.fade_out_seconds": "0至5秒黑场淡出；独立于音频淡出。",
                "effects.beauty_strength": "0至1；非零本机人像效果尚无受许可执行路径。",
                "effects.skin_exposure": "-0.5至0.5 EV；非零本机人像效果尚无受许可执行路径。",
                "patch": "只修改明确提交字段，遗漏保持各对象现值；重置须明确提交默认值。结构动作按action执行；不接独立音频变速、solo持久化或update来源替换。"},
            "preview_modes": {"draft": "只生成参数差异，不生成媒体", "media": "需现有设备许可和标定预算，返回实际预览或异步作业"}}


def build_task_capability_router(*, access_for, edit_facts_for, execution_facts_for):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/assistant/edit-capabilities')
    def get_creation_task_edit_capabilities(task_id: str, request: Request):
        task, _member_id = access_for()(task_id, request)
        return edit_facts_for()(task)

    @router.get('/api/creation-tasks/{task_id}/assistant/execution-capabilities')
    def get_creation_task_execution_capabilities(task_id: str, request: Request):
        task, _member_id = access_for()(task_id, request)
        return execution_facts_for()(task)
    return router
