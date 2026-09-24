const apiBase = process.env.AI_VIDEO_DIARY_AGENT_API_BASE;
const taskId = process.env.AI_VIDEO_DIARY_AGENT_TASK_ID;
const stage = process.env.AI_VIDEO_DIARY_AGENT_STAGE;
const selectedRefs = JSON.parse(process.env.AI_VIDEO_DIARY_AGENT_SELECTED_REFS || "[]");
const playheadMs = process.env.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS
  ? Number(process.env.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS)
  : null;
let previewCreated = false;

async function request(path: string, init?: RequestInit) {
  if (!apiBase) throw new Error("本地业务服务不可用");
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (process.env.AI_VIDEO_DIARY_AGENT_AUTHORIZATION) {
    headers.set("Authorization", process.env.AI_VIDEO_DIARY_AGENT_AUTHORIZATION);
  }
  if (process.env.AI_VIDEO_DIARY_AGENT_COOKIE) {
    headers.set("Cookie", process.env.AI_VIDEO_DIARY_AGENT_COOKIE);
  }
  const response = await fetch(`${apiBase}${path}`, { ...init, headers, signal: AbortSignal.timeout(120000) });
  const body = await response.json();
  if (!response.ok) {
    const reason = typeof body.detail === "string" ? body.detail : body.detail?.reason;
    throw new Error(reason || `业务操作失败（${response.status}）`);
  }
  return body;
}

export default function (pi: { registerTool: (tool: unknown) => void }) {
  pi.registerTool({
    name: "read_workspace_context",
    label: "读取当前工作区",
    description: "读取当前工作区最近的创作任务摘要，了解已有任务；不可把历史任务当成新任务结果。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const data = await request("/api/creation-tasks");
      const tasks = (data.items || []).slice(0, 8).map((item: Record<string, unknown>) => ({
        id: item.id, name: item.name, stage: item.stage, status: item.status,
      }));
      return { content: [{ type: "text", text: JSON.stringify({ tasks }) }], details: { tasks } };
    },
  });

  pi.registerTool({
    name: "read_video_templates",
    label: "读取我的模板",
    description: "用户要求使用已有模板时，读取当前工作区真实模板名称、ID和版本。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const data = await request("/api/video-templates");
      const allTemplates = (data.items || []) as Record<string, unknown>[];
      const templates = [...allTemplates.filter((item) => item.origin === "user"), ...allTemplates.filter((item) => item.origin !== "user")]
        .slice(0, 50).map((item: Record<string, unknown>) => ({
        id: item.id, name: item.name, version: item.version, origin: item.origin,
      }));
      const truncated = allTemplates.length > templates.length;
      return { content: [{ type: "text", text: JSON.stringify({ templates, truncated }) }], details: { templates, truncated } };
    },
  });

  pi.registerTool({
    name: "find_materials",
    label: "检索可用素材",
    description: "按文字检索当前工作区已审核且允许直接用片的镜头，只返回引用和匹配原因，不读取媒体文件。",
    parameters: {
      type: "object",
      properties: { query: { type: "string", minLength: 1, maxLength: 120 } },
      required: ["query"], additionalProperties: false,
    },
    execute: async (_id: string, params: { query: string }) => {
      const matches = await request(`/api/production/materials/search?q=${encodeURIComponent(params.query)}&limit=8`);
      const excerpts = matches.map((item: {asset_ref: string; segment_ref: string; score: number; reasons: string[]}) => ({
        asset_ref: item.asset_ref, segment_ref: item.segment_ref, score: item.score,
        reasons: item.reasons.slice(0, 3).map((reason) => reason.slice(0, 160)),
      }));
      return { content: [{ type: "text", text: JSON.stringify(excerpts) }], details: { matches: excerpts } };
    },
  });

  pi.registerTool({
    name: "preview_creation_plan",
    label: "预检新视频方案",
    description: "根据用户的新视频目标生成工作区真实预检。只预检，不创建任务；必须向用户展示影响和阻断，等待明确确认。",
    parameters: {
      type: "object",
      properties: {
        goal: { type: "string", minLength: 1, maxLength: 2000 },
        name: { type: "string", minLength: 1, maxLength: 120 },
        language: { type: "string", enum: ["zh-CN", "en-US", "zh-CN/en-US"] },
        aspect_ratio: { type: "string", enum: ["9:16", "16:9", "1:1"] },
        duration_target_seconds: { type: "integer", minimum: 15, maximum: 600 },
        presenter_share_target: { type: "integer", enum: [0, 20, 40, 60, 80, 100] },
        lipsync_quality: { type: "string", enum: ["high", "fast"] },
        template_id: { type: "string", minLength: 1, maxLength: 120 },
        template_version: { type: "integer", minimum: 1 },
      },
      required: ["goal"],
      additionalProperties: false,
    },
    execute: async (_id: string, params: { goal: string; name?: string; language?: string; aspect_ratio?: string; duration_target_seconds?: number; presenter_share_target?: number; lipsync_quality?: "high" | "fast"; template_id?: string; template_version?: number }) => {
      if (previewCreated) throw new Error("本轮已生成新视频预检，请用户检查后再决定下一步");
      const preview = await request("/api/creation-plans/preview", {
        method: "POST",
        body: JSON.stringify({
          goal: params.goal,
          ...(params.name ? { name: params.name } : {}),
          target_count: 1,
          language: params.language || "zh-CN",
          aspect_ratio: params.aspect_ratio || "9:16",
          duration_target_seconds: params.duration_target_seconds || 60,
          ...((params.presenter_share_target !== undefined || !params.template_id)
            ? { presenter_share_target: params.presenter_share_target ?? 20 } : {}),
          ...((params.lipsync_quality !== undefined || !params.template_id)
            ? { lipsync_quality: params.lipsync_quality ?? "high" } : {}),
          ...(params.template_id ? { template_id: params.template_id, template_version: params.template_version } : {}),
        }),
      });
      previewCreated = true;
      return {
        content: [{ type: "text", text: JSON.stringify({
          name: preview.name, goal: preview.goal, language: preview.language,
          aspect_ratio: preview.aspect_ratio, duration_target_seconds: preview.duration_target_seconds,
          presenter_share_target: preview.presenter_share_target,
          lipsync_quality: preview.lipsync_quality,
          approved_clip_count: preview.approved_clip_count, confirmable: preview.confirmable,
          blockers: preview.blockers,
          instruction: "预检不会创建任务。请让用户检查方案并手动确认。",
        }) }],
        details: { kind: "plan_preview", preview },
      };
    },
  });

  pi.registerTool({
    name: "read_current_task",
    label: "读取当前创作任务",
    description: "读取当前任务、阶段和时间线摘要。处理修改要求前先调用，不得把对话记录当作任务状态。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      if (!taskId) throw new Error("当前创作任务不可用");
      const task = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}`);
      const allClips = task.montage?.clips || [];
      const selectedClipIds = new Set(selectedRefs.filter((ref: string) => ref.startsWith("clip:")).map((ref: string) => ref.slice(5)));
      const clips = allClips.filter((clip: Record<string, unknown>, index: number) => index < 24 || selectedClipIds.has(String(clip.id)))
        .map((clip: Record<string, unknown>) => ({
        index: allClips.indexOf(clip) + 1,
        id: clip.id,
        asset_id: clip.asset_id,
        cue_text: String(task.montage?.cues?.find((cue: {id: string}) => cue.id === clip.cue_id)?.text || "").slice(0, 180),
        match_reasons: (Array.isArray(clip.match_reasons) ? clip.match_reasons : []).slice(0, 3).map((reason: unknown) => String(reason).slice(0, 160)),
        start_seconds: clip.timeline_start_seconds,
        end_seconds: clip.timeline_end_seconds,
        locked: clip.locked,
      }));
      const subtitleTracks = (task.subtitle_tracks || []).filter((track: Record<string, unknown>) => track.status === "valid")
        .map((track: Record<string, unknown>) => ({
          id: track.id,
          language: track.language,
          cues: (Array.isArray(track.cues) ? track.cues : []).slice(0, 24).map((cue: Record<string, unknown>) => ({
            id: cue.id, text: String(cue.text || "").slice(0, 120),
          })),
        }));
      const narrationSegments = (task.narration?.segments || []).slice(0, 24).map((segment: Record<string, unknown>) => ({
        id: segment.id, text: String(segment.text || "").slice(0, 120),
      }));
      const context = {
        task_id: taskId,
        name: task.task?.name,
        stage: task.task?.stage,
        current_timeline_version_id: task.task?.current_timeline_version_id,
        selected_refs: selectedRefs,
        selected_stage: stage,
        playhead_ms: playheadMs,
        clips,
        subtitle_tracks: subtitleTracks,
        narration_segments: narrationSegments,
        omitted_clip_count: Math.max(0, allClips.length - clips.length),
      };
      return { content: [{ type: "text", text: JSON.stringify(context) }], details: context };
    },
  });

  pi.registerTool({
    name: "find_knowledge",
    label: "检索已保存资料",
    description: "在当前工作区已保存的资料中查找与问题有关的事实。只返回短摘录和来源；无结果时明确说明。",
    parameters: {
      type: "object",
      properties: { query: { type: "string", minLength: 1, maxLength: 120 } },
      required: ["query"],
      additionalProperties: false,
    },
    execute: async (_id: string, params: { query: string }) => {
      const data = await request("/api/knowledge-items");
      const terms = params.query.toLowerCase().split(/\s+/).filter(Boolean);
      const matches = (data.items || [])
        .filter((item: Record<string, unknown>) => {
          const text = `${item.title || ""} ${item.body || ""}`.toLowerCase();
          return terms.some((term) => text.includes(term));
        })
        .slice(0, 5)
        .map((item: Record<string, unknown>) => ({
          id: item.id,
          title: item.title,
          version: item.version,
          excerpt: String(item.body || "").slice(0, 900),
        }));
      return { content: [{ type: "text", text: JSON.stringify(matches) }], details: { matches } };
    },
  });

  pi.registerTool({
    name: "preview_edit",
    label: "预览剪辑修改",
    description: "对当前任务生成真实修改预览。没有界面选中对象时，可从 read_current_task 选择用户明确指出的镜头、字幕轨或配音段；不明确时先询问。只预览，不确认执行。",
    parameters: {
      type: "object",
      properties: {
        command: { type: "string", minLength: 2, maxLength: 500 },
        context_stage: { type: "string", enum: ["topic", "script", "narration", "montage", "review", "subtitle_cover"] },
        selected_refs: { type: "array", items: { type: "string" }, maxItems: 8 },
      },
      required: ["command"],
      additionalProperties: false,
    },
    execute: async (_id: string, params: { command: string; context_stage?: string; selected_refs?: string[] }) => {
      const effectiveStage = params.context_stage || stage;
      const effectiveRefs = params.selected_refs?.length ? params.selected_refs : selectedRefs;
      if (!effectiveStage) throw new Error("请先选择要修改的任务阶段");
      if (previewCreated) throw new Error("本轮已生成修改预览，请用户检查后再决定下一步");
      const preview = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/preview`, {
        method: "POST",
        body: JSON.stringify({
          raw_command: params.command,
          context_stage: effectiveStage,
          selected_object_refs: effectiveRefs,
          playhead_ms: playheadMs,
        }),
      });
      previewCreated = true;
      return {
        content: [{ type: "text", text: JSON.stringify({
          proposal_id: preview.proposal.id,
          summary: preview.proposal.summary,
          invalidated_refs: preview.proposal.invalidated_refs,
          status: preview.proposal.status,
          instruction: "告诉用户预览已生成，必须由用户确认后才能执行。",
        }) }],
        details: { kind: "edit_preview", preview },
      };
    },
  });
}
