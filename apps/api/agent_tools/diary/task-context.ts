import { clipFacts, subtitleFacts, narrationFacts, executionFacts, audioFacts, narrationSummary, taskObjectBinding, overlayFacts } from '../task_facts.ts';
import type {BusinessRequest} from './contracts.ts';

export function createTaskContext({request, taskId, stage, selectedRefs, playheadMs, verifyTaskVersion, initialTaskRead, initialScriptDraftUpdatedAt}: {
  request: BusinessRequest; taskId?: string; stage?: string; selectedRefs: string[]; playheadMs: number | null;
  verifyTaskVersion: (workspace: any) => void; initialTaskRead: boolean; initialScriptDraftUpdatedAt: string | null;
}) {
  let currentTaskRead = initialTaskRead;
  let currentScriptDraftUpdatedAt = initialScriptDraftUpdatedAt;
  async function readCurrentTaskFacts() {
    if (!taskId) throw new Error("当前创作任务不可用");
    const task = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}`);
    verifyTaskVersion(task);
    if (task.task?.id !== taskId) throw new Error("任务响应身份不一致，不能读取其他任务的事实");
    const execution = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/execution`);
    if (execution.task_id !== taskId || !("job" in execution)
        || (execution.job && execution.job.task_id !== taskId)) {
      throw new Error("后台安排响应不可读或任务身份不一致，不能推断为空或未排队");
    }
    const rawScriptContext = (task.task?.stage === "script" || task.task?.can_revise_script
        && !(task.scripts || []).find((item: any) => item.id === task.task.confirmed_script_id)?.locked_body_digest)
      ? await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/script-context`)
      : null;
    const draft = rawScriptContext?.draft;
    const scriptContext = rawScriptContext ? {...rawScriptContext, draft: draft ? {
      id: draft.id, title: draft.title, updated_at: draft.updated_at,
      sections_total_count: typeof draft.sections_total_count === "number" ? draft.sections_total_count : null,
      sections: null, sections_loaded: false, draft_complete: typeof draft.draft_complete === "boolean" ? draft.draft_complete : null,
      read_tool: "read_script_sections",
    } : null} : null;
    currentScriptDraftUpdatedAt = typeof scriptContext?.draft?.updated_at === "string"
      ? scriptContext.draft.updated_at : null;
    const allClips = task.montage?.clips || [];
    const selectedClipIds = new Set(selectedRefs.filter((ref: string) => ref.startsWith("clip:")).map((ref: string) => ref.slice(5)));
    const clips = allClips.flatMap((clip: any, index: number) => index < 24 || selectedClipIds.has(String(clip.id))
      ? [clipFacts(clip, index, task.montage?.cues || [])] : []);
    const allTracks = (task.subtitle_tracks || []).filter((track: any) => track.status === "valid");
    const subtitleTracks = allTracks.slice(0, 8).map((track: any) => subtitleFacts(track));
    const allNarration = task.narration?.segments || [];
    const narrationSegments = allNarration.slice(0, 24).map(narrationFacts);
    const metadata = (value: unknown, limit = 180) => typeof value === "string" ? value.slice(0, limit) : null;
    const plan = task.plan;
    const knowledgeReferences = plan?.knowledge_references || [];
    const sourceReferences = plan?.source_references || [];
    const profile = (value: Record<string, unknown> | null | undefined) => value ? {
      profile_id: metadata(value.profile_id, 120), version: metadata(value.version, 120),
    } : null;
    const confirmedScriptId = task.task.confirmed_script_id ?? null;
    const confirmedScript = confirmedScriptId ? task.scripts?.find((item: Record<string, unknown>) =>
      item.id === confirmedScriptId && item.task_id === taskId && item.selection_status === "confirmed") : null;
    if (confirmedScriptId && !confirmedScript) throw new Error("当前任务的已确认文案不可读或身份不一致，不能当作没有文案");
    const sectionCount = typeof confirmedScript?.sections_total_count === "number" ? confirmedScript.sections_total_count : null;
    const audioTracks = task.montage?.audio_timeline?.tracks || [];
    const overlays = task.montage?.graphic_overlays || [];
    const binding = taskObjectBinding(task);
    const context = {
      task_id: taskId,
      name: metadata(task.task?.name),
      stage: task.task?.stage,
      stage_meaning: '作品的编辑阶段；制作接续以execution.task_execution和真实作业回执为准，全机预算不表示当前任务需要运行全部阶段。',
      status: task.task?.status,
      stage_status: task.task?.stage_status,
      updated_at: task.task?.updated_at,
      stage_reason: metadata(task.task?.stage_reason),
      stage_impact: metadata(task.task?.stage_impact, 500),
      stage_repair_action: metadata(task.task?.stage_repair_action, 500),
      current_timeline_version_id: task.task?.current_timeline_version_id,
      timeline_sequence: task.task?.timeline_sequence,
      confirmed_script_id: confirmedScriptId,
      can_revise_script: task.task?.can_revise_script === true,
      confirmed_script: confirmedScript ? {
        id: confirmedScript.id, title: metadata(confirmedScript.title), language: confirmedScript.language,
        selection_status: confirmedScript.selection_status, updated_at: confirmedScript.updated_at,
        sections_total_count: sectionCount, sections: null, sections_loaded: false,
        draft_complete: typeof confirmedScript.draft_complete === "boolean" ? confirmedScript.draft_complete : null,
        read_tool: "read_script_sections",
      } : null,
      execution: executionFacts(execution),
      available_voices: (task.available_voices || []).slice(0, 24).map((voice: Record<string, unknown>) => ({
        profile_id: metadata(voice.profile_id, 120), version: metadata(voice.version, 120),
        display_name: metadata(voice.person_display_name, 160), language: voice.language,
        runnable: voice.runnable, runnable_reason: voice.runnable_reason,
      })),
      available_voice_count: (task.available_voices || []).length,
      available_voices_truncated: (task.available_voices || []).length > 24,
      plan: plan ? {
        id: plan.id, updated_at: plan.updated_at,
        source_media: plan.source_media ? {
          asset_id: plan.source_media.asset_id, asset_updated_at: plan.source_media.asset_updated_at,
          source_start_seconds: plan.source_media.source_start_seconds, source_end_seconds: plan.source_media.source_end_seconds,
          aspect_ratio: plan.source_media.aspect_ratio, preserve_audio: plan.source_media.preserve_audio,
        } : null,
        creation_intent: plan.creation_intent ? {kind: plan.creation_intent.kind, source: plan.creation_intent.source,
          topic_title: plan.creation_intent.topic_title,
          protected_body_digest: plan.creation_intent.final_script?.body_digest} : null,
        adoption: plan.adoption,
        goal: metadata(plan.goal, 1000), language: plan.language, aspect_ratio: plan.aspect_ratio,
        duration_target_seconds: plan.duration_target_seconds, target_count: plan.target_count,
        template_id: metadata(plan.template_id, 120), template_version: plan.template_version,
        selected_person: profile(plan.selected_person), selected_voice: profile(plan.selected_voice),
        knowledge_references: knowledgeReferences.slice(0, 50).map((item: Record<string, unknown>) => ({
          id: metadata(item.id, 120), version: metadata(item.version, 120), title: metadata(item.title),
        })),
        source_references: sourceReferences.slice(0, 50).map((item: Record<string, unknown>) => ({
          id: metadata(item.id, 120), identity: metadata(item.identity, 200), title: metadata(item.title),
        })),
        knowledge_reference_count: knowledgeReferences.length, source_reference_count: sourceReferences.length,
        references_truncated: knowledgeReferences.length > 50 || sourceReferences.length > 50,
      } : null,
      selected_refs: selectedRefs,
      selected_refs_meaning: "当前编辑器选中对象，独立于plan中的固定输入资料。为空仅表示没有选中编辑对象，不表示任务没有资料，也不代表使用授权。",
      selected_stage: stage,
      playhead_ms: playheadMs,
      clips,
      subtitle_tracks: subtitleTracks,
      narration_segments: narrationSegments,
      narration: narrationSummary(task),
      audio_tracks: audioFacts(task),
      audio_track_count: audioTracks.length,
      audio_tracks_truncated: audioTracks.length > 8,
      graphic_overlays: overlays.slice(0, 8).map(overlayFacts),
      graphic_overlay_count: overlays.length, graphic_overlays_truncated: overlays.length > 8,
      object_catalog: {binding, read_tool: "read_task_object", page_limit: 24,
        collections: [
          {kind: "clips", total_count: allClips.length},
          {kind: "subtitle_tracks", total_count: allTracks.length},
          {kind: "subtitle_cues", total_count: allTracks.reduce((n: number, track: any) => n + (track.cues || []).length, 0), parent_field: "subtitle_track_id"},
          {kind: "narration_segments", total_count: allNarration.length},
          {kind: "script_sections", total_count: sectionCount, parent_field: "script_id",
            script_id: confirmedScriptId, read_tool: "read_script_sections", updated_at: confirmedScript?.updated_at ?? null},
          {kind: "audio_tracks", total_count: audioTracks.length},
          {kind: "audio_clips", total_count: audioTracks.reduce((n: number, track: any) => n + (track.clips || []).length, 0), parent_field: "audio_track_id"},
          {kind: "graphic_overlays", total_count: overlays.length},
        ], instruction: "摘要可以截断；用kind分页发现全部对象，再按object_id读取。子对象可用parent_id限定；对象存在不代表动作许可。"},
      clip_count: allClips.length, clips_truncated: clips.length < allClips.length,
      subtitle_track_count: allTracks.length, subtitle_tracks_truncated: allTracks.length > 8,
      narration_segment_count: allNarration.length, narration_segments_truncated: allNarration.length > 24,
      units: { times: "seconds", playback_rate: "dimensionless multiplier", crop: "0–1 window position" },
      omitted_clip_count: Math.max(0, allClips.length - clips.length),
      ...(scriptContext ? { script_context: scriptContext } : {}),
    };
    currentTaskRead = true;
    return context;
  }

  return {readCurrentTaskFacts, isTaskRead: () => currentTaskRead, markTaskRead: () => {currentTaskRead = true;},
    getScriptDraftUpdatedAt: () => currentScriptDraftUpdatedAt,
    setScriptDraftUpdatedAt: (value: string) => {currentScriptDraftUpdatedAt = value;}};
}
