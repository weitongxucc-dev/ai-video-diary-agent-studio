from datetime import datetime, timezone

import pytest

from app.montage import MontagePlanner
from app.production import (
    CreationTaskScript,
    CreationTaskScriptSection,
    MaterialAsset,
    MaterialSegment,
    ProductionProfile,
)


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
