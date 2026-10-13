import type {BusinessRequest, ToolRegistrar} from './contracts.ts';
import {rankByQuery, tokenizeQuery} from '../retrieval.ts';

export function registerScriptTools(pi: ToolRegistrar, {request, taskId, stage, isTaskRead, hasPreview, markPreview, getScriptDraftUpdatedAt, setScriptDraftUpdatedAt}: {
  request: BusinessRequest; taskId: string | undefined; stage: string | undefined; isTaskRead: () => boolean; hasPreview: () => boolean; markPreview: () => void; getScriptDraftUpdatedAt: () => string | null; setScriptDraftUpdatedAt: (value: string) => void;
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
      if (stage !== "script") {
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
      if (!isTaskRead()) throw new Error("请先读取当前任务和允许使用的资料");
      if (hasPreview()) throw new Error("本轮已保存文案草稿，请用户检查后再继续");
      const draft = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-draft`, {
        method: "POST",
        body: JSON.stringify({ title: params.title, sections: params.sections.map((section, index) => ({
          ...section, id: section.id || `section_${String(index + 1).padStart(2, "0")}`,
        })), ...(getScriptDraftUpdatedAt() ? { expected_updated_at: getScriptDraftUpdatedAt() } : {}) }),
      });
      if (typeof draft.updated_at === "string") setScriptDraftUpdatedAt(draft.updated_at);
      markPreview();
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

}
