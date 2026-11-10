"""Semantic cut decisions use the existing ripple map and linked transaction."""
from app.creation.editing.linked import _prepared_proposal
from app.creation.editing.metadata import check_command
from app.creation.editing.range_contracts import TimelineRangeEdit
from app.linked_timeline import remove_time_ranges


def apply_range_edit(task, plan, tracks, audio, command):
    check_command(task, command)
    edit = TimelineRangeEdit.model_validate(command.values["range_edit"])
    return remove_time_ranges(plan, tracks, audio, edit.removed_ranges_ms)


def prepare_range_edit(task, plan, tracks, audio, command, *, verifier, output_refs):
    changed, captions = apply_range_edit(task, plan, tracks, audio, command)
    edit = TimelineRangeEdit.model_validate(command.values["range_edit"])
    removed = round((plan.duration_seconds - changed.duration_seconds) * 1000)
    return _prepared_proposal(command, plan, changed, tracks, captions,
        verifier=verifier, output_refs=output_refs, raw_command="按证据精剪原声",
        scope={"kind": "time_ranges", **edit.model_dump(mode="json")}, playhead_ms=edit.removed_ranges_ms[0][0],
        summary=f"删除 {len(edit.removed_ranges_ms)} 个区间，共 {removed} 毫秒；声音、字幕、图层和后续画面同步移动。",
        payload={"base_version_id": command.base_version_id, "range_edit": edit.model_dump(mode="json")},
        preview={side: {"ref": f"timeline:{command.base_version_id}", "label": label,
            "detail": f"{candidate.duration_seconds:.3f} 秒；{edit.reason}"}
            for side, label, candidate in (("before", "原时间线", plan), ("after", "精剪后草稿", changed))},
        effects=["保留原素材与原声；这是草稿区间变化，听感与切点须用实际预览检查，可整组撤销。"])
