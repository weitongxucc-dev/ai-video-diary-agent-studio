"""A candidate's saved picture and audio projected onto one short preview window."""
from dataclasses import asdict, replace

from app.audio_timeline import effective_audio_timeline
from app.creation.clip_preview import ClipPreviewConflict, ClipPreviewUnavailable
from app.creation.narration_duration import DELIVERY_FRAME_RATE
from app.linked_timeline import splice_audio
from app.montage import creation_task_montage_plan_to_payload
from app.visual_effects import clip_playback_rate


def seal_candidate_picture(case, task, actor, plan, clip, source, *, connection):
    if plan.audio_timeline is None and not plan.narration_audio_path:
        raise ClipPreviewConflict("该预览区间没有可播放的音轨，可生成静音预览。")
    rate = DELIVERY_FRAME_RATE
    first = round(clip.timeline_start_seconds * rate)
    shot_end = round(clip.timeline_end_seconds * rate)
    last = min(shot_end, first + 5 * rate)
    if first >= last:
        raise ValueError("镜头区间不足一个输出帧")
    start_ms, end_ms = round(first * 1000 / rate), round(last * 1000 / rate)
    graph = effective_audio_timeline(plan).model_copy(deep=True)
    # Locks apply to saved edits. The preview never changes the saved graph.
    graph.tracks = [track for track in graph.tracks if not track.muted]
    for track in graph.tracks:
        track.locked = False
        track.clips = [item for item in track.clips if not item.muted]
        for item in track.clips:
            item.locked = False
    window = splice_audio(graph, [(start_ms, end_ms, 0)])
    if not any(track.clips for track in window.tracks):
        raise ClipPreviewConflict("该预览区间没有可播放的音轨，可生成静音预览。")

    render_budget, audio_budget = case.candidate_picture_budgets()
    if render_budget is None or audio_budget is None:
        raise ClipPreviewUnavailable("本机有声预览预算尚未标定，未接收媒体作业")
    renderer = case.candidate_picture_renderer()
    duration = (last - first) / rate
    source_end = min(clip.source_end_seconds, clip.source_start_seconds + duration * clip_playback_rate(clip))
    local_clip = replace(clip, timeline_start_seconds=0, timeline_end_seconds=duration,
        source_end_seconds=source_end, transition_in_seconds=0,
        fade_envelope_duration_frames=clip.fade_envelope_duration_frames or (shot_end - first))
    preview = replace(plan, status="ready", clips=[local_clip], cues=[], graphic_overlays=[],
        duration_seconds=duration, audio_timeline=window, narration_audio_path=None,
        output_path=None, subtitle_path=None, delivery_window=None, duration_requirement=None)
    files, _ = renderer.qualified_picture_sources(task.workspace_id, task, preview, [preview], actor,
        connection=connection)
    mix = renderer.audio.mix_facts(task.workspace_id, task.id, window, duration, actor=actor,
        normalize_voice_sources=preview.narration_linkage_created_audio_timeline, budget=audio_budget,
        connection=connection)
    facts = {"clip": asdict(clip), "files": files, "source_path": str(source),
        "window_frames": [first, last], "audio_sources": mix["sources"],
        "picture_input": {"plan": creation_task_montage_plan_to_payload(preview), "subtitle_tracks": []}}
    resources = renderer.picture_resources(preview, {}, [preview], facts, render_budget)
    resources.update(audio_mix=mix["resources"], silent=False)
    return facts, resources


def candidate_picture_publication(facts):
    media_input = facts["picture_input"]
    clip, = media_input["plan"]["clips"]
    parameters = {"base_version_id": facts["base_version_id"], "side": "after", "clip_id": clip["id"],
        "max_seconds": 5, "silent": False, "candidate_id": facts["candidate_id"],
        "formal_base_version_id": facts["formal_base_version_id"], "window_frames": facts["window_frames"]}
    references = list(dict.fromkeys([f"timeline:{facts['base_version_id']}", clip["source_path"],
        *(item["reference"] for item in facts["audio_sources"])]))
    return parameters, references, media_input
