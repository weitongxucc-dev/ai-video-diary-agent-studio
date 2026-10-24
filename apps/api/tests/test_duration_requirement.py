from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace

import pytest

from app.creation.duration_requirement import DurationConflict, DurationRequirement, SectionWindow, measured_section_windows
from app.montage import MontagePlanner
from app.production import CreationTaskScriptSection
from app.subtitle_schedule import narration_schedule
from test_task_montage_duration_policy import task_inputs  # noqa: F401


@pytest.mark.parametrize('duration,result', [(30, 'met'), (Fraction(901, 30), 'met'),
    (Fraction(899, 30), 'met'), (17.12, 'mismatch'), (29.92, 'mismatch'), (30.04, 'mismatch'), (None, 'unknown')])
def test_exact_delivery_uses_video_frame_clock(duration, result):
    assert DurationRequirement(30).assess(duration, '30/1') == result


def test_approximate_requires_an_explicit_range_and_unknown_is_not_met():
    requirement = DurationRequirement(30, 'approximate', (29, 31))
    assert [requirement.assess(n, 30) for n in (29, 29.92, 31, 31.01)] == ['met', 'met', 'met', 'mismatch']
    assert requirement.assess(30, '0/0') == 'unknown'
    with pytest.raises(ValueError):
        DurationRequirement(30, 'approximate')


def test_real_segment_overflow_replans_unadopted_budget_and_keeps_adoption_explicit():
    sections = [SimpleNamespace(id=str(i), content='正文一二三四五') for i in range(3)]
    real = {'0': 11.2, '1': 9.76, '2': 8.96}
    old = SectionWindow('0', 0, 12)
    windows = measured_section_windows(DurationRequirement(30), sections, real, adopted=[old])
    assert windows[-1].end_seconds == 30
    assert all(window.duration_seconds + 1e-9 >= real[window.section_id] for window in windows)
    assert windows[0] != old
    assert old.end_seconds == 12
    stable = measured_section_windows(DurationRequirement(30), sections, real, adopted=[windows[0]])
    assert stable[0] == windows[0]
    with pytest.raises(DurationConflict, match='真实配音'):
        measured_section_windows(DurationRequirement(30), sections, {'0': 31})


@pytest.mark.parametrize('source_limited', [False, True])
def test_visual_delivery_30_keeps_all_real_voice_and_caption_durations(task_inputs, source_limited):
    texts = ['玉米脱粒，多人围着设备忙。', '绿色圆筒设备，侧面有轮带。', '画面所见仅此，性能未证实。',
             '这是公开演示，非工厂实拍。', '署名与许可见片尾，再谈需求。']
    durations = [3.2, 3.44, 3.6, 3.2, 3.68]
    sections = [CreationTaskScriptSection(str(i), str(i), text, '', []) for i, text in enumerate(texts)]
    script = replace(task_inputs['script'], sections=sections, language='zh-CN')
    measured = dict(zip([s.id for s in sections], durations))
    windows = measured_section_windows(DurationRequirement(30), sections, measured)
    assets = task_inputs['assets']
    if source_limited:
        asset = assets[0]
        donor = asset.segments[0]
        assets = [replace(asset, duration_seconds=25.578, segments=[
            replace(donor, id='cn-first', start_seconds=0, end_seconds=12),
            replace(donor, id='cn-second', start_seconds=12, end_seconds=22.422),
            replace(donor, id='cn-last', start_seconds=22.422, end_seconds=25.578)])]
    plan = MontagePlanner().build_for_creation_task(**{**task_inputs, 'script': script, 'assets': assets},
        duration_target_seconds=30, narration_duration_seconds=17.12,
        narration_segment_durations_by_section=measured, delivery_windows=windows)
    assert plan.duration_seconds == 30
    assert sum(cue.duration_seconds for cue in plan.cues) == pytest.approx(17.12)
    assert [cue.text for cue in plan.cues] == texts
    schedule = narration_schedule(plan.cues, plan.duration_seconds)
    assert sum(c.end_seconds - c.start_seconds for c in schedule) == pytest.approx(17.12)
    assert [c.start_seconds for c in schedule] == pytest.approx([w.start_seconds for w in windows])
    ordered = sorted(plan.clips, key=lambda clip: clip.source_start_seconds)
    assert all(left.source_end_seconds <= right.source_start_seconds for left, right in zip(ordered, ordered[1:]))
    if source_limited:
        assert sum(c.source_end_seconds - c.source_start_seconds for c in plan.clips) == pytest.approx(25.578, abs=.003)
        assert all(c.needs_confirmation and '仅画面' in c.match_reasons[0] for c in plan.clips)
    else:
        assert all(clip.source_end_seconds - clip.source_start_seconds == pytest.approx(
            clip.timeline_end_seconds - clip.timeline_start_seconds) for clip in plan.clips), [(c.source_start_seconds,c.source_end_seconds,c.timeline_start_seconds,c.timeline_end_seconds) for c in plan.clips]
    assert any(clip.needs_confirmation for clip in plan.clips)
