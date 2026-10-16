import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';
import {BusinessRequestError, RequestNotSubmitted} from './transport.ts';
import {boundedText} from '../task_facts.ts';
import {setTimeout as delay} from 'node:timers/promises';

export function registerExecutionTools(pi: ToolRegistrar, {request, taskId, stage, isTaskRead, hasCreationAction, markCreationAction, hasEditControlWrite, directEditUnverified, hasPreview, readActionSource, getSignal}: {
  request: BusinessRequest; taskId: string | undefined; stage: string | undefined; isTaskRead: () => boolean; hasCreationAction: () => boolean; markCreationAction: () => void; hasEditControlWrite: () => boolean; directEditUnverified?: () => boolean; hasPreview: () => boolean; readActionSource: () => ActionSource; getSignal: () => AbortSignal | undefined;
}) {
  if (taskId && stage !== "topic" && stage !== "script") {
    pi.registerTool({
      name: "read_creation_execution", label: "读取原制作作业",
      description: "只读指定普通制作作业，使用原 job_id，不走媒体预览作业接口。最多20秒有限GET等待，不重启或恢复制作；若任务已有新作业明确拒绝，不把新作业当原结果。job succeeded仍须video_ready才是当前有效成片。",
      parameters: {type: "object", properties: {job_id: {type: "string", minLength: 1, maxLength: 120},
        wait_seconds: {type: "integer", minimum: 0, maximum: 20}}, required: ["job_id"], additionalProperties: false},
      execute: async (_id: string, params: {job_id: string; wait_seconds?: number}) => {
        const seconds = params.wait_seconds ?? 0;
        if (!Number.isInteger(seconds) || seconds < 0 || seconds > 20) throw new Error("作业等待须为0–20秒整数");
        const parentSignal = getSignal();
        const deadline = AbortSignal.timeout(seconds > 0 ? seconds * 1000 : 15000);
        const signal = parentSignal ? AbortSignal.any([parentSignal, deadline]) : deadline;
        let status: any = null;
        for (;;) {
          try {
            const value = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution?job_id=${encodeURIComponent(params.job_id)}`, {signal});
            if (value.task_id !== taskId || value.job?.id !== params.job_id || value.job.task_id !== taskId) throw new Error("原制作作业响应身份不一致");
            status = value;
          } catch (error) {
            if (parentSignal?.aborted || !deadline.aborted || !status) throw error;
            break;
          }
          if (!seconds || !["queued", "running"].includes(status.job.status) || signal.aborted) break;
          try { await delay(1000, undefined, {signal}); }
          catch (error) { if (parentSignal?.aborted || !deadline.aborted) throw error; break; }
        }
        const details = {kind: "execution_result", status, wait: {seconds, deadline_reached: deadline.aborted, queried_only: true}};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
    const perform = async (action: "start" | "resume" | "cancel") => {
      if (!isTaskRead()) throw new Error("请先读取当前创作任务");
      if (hasCreationAction()) throw new Error("本轮制作动作已经提交，不重复执行");
      if (directEditUnverified?.()) throw new Error("本次直接编辑结果未核实，不能继续制作；请先查询原请求");
      if (hasEditControlWrite()) throw new Error("本轮已提交历史控制动作，不能同时开始、继续或取消制作");
      if (hasPreview()) throw new Error("本轮已请求修改预览，不能同时开始、继续或取消制作");
      markCreationAction();
      const turn = readActionSource().turnId;
      if (action !== "cancel" && !turn) throw new RequestNotSubmitted("制作请求缺少当前助手轮次，未提交");
      const binding = action !== "cancel" ? `?assistant_turn_id=${encodeURIComponent(turn!)}` : "";
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
          ? await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/resume${binding}`, {
            method: "POST", body: JSON.stringify({}),
          })
          : await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/start${binding}`, {
            method: "POST",
            body: JSON.stringify({ script_id: preview.script_id, input_digest: preview.input_digest, requirements_confirmed: true }),
          });
      } else {
        status = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/${action}${binding}`, {
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
        execute: async () => {
          try { return await perform(action); }
          catch (error) {
            const reason = boundedText(error instanceof Error ? error.message : String(error), 500).text;
            const repair = error instanceof BusinessRequestError && typeof error.repairAction === "string" ? boundedText(error.repairAction, 500).text : undefined;
            return {isError: true, content: [{type: "text", text: reason}], details: {
              kind: "execution_action_failed", action, failure: {reason, ...(repair ? {repair_action: repair} : {})}}};
          }
        },
      });
    }
  }
}
