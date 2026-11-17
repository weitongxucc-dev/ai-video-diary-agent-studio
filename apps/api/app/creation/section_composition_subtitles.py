"""Bind composed narration captions to the new version without losing display choices."""
from dataclasses import replace

from app.creation.contracts import SubtitleCue
from app.creation.transactions import clone_subtitle_track
from app.subtitle_schedule import narration_schedule


def compose_subtitles(metadata, task, base, version, montage):
    from app.creation.editing.metadata_use_case import execution_subtitle_track
    from app.subtitle_tracks import render_srt

    tracks = metadata.current_subtitle_tracks(task.workspace_id, task, current_version=base)
    result = []
    schedule = narration_schedule(montage.cues, montage.duration_seconds)
    from app.montage import creation_task_montage_plan_from_payload
    before = creation_task_montage_plan_from_payload(base.snapshot['montage'])
    before_schedule = narration_schedule(before.cues, before.duration_seconds)
    def clock(cues, captions):
        return [(cue.id, cue.text, round(caption.start_seconds, 3), round(caption.end_seconds, 3))
            for cue, caption in zip(cues, captions, strict=True)]
    if clock(before.cues, before_schedule) == clock(montage.cues, schedule):
        # Picture-only adoption must preserve manual caption timing/text and translations.
        return [clone_subtitle_track(track, timeline_version_id=version.id) for track in tracks]
    for track in tracks:
        if track.generation_method != 'timeline':
            # A changed source script cannot silently keep an old translation valid.
            result.append(replace(clone_subtitle_track(track, timeline_version_id=version.id),
                artifact_ref=None, transform_id=None, status='unavailable',
                failure_reason='段落已重新组合，译文须重新核对', impact='旧译文不进入当前成片',
                repair_action='按当前正文重新生成或复核翻译字幕'))
            continue
        previous = {cue.id: cue for cue in track.cues}
        cues = []
        for source, caption in zip(montage.cues, schedule, strict=True):
            prior = previous.get(source.id)
            cue = prior if prior is not None and prior.text == source.text else SubtitleCue(
                id=source.id, source_cue_id=None, text=source.text, start_seconds=caption.start_seconds,
                end_seconds=caption.end_seconds, emphasis=source.subtitle_emphasis or [])
            cues.append(replace(cue, start_seconds=round(caption.start_seconds, 3), end_seconds=round(caption.end_seconds, 3)))
        updated = clone_subtitle_track(track, timeline_version_id=version.id, cues=cues)
        reference, transform = metadata.publish_subtitle_text(workspace_id=task.workspace_id, task_id=task.id,
            proposal_id=version.id, kind='subtitle_section_composition', input_refs=[f'timeline:{base.id}',
                *base.snapshot.get('stage_segments', {}).values()], content=render_srt(execution_subtitle_track(updated)),
            engine='timeline-clock/v1', parameters={'language': track.language})
        result.append(clone_subtitle_track(updated, timeline_version_id=version.id,
            artifact_ref=reference, transform_id=transform))
    return result
