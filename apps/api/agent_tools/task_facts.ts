// Authorized API data only. Never expose local paths, tokens, or payload bodies.
export function boundedText(value: unknown, limit: number) {
  const text = typeof value === "string" ? value : "";
  return { text: text.slice(0, limit), text_length: text.length, text_truncated: text.length > limit };
}

function fields(value: any, names: string[]) {
  return Object.fromEntries(names.map(name => [name, value[name] ?? null]));
}

function logicalReference(value: unknown) {
  if (typeof value !== "string" || !value) return null;
  if (value.startsWith("narration://") || value.startsWith("workspace://")) return value;
  if (value.includes(":") || value.includes("\\") || value.startsWith("/")
      || value.split("/").some(part => !part || part === "." || part === "..")) return null;
  return value;
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
      "playback_rate", "fit_mode", "crop_x", "crop_y", "transition_in_seconds", "needs_confirmation", "manually_selected",
      "fade_envelope_rate", "fade_envelope_offset_frames", "fade_envelope_duration_frames"]),
    object_ref: `clip:${clip.id}`, units: {time: "seconds", crop: "0–1", playback_rate: "dimensionless multiplier",
      fade_envelope_frames: "30 fps effect clock", fade_envelope_rate: "effect clock multiplier",
      visual_effects: {exposure: "EV", fade_in_seconds: "seconds", fade_out_seconds: "seconds",
        contrast: "multiplier", saturation: "multiplier", temperature: "−1–1",
        zoom: "multiplier", offset_x: "signed canvas fraction", offset_y: "signed canvas fraction", rotation: "degrees"}},
    source_reference: logicalReference(clip.source_reference),
    visual_effects: fields(clip.visual_effects || {}, ["fade_in_seconds", "fade_out_seconds", "zoom", "offset_x", "offset_y",
      "rotation", "beauty_strength", "skin_exposure", "exposure", "contrast", "saturation", "temperature"]),
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
    ...fields(track, ["id", "language", "source_language", "timeline_version_id", "status", "updated_at"]),
    object_ref: `subtitle-track:${track.id}`, revision: track.updated_at ?? null,
    revision_source: "subtitle_track_updated_at", units: {time: "seconds", font_size: "pixels"},
    style: Object.fromEntries(Object.entries(style).filter(([key]) => styleKeys.includes(key))),
    style_omitted_keys: Object.keys(style).filter(key => !styleKeys.includes(key)),
    cue_count: cues.length, offset, cues_truncated: offset > 0 || offset + limit < cues.length,
    cues: cues.slice(offset, offset + limit).map(cue => subtitleCueFacts(cue, track)),
  };
}

export function subtitleCueFacts(cue: any, track: any) {
  return {...fields(cue, ["id", "source_cue_id", "start_seconds", "end_seconds", "speaker", "line_breaks", "emphasis"]),
    object_ref: `cue:${cue.id}`, subtitle_track_id: track.id, language: track.language,
    timeline_version_id: track.timeline_version_id, revision: track.updated_at ?? null,
    revision_source: "parent_subtitle_track_updated_at", units: "seconds", ...boundedText(cue.text, 2000)};
}

export function narrationFacts(segment: any) {
  return {
    ...fields(segment, ["id", "source_section_id", "duration_seconds", "sample_rate", "model_id", "voice_profile_id",
      "voice_version", "language", "status", "source_kind", "source_sha256", "source_script_digest"]),
    object_ref: `narration:${segment.id}`, units: {duration: "seconds", sample_rate: "Hz"},
    revision: segment.source_sha256 ?? null, revision_source: "source_sha256",
    source_artifact_ref: logicalReference(segment.source_artifact_ref),
    ...boundedText(segment.text, 4000),
  };
}

export function narrationSummary(task: any) {
  const narration=task.narration;
  return {
    has_audio_reference: typeof narration?.audio_reference === "string" && narration.audio_reference.length > 0,
    audio_reference: logicalReference(narration?.audio_reference),
    metadata_reference: logicalReference(narration?.audio_reference),
    metadata_meaning: "时长/采样率/模型/声音元数据属于此登记配音引用，不自动代表montage当前引用。",
    montage_narration_reference: logicalReference(task.montage?.narration_reference),
    montage_uses_registered_narration: typeof narration?.audio_reference === "string"
      && narration.audio_reference===task.montage?.narration_reference,
    duration_seconds: narration?.duration_seconds ?? null, sample_rate: narration?.sample_rate ?? null,
    ...(narration && Object.hasOwn(narration,"status") ? {status:narration.status} : {}),
    language: narration?.language ?? null, model_id:narration?.model_id ?? null,
    voice_profile_id:narration?.voice_profile_id ?? null, source_kind:narration?.source_kind ?? null,
    segment_count: narration?.segments?.length ?? 0,
    meaning: "分段数量为0只表示没有分段记录，不代表没有已存在音频；未选声音档案也不代表没有音频。",
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

export function audioClipFacts(clip: any, track: any, task: any) {
  const linkedClips = (task.montage?.clips || []).filter((shot: any) => shot.source_reference === clip.source_ref);
  return {...fields(clip, ["id", "source_start_ms", "source_end_ms", "timeline_start_ms", "playback_rate", "gain_db",
    "fade_in_ms", "fade_out_ms", "muted", "locked", "denoise_db", "loudness_target_lufs", "envelope_offset_ms", "envelope_duration_ms"]),
    object_ref: `audio:${clip.id}`, name: boundedText(clip.name, 120), audio_track_id: track.id, track_role: track.role,
    source_ref: logicalReference(clip.source_ref),
    duration_ms: Math.round((clip.source_end_ms - clip.source_start_ms) / clip.playback_rate),
    editor_gain_points: (clip.editor_gain_points || []).map((point: any) => fields(point, ["offset_ms", "gain_db"])),
    narration_segment_ids: (task.narration?.segments || []).filter((segment: any) => segment.audio_reference === clip.source_ref).map((segment: any) => segment.id),
    linked_clip_ids: linkedClips.slice(0, 24).map((shot: any) => shot.id), linked_clip_count: linkedClips.length,
    linked_clips_truncated: linkedClips.length > 24,
    association_source: "matching_recorded_source_reference", units: {time: "milliseconds", gain: "dB", loudness: "LUFS",
      editor_gain_point_offset: "milliseconds from current clip start; read-only in the current typed mix action"}};
}

export function audioTrackFacts(track: any, task: any, clipLimit = 24) {
  const clips = track.clips || [];
  return {...fields(track, ["id", "role", "gain_db", "muted", "locked", "duck_under_voice"]),
    object_ref: `audio:${track.id}`, name: boundedText(track.name, 120), clip_count: clips.length,
    clips_truncated: clips.length > clipLimit, units: {gain: "dB"},
    clips: clips.slice(0, clipLimit).map((clip: any) => audioClipFacts(clip, track, task))};
}

// Homepage summary only. Fine reads build from the complete authorized collections below.
export function audioFacts(task: any) {
  return (task.montage?.audio_timeline?.tracks || []).slice(0, 8).map((track: any) => audioTrackFacts(track, task));
}

export function overlayFacts(overlay: any) {
  return {...fields(overlay, ["id", "kind", "start_seconds", "end_seconds", "x", "y", "width", "font_size",
    "font_family", "color", "opacity", "position"]), object_ref: `overlay:${overlay.id}`,
    ...(overlay.kind === "text" ? {text: boundedText(overlay.text, 200)} : {}),
    ...(overlay.kind === "image" || overlay.kind === "arrow" ? {name: boundedText(overlay.name, 120)} : {}),
    ...(overlay.kind === "image" ? {source_ref: logicalReference(overlay.source_ref)} : {}),
    ...(overlay.kind === "arrow" ? {purpose: boundedText(overlay.purpose, 64)} : {}),
    units: {time: "seconds", position: "normalized 0–1", width: "canvas fraction", font_size: "pixels"}};
}

export function scriptSectionFacts(section: any, script: any) {
  const evidence = section.evidence_reference_ids || [];
  return {id: section.id, script_id: script.id,
    revision: script.updated_at ?? null, revision_source: "parent_script_updated_at",
    title: boundedText(section.title, 160), ...boundedText(section.content, 8000),
    visual_prompt: boundedText(section.visual_prompt, 2000), evidence_reference_ids: evidence.slice(0, 24),
    evidence_reference_count: evidence.length, evidence_truncated: evidence.length > 24};
}

export function taskObjectCollections(task: any) {
  const tracks = (task.subtitle_tracks || []).filter((track: any) => track.status === "valid");
  const audio = task.montage?.audio_timeline?.tracks || [];
  const script = (task.scripts || []).find((item: any) => item.id === task.task.confirmed_script_id
    && item.task_id === task.task.id && item.selection_status === "confirmed");
  return {
    clips: (task.montage?.clips || []).map((clip: any, index: number) => clipFacts(clip, index, task.montage?.cues || [])),
    subtitle_tracks: tracks.map((track: any) => subtitleFacts(track, track.cues || [], 0, 0)),
    subtitle_cues: tracks.flatMap((track: any) => (track.cues || []).map((cue: any) => subtitleCueFacts(cue, track))),
    narration_segments: (task.narration?.segments || []).map(narrationFacts),
    script_sections: (script?.sections || []).map((section: any) => scriptSectionFacts(section, script)),
    audio_tracks: audio.map((track: any) => audioTrackFacts(track, task, 0)),
    audio_clips: audio.flatMap((track: any) => (track.clips || []).map((clip: any) => audioClipFacts(clip, track, task))),
    graphic_overlays: (task.montage?.graphic_overlays || []).map(overlayFacts),
  };
}

export function taskObjectBinding(task: any) {
  return {task_id: task.task.id, timeline_version_id: task.task.current_timeline_version_id ?? null,
    timeline_sequence: task.task.timeline_sequence, task_updated_at: task.task.updated_at ?? null,
    expected_version_meaning: "读取当前授权任务版本；宿主发送时绑定的版本不一致会拒绝，细节不能用于历史版本修改。"};
}
