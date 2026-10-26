import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';
import {BusinessRequestError, RequestNotSubmitted} from './transport.ts';
import {boundedText} from '../task_facts.ts';

export function registerObservationTools(pi: ToolRegistrar, {request, taskId, verifyTaskVersion, readActionSource, typedRequestId}: {
  request: BusinessRequest; taskId: string | undefined; verifyTaskVersion: (workspace: any) => void; readActionSource: () => ActionSource; typedRequestId: (turn: string, call: string) => string;
}) {
    pi.registerTool({
      name: "read_task_observations", label: "读取实际结果检查",
      description: "只读当前任务已保存的技术检查、实际输出采样及其版本/摘要。stale只代表历史；unavailable不代表看过或听过。只返回本地测量与文字，不读取或外发媒体。",
      parameters: {type: "object", properties: {}, additionalProperties: false},
      execute: async () => {
        const details = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/observations`);
        if (details.task_id !== taskId) throw new Error("结果观察不属于当前任务");
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
    const observationAttempts = new Set<string>();
    let observationBlocked = false;
    pi.registerTool({
      name: "observe_task_output", label: "检查实际输出",
      description: "沿当前成员/版本及设备许可安排一次实际输出检查。final检查当前成片；media_job仅检查本任务已成功登记的指定artifact_id。最多3个本地画面采样，返回技术测量及可用OCR；语义/听感无能力时明确unavailable。排队不是已观察，结果通过read_task_media_job回读；未知先按原请求查询，不重提交。",
      parameters: {type: "object", properties: {
        target: {oneOf: [
          {type: "object", properties: {kind: {const: "final"}}, required: ["kind"], additionalProperties: false},
          {type: "object", properties: {kind: {const: "media_job"}, job_id: {type: "string", minLength: 1, maxLength: 120},
            artifact_id: {type: "string", minLength: 1, maxLength: 120}}, required: ["kind", "job_id", "artifact_id"], additionalProperties: false},
        ]}, sample_times_seconds: {type: "array", maxItems: 3, items: {type: "number", minimum: 0}},
      }, required: ["target"], additionalProperties: false},
      execute: async (id: string, params: {target: {kind: "final"} | {kind: "media_job"; job_id: string; artifact_id: string}; sample_times_seconds?: number[]}) => {
        const signature = JSON.stringify([params.target.kind,
          params.target.kind === "media_job" ? params.target.job_id : null,
          params.target.kind === "media_job" ? params.target.artifact_id : null,
          params.sample_times_seconds?.length ? params.sample_times_seconds : null]);
        if (observationBlocked || observationAttempts.has(signature))
          throw new Error("本轮观察尚待核验或已提交相同观察；请回读原请求与实际结果，不重复提交");
        const task = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}`);
        if (task.task?.id !== taskId) throw new Error("任务响应身份不一致");
        verifyTaskVersion(task);
        const turn = readActionSource().turnId;
        if (!turn) throw new Error("观察缺少宿主绑定的真实轮次，未提交操作");
        const requestId = typedRequestId(turn, id);
        const query = `/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${requestId}?phase=observation`;
        observationAttempts.add(signature);
        let details: any;
        try {
          const media_job = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/observations`, {method: "POST",
            body: JSON.stringify({...params, expected_timeline_version_id: task.task.current_timeline_version_id ?? null,
              expected_timeline_sequence: task.task.timeline_sequence, assistant_turn_id: turn, request_id: requestId})});
          if (!media_job.job_id || media_job.result_kind !== "observation") throw new Error("观察未返回可核验作业；请查原请求");
          details = {kind: "task_media_job", media_job: {...media_job, request_id: requestId, request_query_url: query},
            meaning: "这是实际输出检查作业；排队/运行未完成时不能声称已经检查。"};
        } catch (error) {
          const knownRejection = error instanceof BusinessRequestError && error.httpStatus < 500;
          observationBlocked = !(error instanceof BusinessRequestError && error.httpStatus === 422);
          details = {kind: "edit_request_status", task_id: taskId, request_id: requestId, query_url: query,
            phase: "observation", assistant_turn_id: turn, tool_call_id: id,
            base_version_id: task.task.current_timeline_version_id ?? null, base_sequence: task.task.timeline_sequence,
            status: error instanceof RequestNotSubmitted ? "not_submitted" : knownRejection ? "rejected" : "result_unknown",
            failure_reason: boundedText(error instanceof Error ? error.message : String(error), 500)};
          return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
        }
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
    pi.registerTool({
      name: "read_task_observation_request", label: "查询原观察请求",
      description: "仅GET原phase=observation回执，不重POST、不换绑版本；recorded表示受理，须回读真实作业后才判断实际检查结果。",
      parameters: {type: "object", properties: {request_id: {type: "string", format: "uuid"}}, required: ["request_id"], additionalProperties: false},
      execute: async (_id: string, params: {request_id: string}) => {
        const receipt = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${encodeURIComponent(params.request_id)}?phase=observation`);
        if (receipt.request_id !== params.request_id || receipt.phase !== "observation") throw new Error("观察请求回执身份不一致");
        const details = receipt.status === "recorded" && receipt.result?.job_id
          ? {kind: "task_media_job", media_job: receipt.result}
          : {kind: "edit_request_status", ...receipt};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
}
