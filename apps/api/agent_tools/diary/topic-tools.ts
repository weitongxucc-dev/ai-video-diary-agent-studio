import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';

export function registerTopicTools(pi: ToolRegistrar, {request, taskId, readActionSource, verifyTaskVersion, isTaskRead, hasPreview, markPreview}: {
  request: BusinessRequest; taskId: string | undefined; readActionSource: () => ActionSource; verifyTaskVersion: (workspace: any) => void; isTaskRead: () => boolean; hasPreview: () => boolean; markPreview: () => void;
}) {
  if (taskId) {
    pi.registerTool({
      name: "generate_creation_task_topics",
      label: "生成选题候选",
      description: "为缺少主题的当前任务生成真实候选。已有用户主题沿既有版本使用；已保存自主委托可用采用工具选择实际候选，只讨论保持候选。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      execute: async () => {
        if (!taskId || !isTaskRead()) throw new Error("请先读取当前创作任务");
        if (hasPreview()) throw new Error("本轮已有尚未采用的方案，先处理实际方案状态");
        const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}`);
        verifyTaskVersion(current);
        if (current.task?.stage !== "topic") throw new Error("当前任务已有主题或已进入后续阶段，不重新生成选题");
        const topics = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/topics/generate`, { method: "POST", body: JSON.stringify({}) });
        if (current.plan?.creation_intent?.kind !== "goal_delegation") markPreview();
        return {
          content: [{ type: "text", text: JSON.stringify({
            candidate_count: topics.candidates?.length ?? 0,
            candidates: topics.candidates,
            instruction: current.plan?.creation_intent?.kind === "goal_delegation"
              ? "候选已保存；按原委托选择真实候选并采用，再继续文案。"
              : "候选已保存，尚未采用；讨论不开始制作。",
          }) }],
          details: { kind: "topic_generation", topics },
        };
      },
    });
    pi.registerTool({
      name: "adopt_creation_task_topic", label: "采用真实主题",
      description: "仅在已保存的自主制作委托内，采用当前任务的实际候选；授权由业务仓储核验，不接受自报授权或其他任务ID。",
      parameters: {type: "object", properties: {candidate_id: {type: "string", minLength: 1}},
        required: ["candidate_id"], additionalProperties: false},
      execute: async (_id: string, params: {candidate_id: string}) => {
        if (!isTaskRead()) throw new Error("请先读取当前任务");
        const topics = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/topics/${encodeURIComponent(params.candidate_id)}/confirm`,
          {method: "POST", body: JSON.stringify({adoption_kind: "delegated_system", assistant_turn_id: readActionSource().turnId})});
        if (topics.task?.id !== taskId || topics.selected_topic_id !== params.candidate_id) {
          throw new Error("主题采用回执与当前任务或实际候选不一致");
        }
        return {content: [{type: "text", text: JSON.stringify({selected_topic_id: topics.selected_topic_id,
          instruction: "真实主题已在原委托内采用，继续读取文案上下文。"})}], details: {kind: "topic_generation", topics}};
      },
    });
  }

}
