// Authorized API data only. Never expose local paths, tokens, or payload bodies.
export function boundedText(value: unknown, limit: number) {
  const text = typeof value === "string" ? value : "";
  return { text: text.slice(0, limit), text_length: text.length, text_truncated: text.length > limit };
}

function fields(value: any, names: string[]) {
  return Object.fromEntries(names.map(name => [name, value[name] ?? null]));
}

export function clipFacts(clip: any, index: number, cues: any[]) {
  const cue = cues.find(item => item.id === clip.cue_id);
  const text = boundedText(cue?.text, 2000);
  const reasons = (clip.match_reasons || []).map((reason: string) => boundedText(reason, 500));
  const sourceDuration = clip.source_end_seconds - clip.source_start_seconds;
  const timelineDuration = clip.timeline_end_seconds - clip.timeline_start_seconds;
  return {
    ...fields(clip, ["id", "asset_id", "segment_id", "cue_id", "person_render_id", "role", "locked",
      "source_start_seconds", "source_end_seconds", "timeline_start_seconds", "timeline_end_seconds",
      "playback_rate", "fit_mode", "crop_x", "crop_y", "transition_in_seconds", "needs_confirmation"]),
    index: index + 1, picture_only_reorder: !clip.person_render_id,
    playback_rate: Number.isFinite(sourceDuration) && sourceDuration > 0 && Number.isFinite(timelineDuration) && timelineDuration > 0
      ? sourceDuration / timelineDuration : null,
    playback_rate_source: "derived_from_source_and_timeline_intervals",
    start_seconds: clip.timeline_start_seconds, end_seconds: clip.timeline_end_seconds,
    narration_segment_id: cue?.narration_segment_id ?? null, source_section_id: cue?.source_section_id ?? null,
    cue_text: text.text, cue_text_length: text.text_length, cue_text_truncated: text.text_truncated,
    match_reasons: reasons.slice(0, 8).map(item => item.text), match_reason_count: reasons.length,
    match_reasons_truncated: reasons.length > 8 || reasons.some(item => item.text_truncated),
  };
}

export function subtitleFacts(track: any, cues: any[] = track.cues || [], offset = 0, limit = 24) {
  const styleKeys = ["enabled", "name", "font_family", "font_size", "stroke_width", "stroke_color", "background_color",
    "background_opacity", "primary_color", "emphasis_color", "position", "bilingual_order"];
  const style = track.style || {};
  return {
    ...fields(track, ["id", "language", "source_language", "timeline_version_id", "status"]),
    style: Object.fromEntries(Object.entries(style).filter(([key]) => styleKeys.includes(key))),
    style_omitted_keys: Object.keys(style).filter(key => !styleKeys.includes(key)),
    cue_count: cues.length, offset, cues_truncated: offset > 0 || offset + limit < cues.length,
    cues: cues.slice(offset, offset + limit).map(cue => ({
      ...fields(cue, ["id", "source_cue_id", "start_seconds", "end_seconds", "speaker", "line_breaks", "emphasis"]),
      ...boundedText(cue.text, 2000),
    })),
  };
}

export function narrationFacts(segment: any) {
  return {
    ...fields(segment, ["id", "source_section_id", "duration_seconds", "sample_rate", "model_id", "voice_profile_id",
      "voice_version", "language", "status", "source_kind", "source_artifact_ref"]),
    ...boundedText(segment.text, 4000),
  };
}

export function executionFacts(execution: any) {
  const job = execution.job;
  return {
    status: job?.status ?? null,
    job: job ? fields(job, ["id", "task_id", "status", "progress", "current_stage", "waiting_reason", "failure_reason",
      "repair_action", "result_ref", "result_kind", "result_url", "created_at", "updated_at"]) : null,
    current_stage: execution.current_stage ?? null, waiting_reason: execution.waiting_reason ?? null,
    updated_at: execution.status_updated_at ?? null,
    meaning: job ? "后台安排来自独立执行记录，不能从任务阶段推断。" : "服务端本次未返回该任务的后台安排记录。",
  };
}

export function audioFacts(task: any) {
  const tracks = task.audio_timeline?.tracks || [];
  return tracks.slice(0, 8).map((track: any) => {
    const clips = track.clips || [];
    return {id: track.id, kind: track.kind, name: boundedText(track.name, 120), clip_count: clips.length,
      clips_truncated: clips.length > 24,
      clips: clips.slice(0, 24).map((clip: any) => ({
        ...fields(clip, ["id", "source_start_ms", "source_end_ms", "timeline_start_ms", "playback_rate", "gain_db",
          "fade_in_ms", "fade_out_ms", "muted", "locked", "denoise_db", "loudness_target_lufs"]),
        duration_ms: (clip.source_end_ms - clip.source_start_ms) / clip.playback_rate,
        narration_segment_ids: (task.narration?.segments || []).filter((segment: any) => segment.audio_reference === clip.source_ref).map((segment: any) => segment.id),
        linked_clip_ids: (task.montage?.clips || []).filter((shot: any) => shot.source_reference === clip.source_ref).map((shot: any) => shot.id).slice(0, 24),
        association_source: "matching_recorded_source_reference", units: "milliseconds",
      }))};
  });
}
