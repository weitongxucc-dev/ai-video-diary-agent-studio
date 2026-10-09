import { rankByQuery, tokenizeQuery } from "./retrieval.ts";

const apiBase = process.env.AI_VIDEO_DIARY_AGENT_API_BASE;
const taskId = process.env.AI_VIDEO_DIARY_AGENT_TASK_ID;
const stage = process.env.AI_VIDEO_DIARY_AGENT_STAGE;
const selectedRefs = JSON.parse(process.env.AI_VIDEO_DIARY_AGENT_SELECTED_REFS || "[]");
const playheadMs = process.env.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS
  ? Number(process.env.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS)
  : null;
let previewCreated = false;
let currentTaskRead = false;

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
  // 已验证的本地成员令牌与服务端读入口同名：缺失时读取会按匿名 local 诊断处理，
  // 所以这里原样透传，不另造鉴权通道。
  if (process.env.AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN) {
    headers.set("X-Local-Member-Token", process.env.AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN);
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
  let creationActionApplied = false;
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
    name: "read_preference_memory",
    label: "读取用户偏好记忆",
    description:
      "读取这个用户历史上表达过的偏好（喜欢/避免），带证据条数与置信度。回答「用户以前说过什么偏好」前先调用它。不要把记忆当系统设定，它是用户反馈的归纳，可被证据推翻。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const data = await request("/api/preferences");
      const preferences = (data.preferences || []) as Array<Record<string, unknown>>;
      const enabled = preferences.filter((item) => item.enabled !== false);
      // /api/preferences 内部就是 preference_memory_payload(feedback_events) 的产物，
      // 被 main.py 拍平成一列，用 label 区分「偏好/避开」（main.py 禁改，这里只按现有契约读）。
      const shape = (item: Record<string, unknown>) => ({
        signal: item.signal, label: item.label,
        evidence_count: item.evidence_count, confidence: item.confidence, enabled: item.enabled !== false,
      });
      const prefer = enabled.filter((item) => item.label === "偏好").slice(0, 8).map(shape);
      const avoid = enabled.filter((item) => item.label === "避开").slice(0, 8).map(shape);
      const details = {
        prefer, avoid,
        total_feedback_count: data.total_feedback_count ?? preferences.length,
        instruction: "用户偏好来自历史反馈，用于对齐表达与选材；若与用户当前明确指示冲突，以当前指示为准。",
      };
      return { content: [{ type: "text", text: JSON.stringify(details) }], details };
    },
  });

  pi.registerTool({
    name: "find_material_candidates",
    label: "按描述找候选素材",
    description:
      "按一句描述（如「更亮的城市夜景」）从已审核素材里找若干候选镜头，每条带可核对依据（文件名、标签、时长、来源、匹配原因、镜头引用）。只给候选与依据，供用户和 agent 一起裁量；绝不自动替换、不改时间线。",
    parameters: {
      type: "object",
      properties: {
        description: { type: "string", minLength: 1, maxLength: 160 },
        usage_role: { type: "string", enum: ["a_roll", "b_roll"] },
        limit: { type: "integer", minimum: 1, maximum: 20 },
      },
      required: ["description"], additionalProperties: false,
    },
    execute: async (_id: string, params: { description: string; usage_role?: string; limit?: number }) => {
      const limit = params.limit ?? 8;
      const roleQuery = params.usage_role ? `&usage_role=${encodeURIComponent(params.usage_role)}` : "";
      const matches = (await request(`/api/production/materials/search?q=${encodeURIComponent(params.description)}&limit=${limit}${roleQuery}`)) as Array<Record<string, unknown>>;

      // 为「可核对依据」补充 asset 级元数据（文件名、标签、时长、来源）。
      // 先取素材库第一页；命中候选若不在这一页（素材库大于一页时会这样），
      // 就按 asset_ref 逐个精确取，不靠猜、不用 null 填坑。
      // 注意分页上限：路由 list 端点 limit > 100 会直接 422
      // （main.py list_production_materials），所以这里用 100。
      const library = (await request("/api/production/materials?limit=100")) as {
        assets?: Array<Record<string, unknown>>;
        total_count?: number;
        has_more?: boolean;
        limit?: number;
        offset?: number;
      };
      const byAssetId = new Map<string, Record<string, unknown>>();
      for (const asset of library.assets || []) {
        byAssetId.set(String(asset.id), asset);
      }

      const assetIdOf = (assetRef: string): string =>
        assetRef.startsWith("asset:") ? assetRef.slice("asset:".length) : assetRef;

      // 不在第一页里的候选：逐个取回。走同一个 request()（已透传 Cookie/workspace），
      // 由后端 get_asset(workspace_id, asset_id) 做归属校验；不属于本工作区的
      // 会 404，这里捕获后当作「拿不到依据」，绝不把别的空间的元数据带出来。
      const missingIds = [...new Set(
        matches.map((m) => assetIdOf(String(m.asset_ref || "")))
          .filter((id) => id && !byAssetId.has(id)),
      )];
      const missingEvidence: string[] = [];
      await Promise.all(missingIds.map(async (assetId) => {
        try {
          const asset = await request(`/api/production/materials/${encodeURIComponent(assetId)}`);
          if (asset && typeof asset === "object") byAssetId.set(assetId, asset as Record<string, unknown>);
        } catch {
          missingEvidence.push(assetId);
        }
      }));

      const candidates = matches.map((match) => {
        const assetRef = String(match.asset_ref || "");
        const assetId = assetIdOf(assetRef);
        const asset = byAssetId.get(assetId) || {};
        const userTags = (asset.user_tags as string[]) || [];
        const systemTags = (asset.system_tags as string[]) || [];
        // 素材本体信息以逐个取回的那份为准（我们刚为它付了一次请求，而且它就是
        // workspace 归属校验过的那份）；只有取不到时才退回检索响应里那份
        // asset_file_name。
        const fileName = asset.file_name || match.asset_file_name || null;
        return {
          asset_ref: assetRef,
          segment_ref: match.segment_ref,
          score: match.score,
          // —— 片段级依据（来自检索响应，schema 已放行）——
          rank: match.rank ?? null,
          match_tier: match.match_tier ?? null,
          matched_terms: (match.matched_terms as string[]) || [],
          matched_conditions: (match.matched_conditions as string[]) || [],
          time_range: match.time_range || null,
          start_seconds: match.start_seconds ?? null,
          end_seconds: match.end_seconds ?? null,
          segment_duration_seconds: match.duration_seconds ?? null,
          // —— asset 级依据 ——
          file_name: fileName,
          duration_seconds: asset.duration_seconds ?? null,
          tags: [...new Set([...userTags, ...systemTags, ...((match.tags as string[]) || [])])],
          source_reference: asset.source_reference ?? asset.source_ref ?? null,
          reasons: (match.reasons as string[]) || [],
        };
      });

      const totalCount = typeof library.total_count === "number" ? library.total_count : null;
      const hasMore = Boolean(library.has_more);
      // 任一候选缺 asset 级依据 = 依据不全。宁可明说不全，也不让 agent 拿着
      // 一堆 null 当完整依据讲给用户。
      const evidenceComplete = missingEvidence.length === 0;
      const evidenceNote = evidenceComplete
        ? (hasMore && totalCount !== null
            ? `素材库共 ${totalCount} 条，本次只读了第一页，但每条候选的依据都已按 asset 精确取回。`
            : "每条候选的依据都已取回。")
        : `依据不全：素材 ${missingEvidence.join("、")} 的元数据取不到，读取失败的具体原因未确定（本次没有拿到可判定的原因，不据此断定素材已删除或不属于当前工作区）。` +
          `上面点名的候选的 file_name/tags 若有值，只来自检索响应、未经本工作区元数据复核，不等于归属或内容已核实；duration_seconds 与 source_reference 本次取不到，就是空。`;

      const details = {
        candidates,
        evidence_complete: evidenceComplete,
        evidence_note: evidenceNote,
        library_total: totalCount,
        library_has_more: hasMore,
        instruction:
          "以上只是候选与依据，不代表替换。请把依据（文件名/时间点/标签/时长/匹配原因/排第几）摆给用户看，由用户或 agent 判断后，再走正常剪辑预览流程；本工具不执行任何修改。" +
          (evidenceComplete ? "" : ` 注意：必须向用户说明哪些候选的依据没取全（本次是：${missingEvidence.join("、")}），且只针对这些候选说依据不全——没取到的字段就说没取到，不要当成已知；这些候选的 file_name/tags 若有值只来自检索响应，未经本工作区元数据复核，不等于归属或内容已核实。`),
      };
      return { content: [{ type: "text", text: JSON.stringify(details) }], details };
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
      const scriptContext = stage === "script"
        ? await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/script-context`)
        : null;
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
        ...(scriptContext ? { script_context: scriptContext } : {}),
      };
      currentTaskRead = true;
      return { content: [{ type: "text", text: JSON.stringify(context) }], details: context };
    },
  });

  if (stage === "topic") {
    pi.registerTool({
      name: "generate_creation_task_topics",
      label: "生成选题候选",
      description: "仅在用户明确要求生成选题时，调用当前任务已有选题阶段；只生成候选，绝不代替用户选择或确认。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      execute: async () => {
        if (!taskId || !currentTaskRead) throw new Error("请先读取当前创作任务");
        if (previewCreated) throw new Error("本轮已生成选题候选，请用户检查并选择");
        const topics = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/topics/generate`, { method: "POST", body: JSON.stringify({}) });
        previewCreated = true;
        return {
          content: [{ type: "text", text: JSON.stringify({
            candidate_count: topics.candidates?.length ?? 0,
            instruction: "候选已由当前任务阶段生成，尚未选择或确认；请用户在工作台查看并明确选择。",
          }) }],
          details: { kind: "topic_generation", topics },
        };
      },
    });
  }

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
      const data = stage === "script" && taskId
        ? await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-context`)
        : await request("/api/knowledge-items");
      const items: Record<string, unknown>[] = stage === "script" ? data.sources || [] : data.items || [];
      const textOf = (item: Record<string, unknown>) => `${item.title || ""} ${item.text || item.body || ""}`;
      // B08：按 bigram 子串打分排序，中文/英文混合都能命中；不再「匹配不上就静默返回空」。
      const ranked = rankByQuery(params.query, items, textOf);
      const matches = ranked.slice(0, 5).map(({ item, score, hitTerms }) => ({
        id: stage === "script" ? item.reference_id : item.id,
        title: item.title,
        version: item.version,
        excerpt: String(item.text || item.body || "").slice(0, 900),
        relevance: Math.round(score * 100) / 100,
        matched_terms: hitTerms.slice(0, 6),
      }));
      const guidance = matches.length === 0
        ? "没匹配到已保存的资料。换用更接近资料原文的词（资料多为中文，建议 2-4 字关键词），或先问用户该看哪方面，再检索。"
        : undefined;
      const details = { matches, query_terms: tokenizeQuery(params.query).slice(0, 8), ...(guidance ? { guidance } : {}) };
      return { content: [{ type: "text", text: JSON.stringify(details) }], details };
    },
  });

  pi.registerTool({
    name: "preview_script_draft",
    label: "保存待确认文案",
    description: "只为当前任务保存一份待用户检查的完整文案草稿；不会确认文案或开始制作。每段必须引用当前任务允许的来源 ID。",
    parameters: {
      type: "object",
      properties: {
        title: { type: "string", minLength: 1, maxLength: 160 },
        sections: { type: "array", minItems: 1, maxItems: 20, items: {
          type: "object",
          properties: {
            id: { type: "string", minLength: 1, maxLength: 120 },
            title: { type: "string", minLength: 1, maxLength: 160 },
            content: { type: "string", minLength: 1, maxLength: 4000 },
            visual_prompt: { type: "string", minLength: 1, maxLength: 1000 },
            evidence_reference_ids: { type: "array", minItems: 1, maxItems: 12, items: { type: "string" } },
          },
          required: ["title", "content", "visual_prompt", "evidence_reference_ids"],
          additionalProperties: false,
        } },
      },
      required: ["title", "sections"], additionalProperties: false,
    },
    execute: async (_id: string, params: { title: string; sections: { id?: string; title: string; content: string; visual_prompt: string; evidence_reference_ids: string[] }[] }) => {
      if (!taskId || stage !== "script") throw new Error("当前步骤不能保存文案草稿");
      if (!currentTaskRead) throw new Error("请先读取当前任务和允许使用的资料");
      if (previewCreated) throw new Error("本轮已保存文案草稿，请用户检查后再继续");
      const draft = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-draft`, {
        method: "POST",
        body: JSON.stringify({ title: params.title, sections: params.sections.map((section, index) => ({
          ...section, id: section.id || `section_${String(index + 1).padStart(2, "0")}`,
        })) }),
      });
      previewCreated = true;
      const preview = { script_id: draft.id };
      return {
        content: [{ type: "text", text: JSON.stringify({
          title: draft.title, section_count: draft.sections.length,
          instruction: "文案已保存为待确认草稿，请用户在工作台检查并明确确认。尚未开始制作。",
        }) }],
        details: { kind: "script_draft", preview },
      };
    },
  });

  pi.registerTool({
    name: "preview_edit",
    label: "预览剪辑修改",
    description: "读取当前任务后，对用户明确的动作生成真实待确认预览。对象引用和阶段单独传，不写进 command；缺少明确参数先询问。每轮只调用一次，失败后说明原始阻断，不自行改写重试。只预览，不确认执行。",
    parameters: {
      type: "object",
      properties: {
        command: { type: "string", minLength: 2, maxLength: 500,
          description: "只传用户明确的可执行动作，如水平裁剪到40%。不扩写对象、限制、解释或副作用。裁剪百分比是窗口位置，不是宽度或保留比例；缺少百分比先询问，不猜步长。" },
        context_stage: { type: "string", enum: ["topic", "script", "narration", "montage", "review", "subtitle_cover"],
          description: "目标所处阶段，单独传递，不写入 command。" },
        selected_refs: { type: "array", items: { type: "string" }, maxItems: 8,
          description: "用户明确或界面选中的对象引用，例如clip:clip-1；单独传递，不写入 command。" },
      },
      required: ["command"],
      additionalProperties: false,
    },
    execute: async (_id: string, params: { command: string; context_stage?: string; selected_refs?: string[] }) => {
      if (!taskId || !currentTaskRead) throw new Error("请先读取当前创作任务，再预览修改");
      if (creationActionApplied) throw new Error("本轮已执行制作动作，不再创建修改预览");
      const effectiveStage = params.context_stage || stage;
      const effectiveRefs = params.selected_refs?.length ? params.selected_refs : selectedRefs;
      if (!effectiveStage) throw new Error("请先选择要修改的任务阶段");
      if (previewCreated) throw new Error("本轮已请求修改预览，请向用户说明结果后再决定下一步");
      previewCreated = true;
      const preview = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/preview`, {
        method: "POST",
        body: JSON.stringify({
          raw_command: params.command,
          context_stage: effectiveStage,
          selected_object_refs: effectiveRefs,
          playhead_ms: playheadMs,
        }),
      });
      return {
        content: [{ type: "text", text: JSON.stringify({
          proposal_id: preview.proposal.id,
          summary: preview.proposal.summary,
          target_refs: preview.proposal.target_refs,
          operation_type: preview.proposal.operation.operation_type,
          before: preview.proposal.preview.before.detail,
          after: preview.proposal.preview.after.detail,
          invalidated_refs: preview.proposal.invalidated_refs,
          external_effects: preview.proposal.external_effects,
          status: preview.proposal.status,
          instruction: "仅依据上述实际前后变化和影响说明预览；水平/垂直百分比是裁剪窗口位置，不是宽度或面积比例，不猜中心对齐或收窄。必须由用户确认后才能执行，尚未出片。",
        }) }],
        details: { kind: "edit_preview", preview },
      };
    },
  });

  if (taskId && stage !== "topic" && stage !== "script") {
    const perform = async (action: "start" | "resume" | "cancel") => {
      if (!currentTaskRead) throw new Error("请先读取当前创作任务");
      if (creationActionApplied) throw new Error("本轮制作动作已经提交，不重复执行");
      if (previewCreated) throw new Error("本轮已请求修改预览，不能同时开始、继续或取消制作");
      creationActionApplied = true;
      let status: Record<string, unknown>;
      if (action === "start") {
        const preview = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/preview`);
        const requirements = preview.requirements as { can_continue?: boolean; continuation_blocker?: string | null } | undefined;
        if (!requirements?.can_continue) {
          throw new Error(requirements?.continuation_blocker || "当前制作条件尚未就绪，未提交制作");
        }
        const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution`);
        const existingJob = current.job as { input_digest?: string; status?: string } | null;
        const canRetryCurrent = existingJob?.input_digest === preview.input_digest
          && (existingJob.status === "failed" || existingJob.status === "interrupted"
            || existingJob.status === "cancelled" || !current.video_ready);
        status = canRetryCurrent
          ? await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/resume`, {
            method: "POST", body: JSON.stringify({}),
          })
          : await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/start`, {
            method: "POST",
            body: JSON.stringify({ script_id: preview.script_id, input_digest: preview.input_digest, requirements_confirmed: true }),
          });
      } else {
        status = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/${action}`, {
          method: "POST", body: JSON.stringify({}),
        });
      }
      return {
        content: [{ type: "text", text: JSON.stringify({ action, status }) }],
        details: { kind: "execution_action", action, status },
      };
    };
    const actionTools = [
      ["start_creation_execution", "开始连续制作", "仅当用户明确要求开始制作、开始生成成片或修改后重新生成时调用。使用当前任务已确认的文案与制作要求；不提交文案或编辑草稿。"],
      ["resume_creation_execution", "继续连续制作", "仅当用户明确要求继续或恢复当前制作作业时调用。"],
      ["cancel_creation_execution", "取消连续制作", "仅当用户明确要求取消或停止当前制作作业时调用；取消不修改任务草稿。"],
    ] as const;
    for (const [name, label, description] of actionTools) {
      const action = name === "start_creation_execution" ? "start"
        : name === "resume_creation_execution" ? "resume" : "cancel";
      pi.registerTool({
        name, label, description,
        parameters: { type: "object", properties: {}, additionalProperties: false },
        execute: async () => perform(action),
      });
    }
  }
}
