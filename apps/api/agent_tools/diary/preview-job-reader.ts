import type {BusinessRequest, ToolRegistrar} from './contracts.ts';
import {setTimeout as delay} from 'node:timers/promises';

export function registerPreviewJobReader(pi: ToolRegistrar, {request, taskId, getSignal, onPreviewJobObserved}: {
  request: BusinessRequest; taskId: string | undefined; getSignal: () => AbortSignal | undefined; onPreviewJobObserved: (job: any) => void;
}) {
    pi.registerTool({
      name: "read_task_media_job", label: "读取本轮预览作业结果",
      description: "只读可信任务作业；codemode返回结构化对象，直接读media_job，不JSON.parse对象。可用wait_seconds进行至多20秒有限GET等待，每秒一次；超时仍返回最后实际pending状态，不重执行。PNG来源成功不表示已应用图层或出片。",
      parameters: {type: "object", properties: {job_id: {type: "string", maxLength: 120},
        wait_seconds: {type: "integer", minimum: 0, maximum: 20}}, required: ["job_id"], additionalProperties: false},
      execute: async (_id: string, params: {job_id: string; wait_seconds?: number}) => {
        const seconds = params.wait_seconds ?? 0;
        if (!Number.isInteger(seconds) || seconds < 0 || seconds > 20) throw new Error("作业等待须为0–20秒整数");
        const parentSignal = getSignal();
        const deadline = AbortSignal.timeout(seconds > 0 ? seconds * 1000 : 15000);
        const signal = parentSignal ? AbortSignal.any([parentSignal, deadline]) : deadline;
        let media_job: any = null;
        for (;;) {
          try {
            const value = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/media-jobs/${encodeURIComponent(params.job_id)}`, {signal});
            if (value.job_id !== params.job_id) throw new Error("作业响应身份不一致");
            media_job = value;
          } catch (error) {
            if (parentSignal?.aborted || !deadline.aborted || !media_job) throw error;
            break;
          }
          if (!seconds || !["queued", "running"].includes(media_job.status) || signal.aborted) break;
          try { await delay(1000, undefined, {signal}); }
          catch (error) { if (parentSignal?.aborted || !deadline.aborted) throw error; break; }
        }
        onPreviewJobObserved(media_job);
        const details = {kind: "task_media_job", media_job,
          wait: {seconds, deadline_reached: deadline.aborted, queried_only: true},
          ...(media_job.result_kind === "graphic_source" ? {meaning: "这是PNG图形来源作业，未应用图层，未生成成片。"} : {})};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
}
