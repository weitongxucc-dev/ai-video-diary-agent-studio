import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';
import {rankByQuery, tokenizeQuery} from '../retrieval.ts';

export function registerScriptTools(pi: ToolRegistrar, {request, taskId, readActionSource, isTaskRead, hasPreview, markPreview, getScriptDraftUpdatedAt, setScriptDraftUpdatedAt}: {
  request: BusinessRequest; taskId: string | undefined; readActionSource: () => ActionSource; isTaskRead: () => boolean; hasPreview: () => boolean; markPreview: () => void; getScriptDraftUpdatedAt: () => string | null; setScriptDraftUpdatedAt: (value: string) => void;
}) {
  pi.registerTool({
    name: "find_knowledge",
    label: "检索已保存资料",
    description: "在当前工作区已保存的资料中查找与问题有关的事实。只返回短摘录和来源；无结果时明确说明。",
    parameters: {
      type: "object",
      properties: { query: { type: "string", minLength: 1, maxLength: 120 },
        offset: {type: "integer", minimum: 0}, limit: {type: "integer", minimum: 1, maximum: 5} },
      required: ["query"],
      additionalProperties: false,
    },
    execute: async (_id: string, params: { query: string; offset?: number; limit?: number }) => {
      const offset = params.offset ?? 0, limit = params.limit ?? 5;
      if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 5)
        throw new Error("知识读取范围必须是有界整数分页");
      const current = taskId ? await request(`/api/creation-tasks/${encodeURIComponent(taskId)}`) : null;
      if (current?.task?.stage !== "script") {
        const query = new URLSearchParams({query: params.query, offset: String(offset), limit: String(limit)});
        const data = await request(`/api/knowledge-items/search?${query}`);
        const details = {matches: data.items, total_count: data.total_count, offset: data.offset, limit: data.limit,
          next_offset: data.next_offset, query_terms: tokenizeQuery(params.query).slice(0, 8),
          scope: "current_authorized_knowledge_versions", instruction: "本次按当前成员权限重新查询当前知识版本；旧版本引用保持原版本，检索结果不授予使用或发布许可。",
          ...(!data.items.length ? {guidance: "没匹配到当前可读资料。请换用接近资料原文的关键词；不得沿旧结果假定资料仍可用。"} : {})};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      }
      if (!taskId) throw new Error("当前创作任务不可用");
      const data = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-context`);
      const items: Record<string, unknown>[] = data.sources || [];
      const textOf = (item: Record<string, unknown>) => `${item.title || ""} ${item.text || item.body || ""}`;
      // B08：按 bigram 子串打分排序，中文/英文混合都能命中；不再「匹配不上就静默返回空」。
      const ranked = rankByQuery(params.query, items, textOf);
      const matches = ranked.slice(offset, offset + limit).map(({ item, score, hitTerms }) => ({
        id: item.reference_id,
        title: item.title,
        version: item.version,
        excerpt: String(item.text || item.body || "").slice(0, 900),
        relevance: Math.round(score * 100) / 100,
        matched_terms: hitTerms.slice(0, 6),
      }));
      const guidance = matches.length === 0
        ? "没匹配到已保存的资料。换用更接近资料原文的词（资料多为中文，建议 2-4 字关键词），或先问用户该看哪方面，再检索。"
        : undefined;
      const details = { matches, total_count: ranked.length, offset, limit,
        next_offset: offset + matches.length < ranked.length ? offset + matches.length : null,
        scope: "current_task_allowed_sources", query_terms: tokenizeQuery(params.query).slice(0, 8), ...(guidance ? { guidance } : {}) };
      return { content: [{ type: "text", text: JSON.stringify(details) }], details };
    },
  });

  pi.registerTool({
    name: "preview_script_draft",
    label: "保存待确认文案",
    description: "保存当前任务完整文案及真实来源。已保存自主委托时采用这个实际版本并继续；讨论仅保留草稿。固定定稿不能经此工具改写。",
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
      if (!taskId) throw new Error("当前步骤不能保存文案草稿");
      if (!isTaskRead()) throw new Error("请先读取当前任务和允许使用的资料");
      if (hasPreview()) throw new Error("本轮已有尚未采用的方案，不能另写文案");
      const context = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-context`);
      const draft = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-draft`, {
        method: "POST",
        body: JSON.stringify({ title: params.title, assistant_turn_id: readActionSource().turnId, sections: params.sections.map((section, index) => ({
          ...section, id: section.id || `section_${String(index + 1).padStart(2, "0")}`,
        })), ...(getScriptDraftUpdatedAt() ? { expected_updated_at: getScriptDraftUpdatedAt() } : {}) }),
      });
      if (typeof draft.updated_at === "string") setScriptDraftUpdatedAt(draft.updated_at);
      const delegated = context.creation_intent?.kind === "goal_delegation";
      if (delegated) {
        const adopted = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(draft.id)}/confirm`, {
          method: "POST", body: JSON.stringify({title: draft.title, sections: draft.sections,
            expected_updated_at: draft.updated_at, adoption_kind: "delegated_system", assistant_turn_id: readActionSource().turnId}),
        });
        if (adopted.task?.confirmed_script_id !== draft.id || !adopted.scripts?.some((item: any) => item.id === draft.id
            && item.selection_status === "confirmed" && item.adoption?.kind === "delegated_system")) {
          throw new Error("文案采用未返回对应实际版本，没有宣称采用完成");
        }
      } else markPreview();
      const preview = {script_id: draft.id, adopted: delegated};
      return {
        content: [{ type: "text", text: JSON.stringify({
          title: draft.title, section_count: draft.sections.length,
          instruction: delegated ? "实际文案版本已按原委托采用；继续已授权制作步骤，尚未生成成片。"
            : "文案已保存为草稿，尚未采用或开始制作。",
        }) }],
        details: { kind: "script_draft", preview },
      };
    },
  });

}
