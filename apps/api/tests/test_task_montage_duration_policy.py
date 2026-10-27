from datetime import datetime, timezone
from dataclasses import replace

import pytest

from app.montage import MontagePlanner
from app.subtitle_schedule import narration_schedule
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.production import MaterialAsset, MaterialSegment, ProductionProfile


@pytest.fixture
def task_inputs(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"metadata-only fixture; no media decoding")
    stat = source.stat()
    script = CreationTaskScript(
        id="script", workspace_id="workspace", task_id="task", run_id="run",
        topic_id="topic", title="Two sections", language="en-US",
        selection_status="confirmed",
        sections=[CreationTaskScriptSection(
            id=section_id, title=text, content=text, visual_prompt=text,
            evidence_reference_ids=[],
        ) for section_id, text in (("first", "First"), ("second", "Second"))],
    )
    asset = MaterialAsset(
        id="asset", workspace_id="workspace", file_name=source.name,
        path=str(source), size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        duration_seconds=120, usage_role="b_roll", review_status="approved",
        analysis_status="ready",
        segments=[MaterialSegment(
            id=section_id, asset_id="asset", start_seconds=start,
            end_seconds=start + 60, description=text,
            usage_role="b_roll", review_status="approved",
        ) for section_id, text, start in (("first", "First", 0), ("second", "Second", 60))],
    )
    return {
        "workspace_id": "workspace", "task_id": "task", "script": script,
        "profile": ProductionProfile(workspace_id="workspace"), "assets": [asset],
        "media_reference_for_asset": lambda _: "workspace://source.mp4",
        "narration_audio_path": "narration://artifact/current",
        "narration_segment_ids_by_section": {"first": "audio_first", "second": "audio_second"},
    }


def test_task_120_seconds_overrides_profile_90_without_extending_audio(task_inputs):
    plan = MontagePlanner().build_for_creation_task(
        **task_inputs, duration_target_seconds=120, narration_duration_seconds=120,
        narration_segment_durations_by_section={"first": 60, "second": 60},
    )

    assert task_inputs["profile"].target_duration_max_seconds == 90
    assert plan.duration_seconds == 120
    assert [cue.duration_seconds for cue in plan.cues] == [60, 60]
    assert [cue.text for cue in plan.cues] == ["First", "Second"]
    assert [cue.narration_segment_id for cue in plan.cues] == ["audio_first", "audio_second"]
    assert [clip.segment_id for clip in plan.clips] == ["first", "second"]
    assert plan.clips[-1].timeline_end_seconds == 120


@pytest.mark.parametrize("target,actual", [(120, 121), (60, 61)])
def test_actual_audio_exceeding_task_limit_is_rejected(task_inputs, target, actual):
    with pytest.raises(ValueError, match=f"超过创作方案上限 {target} 秒"):
        MontagePlanner().build_for_creation_task(
            **task_inputs, duration_target_seconds=target,
            narration_duration_seconds=actual,
            narration_segment_durations_by_section={"first": actual / 2, "second": actual / 2},
        )


@pytest.mark.parametrize("segmented", [False, True])
def test_short_audio_is_not_padded_to_task_target_or_profile_minimum(task_inputs, segmented):
    plan = MontagePlanner().build_for_creation_task(
        **task_inputs, duration_target_seconds=120, narration_duration_seconds=1.321,
        narration_segment_durations_by_section={"first": 0.901, "second": 0.420} if segmented else None,
    )

    assert plan.duration_seconds == 1.321
    assert sum(cue.duration_seconds for cue in plan.cues) == pytest.approx(1.321)
    assert plan.clips[-1].timeline_end_seconds == 1.321
    assert [cue.text for cue in plan.cues] == ["First", "Second"]
    if segmented:
        assert [cue.duration_seconds for cue in plan.cues] == [0.901, 0.420]


@pytest.mark.parametrize("clock", [
    {}, {"first": float("nan"), "second": 60},
    {"first": 0, "second": 120}, {"first": 60, "second": 59},
])
def test_invalid_segment_clock_is_rejected(task_inputs, clock):
    with pytest.raises(ValueError, match="配音时长"):
        MontagePlanner().build_for_creation_task(
            **task_inputs, duration_target_seconds=120, narration_duration_seconds=120,
            narration_segment_durations_by_section=clock,
        )


@pytest.fixture
def english_semantic_inputs(task_inputs):
    text = [
        "The footage shows an outdoor maize shelling site, with people working beside a green cylindrical machine.",
        "A belt and pulley drive is clearly visible on the side of the machine.",
        "White woven sacks, a blue tarp, and containers are arranged nearby in the open.",
        "Workers gather around the machine, working together in the open air.",
    ]
    sections = [CreationTaskScriptSection(id=f"s{index}", title=f"Scene {index}", content=content,
        visual_prompt=f"Scene {index}", evidence_reference_ids=[]) for index, content in enumerate(text, 1)]
    return {**task_inputs, "script": replace(task_inputs["script"], sections=sections),
        "duration_target_seconds": 30, "narration_duration_seconds": 25.36,
        "narration_segment_ids_by_section": {f"s{i}": f"audio_s{i}" for i in range(1, 5)},
        "narration_segment_durations_by_section": dict(zip((f"s{i}" for i in range(1, 5)), [7.68, 5.36, 6.64, 5.68]))}


@pytest.mark.parametrize("presenter", [False, True])
def test_four_semantic_shots_preserve_ten_english_caption_cues(english_semantic_inputs, presenter):
    values = english_semantic_inputs
    if presenter:
        values = {**values, "assets": [], "presenter_share_target": 100,
            "person_render_by_section": {section: (f"render_{section}", duration)
                for section, duration in values["narration_segment_durations_by_section"].items()}}
    plan = MontagePlanner().build_for_creation_task(**values)
    assert len(plan.clips) == 4 and len(plan.cues) == 10
    assert [(clip.timeline_start_seconds, clip.timeline_end_seconds) for clip in plan.clips] == [
        (0, 7.68), (7.68, 13.04), (13.04, 19.68), (19.68, 25.36)]
    assert [clip.cue_id for clip in plan.clips] == ["cue_001", "cue_005", "cue_007", "cue_009"]
    assert [cue.narration_segment_id for cue in plan.cues] == [
        "audio_s1"] * 4 + ["audio_s2"] * 2 + ["audio_s3"] * 2 + ["audio_s4"] * 2
    captions = narration_schedule(plan.cues, plan.duration_seconds)
    assert [cue.text for cue in captions] == [
        "The footage shows an outdoor maize shelling", "site,",
        "with people working beside a green cylindrical", "machine.",
        "A belt and pulley drive is clearly visible on", "the side of the machine.",
        "White woven sacks, a blue tarp,", "and containers are arranged nearby in the open.",
        "Workers gather around the machine,", "working together in the open air."]
    assert [round(cue.end_seconds - cue.start_seconds, 3) for cue in captions] == [
        3.238, .376, 3.464, .602, 3.496, 1.864, 2.639, 4.001, 2.882, 2.798]
    assert captions[-1].end_seconds == pytest.approx(25.36)
    assert plan.narration_audio_path == values["narration_audio_path"]
    assert all(bool(clip.person_render_id) is presenter for clip in plan.clips)


def test_semantic_shot_cannot_use_caption_fragments_to_hide_insufficient_source(english_semantic_inputs):
    asset = english_semantic_inputs["assets"][0]
    values = {**english_semantic_inputs, "assets": [replace(asset,
        segments=[replace(segment, end_seconds=segment.start_seconds + 7) for segment in asset.segments])]}
    with pytest.raises(ValueError, match="B-roll 镜头时长不足"):
        MontagePlanner().build_for_creation_task(**values)


@pytest.mark.parametrize('presenter', [0, 40])
def test_row_prepared_whole_planner_matches_complete_entry_without_changing_global_allocation(task_inputs, monkeypatch, presenter):
    from itertools import count
    from types import SimpleNamespace
    from app.creation.execution_input import ExecutionScriptHeader
    from app.creation.duration_requirement import SectionWindow
    from app.montage import creation_task_montage_plan_to_payload
    import app.montage as module
    script = task_inputs['script']
    header = ExecutionScriptHeader(script.id, script.task_id, script.title, script.selection_status,
        script.updated_at, len(script.sections), script.workspace_id, script.language)
    common = dict(task_inputs, duration_target_seconds=12, narration_duration_seconds=12,
        narration_segment_durations_by_section={'first': 6, 'second': 6},
        person_render_by_section={'first': ('paid-person', 6)} if presenter else None,
        presenter_share_target=presenter,
        delivery_windows=[SectionWindow('first', 0, 6), SectionWindow('second', 6, 12)])
    def fixed_ids():
        values = count()
        monkeypatch.setattr(module, 'uuid4', lambda: SimpleNamespace(hex=f'{next(values):032x}'))
    fixed_ids()
    legacy = MontagePlanner().build_for_creation_task(**common)
    planner = MontagePlanner()
    prepared = planner.prepare_creation_inputs(lambda: iter(script.sections), profile=task_inputs['profile'],
        duration_target_seconds=12, narration_duration_seconds=12,
        narration_segment_ids_by_section=task_inputs['narration_segment_ids_by_section'],
        narration_segment_durations_by_section=common['narration_segment_durations_by_section'])
    fixed_ids()
    row = planner.build_for_creation_inputs(workspace_id=header.workspace_id, task_id=header.task_id,
        script_header=header, prepared=prepared, assets=task_inputs['assets'],
        media_reference_for_asset=task_inputs['media_reference_for_asset'],
        narration_audio_path=task_inputs['narration_audio_path'],
        person_render_by_section=common['person_render_by_section'], presenter_share_target=presenter,
        delivery_windows=common['delivery_windows'])
    row = replace(row, created_at=legacy.created_at, updated_at=legacy.updated_at)
    assert creation_task_montage_plan_to_payload(row) == creation_task_montage_plan_to_payload(legacy)
    assert row.duration_seconds == 12
    assert [cue.timeline_start_seconds for cue in row.cues] == [0, 6]
