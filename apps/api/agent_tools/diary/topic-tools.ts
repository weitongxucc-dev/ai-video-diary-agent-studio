import type {BusinessRequest, ToolRegistrar} from './contracts.ts';

export function registerTopicTools(pi: ToolRegistrar, {request, taskId, stage, isTaskRead, hasPreview, markPreview}: {
  request: BusinessRequest; taskId: string | undefined; stage: string | undefined; isTaskRead: () => boolean; hasPreview: () => boolean; markPreview: () => void;
}) {
  if (stage === "topic") {
    pi.registerTool({
      name: "generate_creation_task_topics",
      label: "生成选题候选",
      description: "仅在用户明确要求生成选题时，调用当前任务已有选题阶段；只生成候选，绝不代替用户选择或确认。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      execute: async () => {
        if (!taskId || !isTaskRead()) throw new Error("请先读取当前创作任务");
        if (hasPreview()) throw new Error("本轮已生成选题候选，请用户检查并选择");
        const topics = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/topics/generate`, { method: "POST", body: JSON.stringify({}) });
        markPreview();
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

}
