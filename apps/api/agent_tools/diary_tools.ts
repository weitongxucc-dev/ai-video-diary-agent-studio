import { rankByQuery, tokenizeQuery } from "./retrieval.ts";
import { registerMaterialTools } from "./material_tools.ts";

const apiBase = process.env.AI_VIDEO_DIARY_AGENT_API_BASE;
const taskId = process.env.AI_VIDEO_DIARY_AGENT_TASK_ID;
const stage = process.env.AI_VIDEO_DIARY_AGENT_STAGE;
const selectedRefs = JSON.parse(process.env.AI_VIDEO_DIARY_AGENT_SELECTED_REFS || "[]");
const playheadMs = process.env.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS
  ? Number(process.env.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS)
  : null;
const selectedProfile = process.env.AI_VIDEO_DIARY_AGENT_PROFILE_REFERENCE ? JSON.parse(process.env.AI_VIDEO_DIARY_AGENT_PROFILE_REFERENCE) : null;
const selectedKnowledge = process.env.AI_VIDEO_DIARY_AGENT_KNOWLEDGE_REFERENCE ? JSON.parse(process.env.AI_VIDEO_DIARY_AGENT_KNOWLEDGE_REFERENCE) : null;
const selectedTaskVersion = process.env.AI_VIDEO_DIARY_AGENT_TASK_VERSION ? JSON.parse(process.env.AI_VIDEO_DIARY_AGENT_TASK_VERSION) : null;
function verifyTaskVersion(workspace: {task?: {current_timeline_version_id?: string | null; timeline_sequence?: number}}) {
  if (selectedTaskVersion && (workspace.task?.current_timeline_version_id !== selectedTaskVersion.timeline_version_id || workspace.task?.timeline_sequence !== selectedTaskVersion.timeline_sequence)) throw new Error("任务版本已变化，请重新查看选中对象后发送；未提交操作");
}
let previewCreated = false;
let creationRequest: any = null;
let parentCreationPromise: Promise<any> | null = null;
let currentTaskRead = process.env.AI_VIDEO_DIARY_AGENT_TASK_READ_READY === "1";
let currentScriptDraftUpdatedAt: string | null = process.env.AI_VIDEO_DIARY_AGENT_SCRIPT_DRAFT_UPDATED_AT || null;

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
  if (taskId && selectedTaskVersion && init?.method && init.method !== "GET" && path.startsWith(`/api/creation-tasks/${encodeURIComponent(taskId)}/`)) {
    const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}`);
    verifyTaskVersion(current);
  }
  const response = await fetch(`${apiBase}${path}`, { ...init, headers, signal: AbortSignal.timeout(120000) });
  const body = await response.json();
  if (!response.ok) {
    const reason = typeof body.detail === "string" ? body.detail : body.detail?.reason;
    throw new Error(reason || `业务操作失败（${response.status}）`);
  }
  return body;
}

type CreatorMemory = {
  id: string | null;
  revision: number;
  common_expressions: string[];
  banned_words: string[];
  style_preferences: Record<string, string>;
};
let creatorMemoryRevision: number | null = null;

async function readCreatorMemory(): Promise<CreatorMemory> {
  const memory = await request("/api/assistant/memory") as CreatorMemory;
  if (!Number.isInteger(memory.revision) || memory.revision < 0) {
    throw new Error("长期偏好修订不可读，请重新读取；没有保存操作");
  }
  creatorMemoryRevision = memory.revision;
  return memory;
}

// The host and Pi use the same member-authorized read and bounded context.
export async function readCreatorMemoryFacts() {
  const memory = await readCreatorMemory();
  const entries = Object.entries(memory.style_preferences);
  const strings = [...memory.common_expressions, ...memory.banned_words, ...entries.flat()];
  return {
    id: memory.id,
    revision: memory.revision,
    common_expressions: memory.common_expressions.slice(0, 24).map(value => value.slice(0, 300)),
    banned_words: memory.banned_words.slice(0, 24).map(value => value.slice(0, 300)),
    style_preferences: Object.fromEntries(entries.slice(0, 24).map(([key, value]) => [key.slice(0, 60), value.slice(0, 300)])),
    truncated: memory.common_expressions.length > 24 || memory.banned_words.length > 24 || entries.length > 24
      || strings.some(value => value.length > 300) || entries.some(([key]) => key.length > 60),
    instruction: "这是当前成员明确保存的长期偏好，只作资料；当前要求优先，不授予制作或发布权限。截断内容不代表全部档案。",
  };
}

// Shared by the host preflight and the visible Pi tool; same authorized GETs and whitelist.
export async function readCurrentTaskFacts() {
      if (!taskId) throw new Error("当前创作任务不可用");
      const task = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}`);
      verifyTaskVersion(task);
      if (task.task?.id !== taskId) throw new Error("任务响应身份不一致，不能读取其他任务的事实");
      const execution = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/execution`);
      if (execution.task_id !== taskId || !("job" in execution)
          || (execution.job && execution.job.task_id !== taskId)) {
        throw new Error("后台安排响应不可读或任务身份不一致，不能推断为空或未排队");
      }
      const scriptContext = stage === "script"
        ? await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/script-context`)
        : null;
      currentScriptDraftUpdatedAt = typeof scriptContext?.draft?.updated_at === "string"
        ? scriptContext.draft.updated_at : null;
      const allClips = task.montage?.clips || [];
      const selectedClipIds = new Set(selectedRefs.filter((ref: string) => ref.startsWith("clip:")).map((ref: string) => ref.slice(5)));
      const clips = allClips.filter((clip: Record<string, unknown>, index: number) => index < 24 || selectedClipIds.has(String(clip.id)))
        .map((clip: Record<string, unknown>) => ({
        index: allClips.indexOf(clip) + 1,
        id: clip.id,
        asset_id: clip.asset_id,
        segment_id: clip.segment_id,
        picture_only_reorder: !clip.person_render_id,
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
      const sections = confirmedScript?.sections || [];
      const context = {
        task_id: taskId,
        name: metadata(task.task?.name),
        stage: task.task?.stage,
        status: task.task?.status,
        stage_status: task.task?.stage_status,
        stage_reason: metadata(task.task?.stage_reason),
        stage_impact: metadata(task.task?.stage_impact, 500),
        stage_repair_action: metadata(task.task?.stage_repair_action, 500),
        current_timeline_version_id: task.task?.current_timeline_version_id,
        timeline_sequence: task.task?.timeline_sequence,
        confirmed_script_id: confirmedScriptId,
        confirmed_script: confirmedScript ? {
          id: confirmedScript.id, title: metadata(confirmedScript.title), language: confirmedScript.language,
          selection_status: confirmedScript.selection_status, updated_at: confirmedScript.updated_at,
          section_count: sections.length, sections_truncated: sections.length > 24,
          sections: sections.slice(0, 24).map((section: Record<string, unknown>) => {
            const evidenceIds = section.evidence_reference_ids as string[];
            return {
              id: section.id, title: metadata(section.title), content: metadata(section.content, 2000),
              visual_prompt: metadata(section.visual_prompt, 500),
              evidence_reference_ids: evidenceIds.slice(0, 16), evidence_reference_count: evidenceIds.length,
              text_truncated: String(section.content).length > 2000 || String(section.visual_prompt).length > 500,
              evidence_truncated: evidenceIds.length > 16,
            };
          }),
        } : null,
        execution: {
          status: execution.job?.status ?? null,
          job: execution.job ? { id: execution.job.id, status: execution.job.status, progress: execution.job.progress } : null,
          current_stage: metadata(execution.current_stage, 120), waiting_reason: metadata(execution.waiting_reason, 500),
          updated_at: metadata(execution.status_updated_at, 120),
          meaning: execution.job ? "后台安排状态来自独立执行记录，不能由task.stage_status推断是否排队。"
            : "服务端本次未返回该任务的后台安排记录；不以task.stage_status推断其他状态。",
        },
        plan: plan ? {
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
        omitted_clip_count: Math.max(0, allClips.length - clips.length),
        ...(scriptContext ? { script_context: scriptContext } : {}),
      };
      currentTaskRead = true;
      return context;
}

export default function (pi: { registerTool: (tool: unknown) => void }) {
  let creationActionApplied = false;
  let memoryWriteAttempted = false;
  type ControlStep = { index: number; command: string; enabled: boolean };
  type ControlProposal = { id: string; summary: string; operation_type: string; steps: ControlStep[] };
  type ControlContext = { task_id: string; timeline_version_id: string | null; timeline_sequence: number;
    can_undo: boolean; can_redo: boolean; proposals: ControlProposal[] };
  let editControls: ControlContext | null = null;
  let editControlWriteAttempted = false;

  function requireControlScope() {
    if (!taskId || !currentTaskRead) throw new Error("请先读取当前创作任务，再读取编辑控制范围");
    if (!selectedTaskVersion) throw new Error("本轮任务版本未绑定，请重新查看任务后发送");
  }
  async function loadEditControls(): Promise<ControlContext> {
    requireControlScope();
    const context = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edit-controls`) as ControlContext;
    if (context.task_id !== taskId) throw new Error("编辑控制回读不属于当前任务，未提交操作");
    verifyTaskVersion({ task: { current_timeline_version_id: context.timeline_version_id,
      timeline_sequence: context.timeline_sequence } });
    return { ...context, proposals: context.proposals.slice(0, 8) };
  }
  function readControlContext(): ControlContext {
    if (!editControls) throw new Error("请先读取本轮编辑控制范围，不能从聊天历史猜提案");
    return editControls;
  }
  function proposalAt(index: number): ControlProposal {
    const context = readControlContext();
    if (!Number.isInteger(index) || index < 1 || index > context.proposals.length) {
      throw new Error("提案序号不在本轮实际回读的列表中，未提交操作");
    }
    return context.proposals[index - 1];
  }
  async function freshControlProposal(proposal: ControlProposal): Promise<ControlProposal> {
    const current = await loadEditControls();
    const fresh = current.proposals.find(item => item.id === proposal.id);
    if (!fresh || fresh.operation_type !== proposal.operation_type || fresh.summary !== proposal.summary
        || fresh.steps.length !== proposal.steps.length
        || fresh.steps.some(step => !proposal.steps.some(previous => previous.index === step.index
          && previous.command === step.command && previous.enabled === step.enabled))) {
      throw new Error("该提案或步骤已变化，请重新读取编辑控制范围；未提交操作");
    }
    return fresh;
  }
  function controlWriteContext() {
    requireControlScope();
    const assistantTurnId = process.env.AI_VIDEO_DIARY_AGENT_TURN_ID;
    if (!assistantTurnId || !process.env.AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION) {
      throw new Error("本轮原始要求不可核验，未提交编辑控制操作");
    }
    if (editControlWriteAttempted) throw new Error("本轮已提交一次撤销、重做或提案取消，不重复执行");
    if (creationActionApplied) throw new Error("本轮已提交制作动作，不再修改编辑历史");
    return { assistant_turn_id: assistantTurnId,
      expected_timeline_version_id: selectedTaskVersion.timeline_version_id,
      expected_timeline_sequence: selectedTaskVersion.timeline_sequence };
  }
  function controlResult(action: "undo" | "redo" | "reject", result: unknown) {
    return { content: [{ type: "text", text: JSON.stringify({ action, receipt_returned: true,
      instruction: action === "reject" ? "这是取消当前待确认提案的实际回执，没有取消制作作业。普通回复不展示内部编号。"
        : "这是整个上一编辑的撤销或重做回执；包括复合编辑中的全部子项，不能称为只撤销其中调色等单项。未自动开始新的渲染。普通回复不展示内部编号。" }) }],
      details: { kind: "edit_control", action, task_id: taskId, result } };
  }
  pi.registerTool({
    name: "read_edit_control_context", label: "读取可调整编辑",
    description: "读取本轮当前任务版本的撤销、重做条件和最多8个待确认提案。后续选择或取消仅用本次返回的提案序号，步骤仅用实际index；历史聊天不是当前控制范围。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      editControls = await loadEditControls();
      return { content: [{ type: "text", text: JSON.stringify({ can_undo: editControls.can_undo,
        can_redo: editControls.can_redo, proposals: editControls.proposals.map((proposal, index) => ({
          proposal_index: index + 1, summary: proposal.summary, operation_type: proposal.operation_type, steps: proposal.steps })),
        instruction: "proposal_index从1开始；selection使用实际步骤index。撤销只支持整个上一编辑；已应用复合编辑中的单项逆编辑尚未实现。" }) }],
        details: { kind: "edit_control_context", context: editControls } };
    },
  });
  pi.registerTool({
    name: "preview_edit_selection", label: "重看保留修改",
    description: "用户明确要求保留或取消当前复合提案子项时，先读编辑控制范围，再按实际提案序号及要保留的步骤index生成新待确认提案。不能直接应用，也不撤销已应用编辑；每轮最多一个编辑预览。",
    parameters: { type: "object", properties: {
      proposal_index: { type: "integer", minimum: 1, maximum: 8 },
      selection: { type: "array", minItems: 1, maxItems: 100, uniqueItems: true, items: { type: "integer", minimum: 0 } },
    }, required: ["proposal_index", "selection"], additionalProperties: false },
    execute: async (_id: string, params: { proposal_index: number; selection: number[] }) => {
      if (previewCreated) throw new Error("本轮已请求修改预览，请检查结果后再决定下一步");
      if (creationActionApplied || editControlWriteAttempted) throw new Error("本轮已提交制作或历史控制动作，不再生成修改预览");
      const proposal = proposalAt(params.proposal_index);
      if (proposal.operation_type !== "EditCompoundTimeline") throw new Error("这项提案不支持部分选择，请查看当前提案内容");
      if (!Array.isArray(params.selection) || !params.selection.length || params.selection.length > 100
          || new Set(params.selection).size !== params.selection.length
          || params.selection.some(index => !Number.isInteger(index) || !proposal.steps.some(step => step.index === index))) {
        throw new Error("保留步骤不在当前复合提案的实际步骤中，未生成新提案");
      }
      await freshControlProposal(proposal);
      previewCreated = true;
      const preview = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/${encodeURIComponent(proposal.id)}/selection/preview?draft=true`, {
        method: "POST", body: JSON.stringify({ included_step_indices: params.selection }),
      });
      return { content: [{ type: "text", text: JSON.stringify({ summary: preview.proposal.summary,
        before: preview.proposal.preview.before.detail, after: preview.proposal.preview.after.detail,
        instruction: "保留项已重新生成待确认提案，尚未应用。草稿参数不是已生成的视频，请用户检查后应用。" }) }],
        details: { kind: "edit_preview", preview } };
    },
  });
  pi.registerTool({
    name: "discard_edit_proposal", label: "取消待确认修改",
    description: "仅当用户明确取消整个待确认提案时使用。先读取编辑控制范围，以实际proposal_index取消；不是取消制作，也不是删除已应用编辑。服务器核本轮原句与版本。",
    parameters: { type: "object", properties: { proposal_index: { type: "integer", minimum: 1, maximum: 8 } },
      required: ["proposal_index"], additionalProperties: false },
    execute: async (_id: string, params: { proposal_index: number }) => {
      const body = controlWriteContext();
      const proposal = proposalAt(params.proposal_index);
      await freshControlProposal(proposal);
      editControlWriteAttempted = true;
      const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/${encodeURIComponent(proposal.id)}/reject`, {
        method: "POST", body: JSON.stringify(body),
      });
      return controlResult("reject", result);
    },
  });
  for (const action of ["undo", "redo"] as const) {
    pi.registerTool({
      name: action === "undo" ? "undo_last_edit" : "redo_last_edit",
      label: action === "undo" ? "撤销上一修改" : "重做上一修改",
      description: `${action === "undo" ? "撤销" : "重做"}整个上一编辑，包括复合编辑全部子项。必须先读取当前编辑控制范围；不能将其用于已应用复合编辑中的单项逆编辑。模型不传任务或版本，服务器核用户本轮明确要求。每轮最多一次历史控制写入，失败不自动重试。`,
      parameters: { type: "object", properties: {}, additionalProperties: false },
      execute: async () => {
        const body = controlWriteContext();
        readControlContext();
        const current = await loadEditControls();
        if (!(action === "undo" ? current.can_undo : current.can_redo)) {
          throw new Error(action === "undo" ? "当前版本没有可撤销的上一编辑" : "当前版本没有可重做的编辑");
        }
        editControlWriteAttempted = true;
        const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/timeline/${action}`, {
          method: "POST", body: JSON.stringify(body),
        });
        return controlResult(action, result);
      },
    });
  }
  const materialSelection = process.env.AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION
    ? JSON.parse(process.env.AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION)
    : null;
  if (stage === "material" || taskId || materialSelection) registerMaterialTools(pi, {
    selection: materialSelection,
    request: async (path, init) => {
      if (taskId && materialSelection) {
        const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}`);
        verifyTaskVersion(current);
        const clip = selectedRefs.length === 1 && selectedRefs[0].startsWith("clip:")
          ? current.montage?.clips?.find((item: { id: string }) => item.id === selectedRefs[0].slice(5)) : null;
        if (!clip || clip.person_render_id || clip.asset_id !== materialSelection.assetId
            || clip.segment_id !== materialSelection.segmentId) {
          throw new Error("选中镜头的素材来源已变化，未读写其他素材；请重新查看后发送");
        }
      }
      return request(path, init);
    },
  });
  const replacementCandidates = new Set<string>();
  pi.registerTool({
    name: "read_clip_candidates",
    label: "查找替换镜头",
    description: "为发送时选中的当前任务镜头读取其他可用候选：使用范围、当前文件修订及时间槽由服务端校验，自动选择排除不要自动选用的素材。用户让助手决定时可按任务内容与已存标签选择一个候选，再调用preview_edit提交待确认替换；不需要用户先手选素材。候选不是画质评分或已修改结果。无可用候选须明确说明实际条件，不把零候选说成空库。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      if (!taskId || !currentTaskRead || selectedRefs.length !== 1 || !selectedRefs[0].startsWith("clip:")) {
        throw new Error("请先选中要替换的任务镜头，再查找候选");
      }
      const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}`);
      verifyTaskVersion(current);
      const clipId = selectedRefs[0].slice(5);
      const clip = current.montage?.clips?.find((item: { id: string }) => item.id === clipId);
      if (!clip) throw new Error("所选镜头已不存在，未查找其他镜头的候选");
      const candidates = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/montage/clips/${encodeURIComponent(clipId)}/candidates?for_automation=true`);
      const assets = new Map<string, Record<string, any>>();
      const matches = [];
      replacementCandidates.clear();
      for (const candidate of candidates.slice(0, 24)) {
        if (candidate.asset_id === clip.asset_id && candidate.segment_id === clip.segment_id) continue;
        if (!assets.has(candidate.asset_id)) assets.set(candidate.asset_id,
          await request(`/api/production/materials/${encodeURIComponent(candidate.asset_id)}`));
        const asset = assets.get(candidate.asset_id)!;
        if (asset.use_state === "not_for_automation" || asset.use_state === "excluded") continue;
        const segment = asset.segments.find((item: { id: string }) => item.id === candidate.segment_id);
        if (!segment) throw new Error("候选来源已变化，请重新查找；未生成替换提案");
        replacementCandidates.add(`${candidate.asset_id}:${candidate.segment_id}`);
        matches.push({ asset_id: candidate.asset_id, segment_id: candidate.segment_id,
          file_name: String(candidate.file_name).split(/[\\/]/).at(-1),
          description: candidate.description, start_seconds: candidate.start_seconds, end_seconds: candidate.end_seconds,
          user_tags: [...asset.user_tags, ...segment.user_tags], system_tags: segment.system_tags,
          reasons: candidate.reasons });
      }
      return { content: [{ type: "text", text: JSON.stringify({ matches,
        instruction: "这是其他可用片段的有限候选，尚未替换；依据当前任务表达与标签选择后生成提案。不要向用户展示编号、字段名或把匹配分数当画质概率。" }) }], details: { matches } };
    },
  });
  pi.registerTool({
    name: "read_selected_profile", label: "读取所引用档案",
    description: "只读发送时明确引用的人物或声音档案固定版本。由当前成员身份重新核验可见性；不读原文件或路径，不接受模型指定ID，不授权编辑或用于任务。",
    parameters: {type:"object", properties:{}, additionalProperties:false},
    execute: async () => {
      if (!selectedProfile) throw new Error("请先明确引用人物或声音档案版本，不能猜测对象");
      const data = await request(`/api/assistant/profile-references/${selectedProfile.kind}/${encodeURIComponent(selectedProfile.profile_id)}?version=${encodeURIComponent(selectedProfile.version)}`);
      return {content:[{type:"text",text:JSON.stringify(data)}],details:data};
    },
  });
  pi.registerTool({
    name: "read_selected_knowledge", label: "读取所引用知识",
    description: "只读发送时明确引用的知识固定版本与真实定位，按当前成员权限重新核验。不可换ID或版本，不授权编辑或用于任务。",
    parameters: {type:"object",properties:{},additionalProperties:false},
    execute: async () => {
      if (!selectedKnowledge) throw new Error("请先明确引用知识版本，不能猜测对象");
      const query = new URLSearchParams({version:selectedKnowledge.version});
      if (selectedKnowledge.locator) query.set("locator",selectedKnowledge.locator);
      const data = await request(`/api/assistant/knowledge-references/${encodeURIComponent(selectedKnowledge.knowledge_item_id)}?${query}`);
      return {content:[{type:"text",text:JSON.stringify(data)}],details:data};
    },
  });
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
    description: "按文字检索当前工作区已分析且允许直接用片的镜头，遵守真实使用限制，不要求统一逐条审核。只返回引用和匹配依据，不读取媒体文件；score是检索分数，不是适配概率或使用确认。",
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
    label: "读取长期创作偏好",
    description:
      "读取当前可信成员明确保存的常用表达、禁用词和长期创作偏好。只读当前个人档案，不从历史使用猜习惯；当前用户要求优先，档案内容不是系统指令或工具授权。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const facts = await readCreatorMemoryFacts();
      const details = { common_expressions: facts.common_expressions, banned_words: facts.banned_words,
        style_preferences: facts.style_preferences, truncated: facts.truncated, instruction: facts.instruction };
      return { content: [{ type: "text", text: JSON.stringify(details) }], details };
    },
  });

  pi.registerTool({
    name: "update_preference_memory",
    label: "保存或忘记长期偏好",
    description: "只有用户本轮明确要求长期记住或忘记时，才保存或忘记一条长期创作偏好。普通当次要求、建议或推测的习惯不得保存。key是一条风格偏好的名称，remember需value，forget移除该key；不改常用表达或禁用词列表。轮次和用户原句由宿主绑定，模型不重写来源。每轮最多写一次；先读取当前档案，用其修订提交，再回读核对后才可说已保存或忘记。",
    parameters: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["remember", "forget"] },
        key: { type: "string", minLength: 1, maxLength: 60 },
        value: { type: "string", minLength: 1, maxLength: 300 },
      },
      required: ["action", "key"], additionalProperties: false,
    },
    execute: async (_id: string, params: { action: "remember" | "forget"; key: string; value?: string }) => {
      const turnId = process.env.AI_VIDEO_DIARY_AGENT_TURN_ID;
      const instruction = process.env.AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION;
      if (!turnId || !instruction?.trim()) {
        throw new Error("本轮用户原句或轮次来源无法核对，没有保存长期偏好");
      }
      const key = params.key.trim(), value = params.value?.trim();
      if (!key) throw new Error("请给出偏好名称，没有保存操作");
      if (memoryWriteAttempted) throw new Error("本轮长期偏好写入已经提交，不重复保存");
      if (params.action === "remember" && !value) throw new Error("请给出要记住的偏好内容，没有保存操作");
      if (creatorMemoryRevision === null) await readCreatorMemoryFacts();
      const expectedRevision = creatorMemoryRevision!;
      memoryWriteAttempted = true;
      const submitted = await request("/api/assistant/memory", {
        method: "PATCH", body: JSON.stringify({
          action: params.action, key,
          ...(params.action === "remember" ? { value } : {}),
          expected_revision: expectedRevision, assistant_turn_id: turnId, source_quote: instruction.slice(0, 1000),
        }),
      }) as CreatorMemory;
      const verified = await readCreatorMemory();
      const valueMatches = params.action === "remember"
        ? verified.style_preferences[key] === value
        : !Object.hasOwn(verified.style_preferences, key);
      if (verified.id !== submitted.id || verified.revision !== submitted.revision
          || verified.revision < expectedRevision || !valueMatches) {
        throw new Error("长期偏好写后回读未匹配，请查看当前档案；不能确认保存成功");
      }
      const summary = params.action === "remember" ? `已保存长期偏好：${key}` : `已忘记长期偏好：${key}`;
      const details = { kind: "preference_memory_updated", summary };
      return { content: [{ type: "text", text: summary }], details };
    },
  });

  pi.registerTool({
    name: "find_material_candidates",
    label: "按描述找候选素材",
    description:
      "按一句描述（如「更亮的城市夜景」）从已分析、符合实际使用限制的素材里找若干候选镜头，不要求统一逐条审核；每条带可核对依据（文件名、标签、时长、来源、匹配原因、镜头引用）。只给候选与依据，供用户和 agent 一起裁量；绝不自动替换、不改时间线。",
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
    description: "保存用户的完整制作目标和总数量，读取真实澄清状态。数量可以很大，只保留总目标，不一次创建全部任务。未明确多条关系或素材范围时先澄清；当前不会开始制作。",
    parameters: {
      type: "object",
      properties: {
        goal: { type: "string", minLength: 1, maxLength: 2000 },
        target_count: { type: "integer", minimum: 1, maximum: 9007199254740991 },
        output_relation: { type: "string", enum: ["single", "different_topics", "variants", "series"] },
        material_scope: { type: "string", enum: ["workspace_library", "today_received"] },
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
    execute: async (_id: string, params: { goal: string; target_count?: number; output_relation?: string; material_scope?: string; name?: string; language?: string; aspect_ratio?: string; duration_target_seconds?: number; presenter_share_target?: number; lipsync_quality?: "high" | "fast"; template_id?: string; template_version?: number }) => {
      if (previewCreated) throw new Error("本轮已生成新视频预检，请用户检查后再决定下一步");
      const turnId = process.env.AI_VIDEO_DIARY_AGENT_TURN_ID;
      if (!turnId) throw new Error("当前创作回合不可核验，没有保存制作目标");
      previewCreated = true;
      parentCreationPromise = request("/api/creation-requests", {
        method: "POST",
        body: JSON.stringify({
          goal: params.goal,
          request_key: turnId, assistant_turn_id: turnId,
          ...(params.name ? { name: params.name } : {}),
          target_count: params.target_count ?? 1,
          output_relation: params.output_relation ?? null,
          material_scope: params.material_scope ?? "workspace_library",
          language: params.language || "zh-CN",
          aspect_ratio: params.aspect_ratio || "9:16",
          duration_target_seconds: params.duration_target_seconds || 60,
          ...((params.presenter_share_target !== undefined || !params.template_id)
            ? { presenter_share_target: params.presenter_share_target ?? 20 } : {}),
          ...((params.lipsync_quality !== undefined || !params.template_id)
            ? { lipsync_quality: params.lipsync_quality ?? "high" } : {}),
          ...(params.template_id ? { template_id: params.template_id, template_version: params.template_version } : {}),
        }),
      }).then(created => {
        creationRequest = created;
        return created;
      });
      const created = await parentCreationPromise;
      return {
        content: [{ type: "text", text: JSON.stringify({
          goal: created.goal, target_count: created.target_count, output_relation: created.output_relation,
          clarification_questions: created.clarification_questions,
          instruction: "总目标已保存，尚未创建任务或成片。缺重要条件先询问；已明确时只准备当前至多8条具体目标，不用重复文案凑数量。",
        }) }],
        details: { kind: "plan_preview", preview: { kind: "creation_request", request: created } },
      };
    },
  });

  pi.registerTool({
    name: "prepare_creation_window",
    label: "准备当前这组视频",
    description: "将本轮已保存且条件明确的制作目标展开为当前至多8条具体方案供用户检查。不同主题、版本或系列需有各自具体目标；仅保存待确认方案，不确认、不生成成片。总数量与本组数量分开。",
    parameters: { type: "object", properties: {
      goals: { type: "array", minItems: 1, maxItems: 8, items: { type: "object", properties: {
        goal: { type: "string", minLength: 1, maxLength: 2000 }, name: { type: "string", minLength: 1, maxLength: 120 },
      }, required: ["goal"], additionalProperties: false } },
    }, required: ["goals"], additionalProperties: false },
    execute: async (_id: string, params: {goals: {goal: string; name?: string}[]}) => {
      if (parentCreationPromise) {
        try { await parentCreationPromise; }
        catch (cause) { throw new Error("本轮完整目标保存失败，未展开方案；请查看目标保存的实际原因", { cause }); }
      }
      const boundRequestId = process.env.AI_VIDEO_DIARY_AGENT_CREATION_REQUEST_ID;
      if (!creationRequest && boundRequestId) {
        const saved = await request(`/api/creation-requests/${encodeURIComponent(boundRequestId)}`);
        if (saved.id !== boundRequestId) throw new Error("回读目标与本轮已保存请求不一致，没有展开任务");
        creationRequest = saved;
      }
      if (!creationRequest || creationRequest.clarification_questions.length) throw new Error("先保存本次完整目标并确认重要条件，没有展开任务");
      if (creationRequest.current_window?.status === "proposed") throw new Error("当前这组方案尚待用户确认，没有另建一组");
      creationRequest = await request(`/api/creation-requests/${encodeURIComponent(creationRequest.id)}/windows`, {
        method: "POST", body: JSON.stringify({expected_revision: creationRequest.revision, goals: params.goals}),
      });
      return { content: [{type:"text", text:JSON.stringify({
        target_count:creationRequest.target_count, proposed_count:creationRequest.current_window.entries.length,
        goals:creationRequest.current_window.entries.map((entry: any)=>({ordinal:entry.ordinal,
          goal:entry.plan.goal, name:entry.plan.name, blockers:entry.plan.blockers, confirmable:entry.plan.confirmable})),
        instruction:"这组方案已保存，尚未创建视频任务或成片。请用户检查并确认；总目标其余部分还未规划。",
      })}], details: {kind:"plan_preview", preview:{kind:"creation_request",request:creationRequest}} };
    },
  });

  pi.registerTool({
    name: "read_current_task",
    label: "读取当前创作任务",
    description: "读取当前任务真实状态、已确认方案的目标/固定资料、精确已确认文案与独立后台安排，以及时间线摘要。文案已确认时不能说没有配音文本；stage_status不是排队状态，必须以execution.job/status/waiting_reason为准。selected_refs仅是编辑器当前选择，不代表方案输入资料；空选择不能推断资料未录入或未授权。处理修改要求前先调用，不得把对话记录当作任务状态。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const context = await readCurrentTaskFacts();
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
        })), ...(currentScriptDraftUpdatedAt ? { expected_updated_at: currentScriptDraftUpdatedAt } : {}) }),
      });
      currentScriptDraftUpdatedAt = typeof draft.updated_at === "string" ? draft.updated_at : currentScriptDraftUpdatedAt;
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
    description: "读取当前任务后，对用户明确的动作生成真实待确认预览。任务、阶段和界面默认对象由宿主绑定，不由模型传入；用户明确的镜头序号由服务端从本轮原句解析，交换镜头1和2不要求先在界面多选。每轮只调用一次，失败后说明原始阻断，不自行改写重试。只预览，不确认执行。",
    parameters: {
      type: "object",
      properties: {
        replacement_asset_id: { type: "string", minLength: 1, maxLength: 120, description: "read_clip_candidates本轮实际返回的候选素材，仅替换时填写。" },
        replacement_segment_id: { type: "string", minLength: 1, maxLength: 120, description: "同一候选的片段，须与候选素材一起填写。" },
      },
      additionalProperties: false,
    },
    execute: async (_id: string, params: { command?: string; context_stage?: string; selected_refs?: string[]; replacement_asset_id?: string; replacement_segment_id?: string } = {}) => {
      if (!taskId || !currentTaskRead) throw new Error("请先读取当前创作任务，再预览修改");
      if (creationActionApplied || editControlWriteAttempted) throw new Error("本轮已执行制作或历史控制动作，不再创建修改预览");
      const originalInstruction = process.env.AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION;
      const assistantTurnId = process.env.AI_VIDEO_DIARY_AGENT_TURN_ID;
      if (!originalInstruction || !assistantTurnId) throw new Error("本轮原始要求不可核验，未生成修改提案");
      if (params.context_stage && params.context_stage !== stage) throw new Error("模型指定阶段与本轮选中阶段不一致，未生成修改提案");
      if (params.selected_refs && JSON.stringify([...params.selected_refs].sort()) !== JSON.stringify([...selectedRefs].sort())) throw new Error("模型指定对象与本轮选中对象不一致，未生成修改提案");
      const effectiveStage = stage;
      const effectiveRefs = selectedRefs;
      if (!effectiveStage) throw new Error("请先选择要修改的任务阶段");
      if (params.replacement_asset_id || params.replacement_segment_id) {
        if (!params.replacement_asset_id || !params.replacement_segment_id
            || !replacementCandidates.has(`${params.replacement_asset_id}:${params.replacement_segment_id}`)) {
          throw new Error("替换来源不是本轮实际读取的可用候选，未生成提案");
        }
      }
      if (previewCreated) throw new Error("本轮已请求修改预览，请向用户说明结果后再决定下一步");
      previewCreated = true;
      const preview = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/preview`, {
        method: "POST",
        body: JSON.stringify({
          raw_command: originalInstruction,
          assistant_turn_id: assistantTurnId,
          context_stage: effectiveStage,
          selected_object_refs: effectiveRefs,
          playhead_ms: playheadMs,
          ...(params.replacement_asset_id ? { replacement_asset_id: params.replacement_asset_id,
            replacement_segment_id: params.replacement_segment_id } : {}),
        }),
      });
      if (preview.job_id && preview.result_kind === "edit_preview") {
        return {
          content: [{ type: "text", text: JSON.stringify({
            status: preview.status,
            waiting_reason: preview.waiting_reason,
            instruction: "已安排，预览尚未完成；完成后请在工作台查看实际预览，再确认是否应用。本轮不要重试或轮询作业，不得声称预览已生成或修改已应用。",
          }) }],
          details: { kind: "task_media_job", media_job: preview },
        };
      }
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
      if (editControlWriteAttempted) throw new Error("本轮已提交历史控制动作，不能同时开始、继续或取消制作");
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
