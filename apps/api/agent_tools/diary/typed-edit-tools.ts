import type {BusinessRequest, ToolRegistrar, TaskVersion, ActionSource} from './contracts.ts';
import {typedEditSchema, isHostEditField} from './schemas.ts';
import {BusinessRequestError, RequestNotSubmitted} from './transport.ts';
import {boundedText} from '../task_facts.ts';

export function createTypedEditTools({request, taskId, selectedTaskVersion, isTaskRead, readActionSource, typedRequestId, getEditCapabilities, hasCreationAction, hasEditControlWrite, markPreview}: {
  request: BusinessRequest; taskId: string | undefined; selectedTaskVersion: TaskVersion | null; isTaskRead: () => boolean; readActionSource: () => ActionSource; typedRequestId: (turn: string, call: string) => string; getEditCapabilities: () => any; hasCreationAction: () => boolean; hasEditControlWrite: () => boolean; markPreview: () => void;
}) {
  let typedAttempts = 0;
  let typedPreviewBlocked = false;
  const typedInputs = new Set<string>();
  function registerRequestReader(pi: ToolRegistrar) {
    pi.registerTool({
      name: "read_typed_edit_request", label: "回读已提交编辑请求",
      description: "用原请求身份查询真实结果；只读，不重POST，不换绑当前版本或从对话推测成功。",
      parameters: {type: "object", properties: {request_id: {type: "string", format: "uuid"}}, required: ["request_id"], additionalProperties: false},
      execute: async (_id: string, params: {request_id: string}) => {
        const query = `/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${encodeURIComponent(params.request_id)}?phase=preview`;
        const receipt = await request(query);
        if (receipt.request_id !== params.request_id || receipt.phase !== "preview") throw new Error("编辑请求回执身份不一致");
        let details: any = {kind: "edit_request_status", request_id: params.request_id, query_url: query,
          status: receipt.status, result: receipt.result};
        if (receipt.status === "recorded" && receipt.result?.proposal?.id) {
          typedPreviewBlocked = false;
          details = {kind: "edit_preview", preview: {...receipt.result, request_id: params.request_id, request_query_url: query}};
        } else if (receipt.status === "recorded" && receipt.result?.job_id) {
          details = {kind: "task_media_job", media_job: {...receipt.result, request_id: params.request_id, request_query_url: query}};
        }
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
  }
  function registerPreview(pi: ToolRegistrar) {
    if (getEditCapabilities()) {
      const parameters = typedEditSchema(getEditCapabilities());
      pi.registerTool({
        name: "preview_typed_edit", label: "预览类型化修改",
        description: "使用 API 真实 schema 提交类型化编辑；任务、用户原句、身份和基础版本由宿主绑定。音频EditAudioTimeline的audio_edit指定真实target_id、update片段或track音轨、非空values patch；画面AdjustVisualEffects指定真实clip_ids和非空effects patch。先读当前对象，降低6dB须用当前gain_db计算新绝对值，只传指定字段，遗漏保留、重置显式提交；淡入淡出音频用毫秒，画面用秒。duck_under_voice只音乐轨；本轮不开放音量关键点写入、solo持久化或独立音频变速。不绕过人像/资源许可。只生成提案/实际预览，不应用或宣称成片。最多3个不同候选，同输入、未知结果或授权/版本拒绝不重放。",
        parameters,
        execute: async (_id: string, params: Record<string, unknown>) => {
          if (!isTaskRead() || !selectedTaskVersion) throw new Error("请先读取本轮可信任务与版本");
          if (hasCreationAction() || hasEditControlWrite()) throw new Error("本轮已有执行或历史控制动作");
          if (typedPreviewBlocked || typedAttempts >= 3) throw new Error("本轮预览尚待核验结果或达到3次上限；不重复未知操作");
          if (Object.keys(params).some(key => isHostEditField(key) || !Object.hasOwn(parameters.properties, key)))
            throw new Error("模型参数包含未知字段或可信宿主字段，未提交预览");
          const capability = getEditCapabilities().operations.find((item: any) => item.operation_type === params.operation_type);
          if (!capability || !capability.available) throw new Error(capability?.reason || "当前 API 未声明该类型化预览可执行");
          const instruction = readActionSource().userInstruction;
          const turn = readActionSource().turnId;
          if (!instruction || !turn) throw new Error("本轮原句或身份未绑定，未提交预览");
          if (typeof _id !== "string" || !_id) throw new Error("本轮可信工具调用身份缺失，未提交预览");
          const input = JSON.stringify(params, (_key, value) => value && typeof value === "object" && !Array.isArray(value)
            ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
          if (typedInputs.has(input)) throw new Error("本轮已提交相同预览，不重复请求");
          typedInputs.add(input); typedAttempts++; typedPreviewBlocked = true; markPreview();
          const requestId = typedRequestId(turn, _id);
          const queryUrl = `/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${requestId}?phase=preview`;
          let preview: any;
          try {
            const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/exact/preview`, {
              method: "POST", body: JSON.stringify({...params, assistant_turn_id: turn, request_id: requestId,
                expected_timeline_version_id: selectedTaskVersion.timeline_version_id,
                expected_timeline_sequence: selectedTaskVersion.timeline_sequence}),
            });
            preview = {...result, request_id: requestId, request_query_url: queryUrl};
          } catch (error) {
            const knownRejection = error instanceof BusinessRequestError && error.httpStatus < 500;
            // Only the API's parameter validation rejection permits another
            // distinct candidate. Auth/version denial and lost receipts stay closed.
            const parameterRejected = error instanceof BusinessRequestError && error.httpStatus === 422;
            if (parameterRejected) typedPreviewBlocked = false;
            const details = {kind: "edit_request_status", task_id: taskId, request_id: requestId, query_url: queryUrl,
              phase: "preview", assistant_turn_id: turn, tool_call_id: _id,
              status: error instanceof RequestNotSubmitted ? "not_submitted" : knownRejection ? "rejected" : "result_unknown",
              base_version_id: selectedTaskVersion.timeline_version_id, base_sequence: selectedTaskVersion.timeline_sequence,
              failure_reason: boundedText(error instanceof Error ? error.message : String(error), 500),
              correction_allowed: parameterRejected,
              instruction: parameterRejected ? "参数已明确拒绝，没有生成修改；可在原授权和版本内纠正参数准备另一候选，不重发相同输入，仍受本轮3次上限。"
                : "原请求身份已保留；先回读实际结果，不自动重POST或换绑最新版本。"};
            return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
          }
          let details: any;
          if (preview.job_id && preview.result_kind === "edit_preview") {
            details = {kind: "task_media_job", media_job: preview};
          } else {
            if (!preview.proposal?.id) throw new Error("预览未返回可核验提案；结果未知，不重放");
            typedPreviewBlocked = false;
            details = {kind: "edit_preview", preview};
          }
          return {content: [{type: "text", text: JSON.stringify(details)}], details};
        },
      });
    }
  }
  function onPreviewJobObserved(media_job: any) {
    if (media_job.status === "succeeded" && media_job.result?.proposal?.id
        && media_job.base_version_id === selectedTaskVersion?.timeline_version_id) typedPreviewBlocked = false;
  }
  return {registerRequestReader, registerPreview, onPreviewJobObserved};
}
