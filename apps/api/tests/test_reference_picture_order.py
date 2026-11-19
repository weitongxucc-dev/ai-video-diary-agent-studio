"""Ordered task input is replayable and consumes only the adopted visual ranges."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.creation.duration_requirement import DurationConflict, SectionWindow
from app.creation.reference_picture_order import ReferencePictureOrder
from app.creation.segment_production_use_case import SegmentProductionUseCase
from app.montage import MontagePlanner, NarrationCue, creation_task_montage_plan_to_payload
from app.production import ProductionRepository
from test_creation_task_material_scope import command, scoped_work as scoped_work


def test_scope_preserves_source_sequence_and_replays_historical_request(scoped_work):
    work = scoped_work
    first, second = work.assets
    path = f"/api/creation-tasks/{work.task.id}/material-scope"

    # A pre-change work and receipt survive the new default fields.
    first_request = command(work.plan, first)
    original = work.client.put(path, headers=work.headers, json=first_request)
    assert original.status_code == 200, original.text
    legacy_id = f"{path}/{first_request['request_id']}"
    with work.repo._connect() as conn:
        conn.execute("UPDATE task_events SET payload_json=json_remove(payload_json,'$.receipt.selection_mode',"
                     "'$.receipt.source_start_seconds_by_asset') WHERE event_type='task_material_scope_saved'")
    legacy_receipt = work.client.get(legacy_id, headers=work.headers).json()
    assert "selection_mode" not in legacy_receipt

    plan = work.repo.plan_records.get_creation_plan("local", work.plan.id)
    assert plan.material_selection_mode == "automatic"
    payload = command(plan, second, first)
    payload["selection_mode"] = "source_order"
    payload["assets"][0]["source_start_seconds"] = 1.033
    order_saved = work.client.put(path, headers=work.headers, json=payload)
    assert order_saved.status_code == 200, order_saved.text
    receipt = order_saved.json()
    assert receipt["selection_mode"] == "source_order"
    assert receipt["selected_material_asset_ids"] == [second.id, first.id]
    assert receipt["source_start_seconds_by_asset"] == {second.id: 31 / 30}
    assert work.client.put(path, headers=work.headers, json=first_request).json() == legacy_receipt

    restarted = ProductionRepository(database_path=work.repo.database_path, video_library_path=work.repo.video_library_path)
    resumed = restarted.plan_records.get_creation_plan("local", plan.id)
    assert ReferencePictureOrder.from_plan(resumed) == ReferencePictureOrder((second.id, first.id), {second.id: 31 / 30})
    assert work.client.put(path, headers=work.headers, json=payload).json() == receipt
    conflict = {**payload, "assets": list(reversed(payload["assets"]))}
    assert work.client.put(path, headers=work.headers, json=conflict).status_code == 409
    fresh = command(resumed, second)
    fresh["selection_mode"] = "source_order"
    fresh["assets"][0]["source_start_seconds"] = 3
    assert work.client.put(path, headers=work.headers, json=fresh).status_code == 409
    assert restarted.plan_records.get_creation_plan("local", plan.id) == resumed


def test_ordered_montage_and_local_redo_keep_original_source_frames(scoped_work, tmp_path):
    work = scoped_work
    ignored_opening, reference = work.assets
    asset = replace(ignored_opening, usage_role="a_roll", segments=[
        replace(segment, usage_role="b_roll" if ordinal else "unassigned")
        for ordinal, segment in enumerate(ignored_opening.segments)
    ])
    other = replace(reference, usage_role="unassigned")
    cues = [NarrationCue(f"cue-{section}", section, section, duration, f"audio-{section}") for section, duration in (
        ("speaker", 1), ("section-2", 1.5), ("section-3", 1.5),
    )]
    windows = [SectionWindow("speaker", 0, 1), SectionWindow("section-2", 1, 2.5), SectionWindow("section-3", 2.5, 4)]
    order = ReferencePictureOrder((asset.id, other.id), {asset.id: 1.5})
    args = dict(workspace_id="local", task_id=work.task.id, script_id="script", script_title="参考片保序",
        cues=cues, visual_cues=cues, visual_requirements={cue.source_section_id: "竞争语义" for cue in cues},
        assets=[other, asset], media_reference_for_asset=lambda asset_id: f"workspace://{asset_id}.mp4",
        narration_audio_path="narration://artifact/test", person_render_by_section={"speaker": ("opening", 1)},
        presenter_share_target=25, delivery_windows=windows, reference_order=order)
    planner = MontagePlanner()
    full = planner._build_creation_plan(**args)
    assert full.clips[0].person_render_id == "opening"
    assert full.status == "ready"
    assert "参考片顺序" in full.message
    rendered = [((clip.asset_id, clip.source_start_seconds, clip.source_end_seconds),
                  (clip.timeline_start_seconds, clip.timeline_end_seconds)) for clip in full.clips[1:]]
    assert rendered == [
        ((asset.id, 1.5, 2), (1, 1.5)), ((asset.id, 2, 3), (1.5, 2.5)),
        ((other.id, 0, 1), (2.5, 3.5)), ((other.id, 1, 1.5), (3.5, 4)),
    ]
    assert all(clip.source_end_seconds - clip.source_start_seconds ==
               clip.timeline_end_seconds - clip.timeline_start_seconds for clip in full.clips)

    class SavedMontage:
        def get_creation_task_montage_plan(self, *_args, **_kwargs):
            return creation_task_montage_plan_to_payload(full)

    segment_production = SegmentProductionUseCase(SavedMontage(), narration_stage=None, renderer=None,
        artifact_store=None, output_root=tmp_path)
    locked, offset, expected = segment_production.reference_section_window(work.task, "section-3",
        SimpleNamespace(duration_seconds=1.25), connection=None)
    assert locked == windows[-1] and offset == 45
    redo = planner._build_creation_plan(**{**args, "cues": cues[-1:], "visual_cues": cues[-1:],
        "delivery_windows": [locked], "person_render_by_section": {}}, reference_offset_frames=offset)
    assert [(c.asset_id, round(c.source_start_seconds * 30), round(c.source_end_seconds * 30)) for c in redo.clips] == expected
    with pytest.raises(DurationConflict, match="超过原参考画面窗口"):
        segment_production.reference_section_window(work.task, "section-3",
            SimpleNamespace(duration_seconds=1.6), connection=None)

    with pytest.raises(DurationConflict, match="缺少 0.500 秒"):
        planner._build_creation_plan(**{**args, "delivery_windows": [*windows[:2], SectionWindow("section-3", 2.5, 6)]})
