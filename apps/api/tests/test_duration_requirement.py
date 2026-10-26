from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace

import pytest

from app.creation.duration_requirement import DurationConflict, DurationRequirement, SectionWindow, measured_section_windows
from app.montage import MontagePlanner
from app.production import CreationTaskScriptSection
from app.subtitle_schedule import narration_schedule
from test_task_montage_duration_policy import task_inputs  # noqa: F401


def test_goal_duration_is_independent_of_execution_window():
    from pydantic import ValidationError
    from app.schemas import (CreationPlanDefaultsRequest, CreationRequestDeliveryRequirements,
                             ScriptLibraryReuseRequest)

    requests = [(CreationPlanDefaultsRequest, {"goal": "A long editing project"}),
                (CreationRequestDeliveryRequirements, {}),
                (ScriptLibraryReuseRequest, {"name": "Reuse", "requirements_confirmed": True})]
    for schema, fields in requests:
        for seconds in (1, 1800):
            assert schema(**fields, duration_target_seconds=seconds).duration_target_seconds == seconds
        for invalid in (0, -1, True, 1.5):
            with pytest.raises(ValidationError):
                schema(**fields, duration_target_seconds=invalid)


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


@pytest.mark.parametrize('adopted', [False, True])
def test_replanned_english_caption_clock_keeps_real_segment_boundaries(task_inputs, monkeypatch, adopted):
    from app.montage import NarrationCue
    import app.montage as montage
    # Persisted EN cue durations; candidate text is deliberately not a real script.
    measured = {'open-ground-en': [3.332, 1.772, 3.331, 2.765],
        'work-and-bags-en': [2.458, 3.398, 1.084, 2.82],
        'outdoor-scene-en': [3.215, .547, 3.009, 2.189]}
    sections = [CreationTaskScriptSection(key, key, 'Declared English caption facts.', '', []) for key in measured]
    cues = [NarrationCue(f'{section}-{i}', section, f'declared caption {i}', duration)
            for section, durations in measured.items() for i, duration in enumerate(durations)]
    old = narration_schedule(cues, 29.92)
    assert old[-1].end_seconds == pytest.approx(29.92)
    monkeypatch.setattr(montage, '_build_creation_task_cues', lambda *args, **kwargs: cues)
    windows = [SectionWindow(sections[0].id, 0, 11.2), SectionWindow(sections[1].id, 11.2, 629 / 30),
        SectionWindow(sections[2].id, 629 / 30, 30)]
    plan = MontagePlanner().build_for_creation_task(**{**task_inputs,
        'script': replace(task_inputs['script'], sections=sections)}, duration_target_seconds=30,
        narration_duration_seconds=29.92, delivery_windows=windows)
    if adopted:
        from dataclasses import asdict
        from app.creation.montage_stage_use_case import MontageStageUseCase
        audio = [SimpleNamespace(id=section, source_section_id=section, audio_reference=f'narration://artifact/{section}',
            duration_seconds=total, status='ready') for section, total in zip(measured, [11.2, 9.76, 8.96])]
        states = {}
        for window, segment in zip(windows, audio):
            local_cues = [replace(cue, timeline_start_seconds=None) for cue in plan.cues
                          if cue.source_section_id == window.section_id]
            ids = {cue.id for cue in local_cues}
            local = replace(plan, cues=local_cues, duration_seconds=window.duration_seconds,
                delivery_window=asdict(window), clips=[replace(clip,
                    timeline_start_seconds=clip.timeline_start_seconds-window.start_seconds,
                    timeline_end_seconds=clip.timeline_end_seconds-window.start_seconds)
                    for clip in plan.clips if clip.cue_id in ids])
            states[window.section_id] = {'narration_reference': segment.audio_reference,
                'montage': montage.creation_task_montage_plan_to_payload(local)}
        repository = SimpleNamespace(effective_creation_script=lambda *args: replace(task_inputs['script'], sections=sections),
            list_narration_segments=lambda *args: audio, get_creation_plan=lambda *args: SimpleNamespace(duration_target_seconds=30),
            stage_segment_state=lambda ws, task, section: states[section])
        task = SimpleNamespace(workspace_id='workspace', id='task', plan_id='plan')
        plan = MontageStageUseCase.include_adopted_sections(SimpleNamespace(repository=repository), task, plan)
    schedule = narration_schedule(plan.cues, plan.duration_seconds)
    assert len(schedule) == 12
    assert [(cue.text, cue.duration_seconds) for cue in plan.cues] == [(cue.text, cue.duration_seconds) for cue in cues]
    assert schedule[3].end_seconds == schedule[4].start_seconds == 11.2
    assert schedule[8].start_seconds == 20.967
    assert sum(item.end_seconds - item.start_seconds for item in schedule) == pytest.approx(29.92)
    assert plan.duration_seconds == 30
