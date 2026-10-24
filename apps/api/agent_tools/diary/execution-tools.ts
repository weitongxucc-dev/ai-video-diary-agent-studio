import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';
import {BusinessRequestError, BusinessResultUnknown, RequestNotSubmitted, safeBusinessFailure} from './transport.ts';
import {boundedText} from '../task_facts.ts';
import {setTimeout as delay} from 'node:timers/promises';

export function registerExecutionTools(pi: ToolRegistrar, {request, taskId, isTaskRead, hasCreationAction, markCreationAction, hasEditControlWrite, directEditUnverified, hasPreview, readActionSource, getSignal, readCurrentTaskFacts, typedRequestId}: {
  request: BusinessRequest; taskId: string | undefined; isTaskRead: () => boolean; hasCreationAction: () => boolean; markCreationAction: () => void; hasEditControlWrite: () => boolean; directEditUnverified?: () => boolean; hasPreview: () => boolean; readActionSource: () => ActionSource; getSignal: () => AbortSignal | undefined;
  readCurrentTaskFacts: () => Promise<any>; typedRequestId: (turn: string, call: string) => string;
}) {
  if (taskId) {
    pi.registerTool({name: "read_execution_capabilities", label: "读取执行单元能力",
      description: "只读当前任务可用执行单元的配置、限制单位与范围、验证未知状态和续作缺口。已配置不等于实测通过或整片可完成；不进行资源准入、执行、提额或制作。",
      parameters: {type: "object", properties: {}, additionalProperties: false},
      execute: async () => {
        const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/execution-capabilities`);
        const details = {kind: "execution_capabilities", capabilities: result};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      }});
    let storyboardAttempt: {inputKey: string; requestId: string} | null = null;
    const storyboardPath = `/api/creation-tasks/${encodeURIComponent(taskId)}/montage/plan`;
    const storyboardRequestPath = (id: string) => `${storyboardPath}/requests/${encodeURIComponent(id)}`;
    const checkStoryboardReceipt = (receipt: any, requestId: string) => {
      if (receipt.task_id !== taskId || receipt.request_id !== requestId || receipt.stage !== "montage_plan"
          || typeof receipt.job_id !== "string" || !["queued", "running", "interrupted", "succeeded", "failed", "cancelled"].includes(receipt.status)
          || (receipt.status === "succeeded" && (!receipt.result?.timeline_version_id || !receipt.result?.montage_plan_id
            || receipt.result.request_id !== requestId)))
        throw new Error(`故事板回执身份不一致，先查询原请求 ${requestId}`);
      return receipt;
    };
    pi.registerTool({name: "read_storyboard_regeneration", label: "查询原故事板重算",
      description: "只读显式故事板重算的原UUID；丢失排队回应先查询，不重发。成功仅表示新故事板版本已保存，不代表已生成视频；回执保留原结果，即使随后已撤销。",
      parameters: {type: "object", properties: {request_id: {type: "string", format: "uuid"}}, required: ["request_id"], additionalProperties: false},
      execute: async (_id: string, params: {request_id: string}) => {
        const receipt = checkStoryboardReceipt(await request(storyboardRequestPath(params.request_id)), params.request_id);
        const details = {kind: "storyboard_regeneration_receipt", receipt};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      }});
    pi.registerTool({name: "regenerate_storyboard", label: "重新生成故事板",
      description: "仅用户明确要求整体重新生成故事板时调用。复用当前采用文案与原配音，只排队重算镜头并保存可撤销版本；保留字幕、图层和声音，不配音或渲染。原作业仍运行、排队或未释放时拒绝，不自动取消。普通开始、继续制作不会重算；未知结果只查原UUID。",
      parameters: {type: "object", properties: {}, additionalProperties: false},
      execute: async (callId: string) => {
        const source = readActionSource();
        if (!source.turnId || !isTaskRead()) throw new Error("请先读取当前任务和可信要求，再重新生成故事板");
        const inputKey = source.inputId ?? source.turnId;
        if (storyboardAttempt?.inputKey === inputKey)
          throw new Error(`本轮故事板重算已提交，先查询原请求 ${storyboardAttempt.requestId}，不重复写入`);
        if (hasCreationAction() || hasEditControlWrite() || hasPreview() || directEditUnverified?.())
          throw new Error("本轮已有制作或未核实的编辑动作，请先读取原结果");
        const before = await readCurrentTaskFacts();
        const preview = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/preview`);
        if (before.task_id !== taskId || preview.task_id !== taskId || !before.current_timeline_version_id
            || !Number.isInteger(before.timeline_sequence) || before.timeline_sequence < 1
            || !before.confirmed_script_id || preview.script_id !== before.confirmed_script_id
            || typeof preview.input_digest !== "string" || !/^[a-f0-9]{64}$/.test(preview.input_digest))
          throw new Error("当前时间线或采用文案事实不完整，没有提交故事板重算");
        if ((readActionSource().inputId ?? readActionSource().turnId) !== inputKey)
          throw new Error("当前要求已变化，没有提交旧输入的故事板重算");
        const requestId = typedRequestId(source.turnId, callId);
        storyboardAttempt = {inputKey, requestId};
        markCreationAction();
        let receipt;
        try {
          receipt = await request(storyboardPath, {method: "POST", body: JSON.stringify({request_id: requestId,
            assistant_turn_id: source.turnId, script_id: preview.script_id, input_digest: preview.input_digest,
            expected_timeline_version_id: before.current_timeline_version_id, expected_timeline_sequence: before.timeline_sequence})});
        } catch (error) {
          const unknown = !(error instanceof BusinessRequestError);
          const details = {kind: unknown ? "storyboard_regeneration_unknown" : "storyboard_regeneration_rejected",
            request_id: requestId, query_url: storyboardRequestPath(requestId), failure: {
              reason: unknown ? "故事板重算回应未知，尚未宣称完成。" : boundedText(error.message, 500).text,
              repair_action: `先查询原请求 ${requestId}；不要重复提交故事板重算。`}};
          return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
        }
        const details = {kind: "storyboard_regeneration_receipt", receipt: checkStoryboardReceipt(receipt, requestId)};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      }});
    let voiceAttempt: {inputKey: string; requestId: string; submissionEffect?: string} | null = null;
    const voiceRequestPath = (id: string) => `/api/creation-tasks/${encodeURIComponent(taskId)}/narration/voice/requests/${encodeURIComponent(id)}`;
    pi.registerTool({name: "read_narration_voice_selection", label: "查询原声音选择",
      description: "只读原声音采用UUID的不可变回执；响应丢失先查询，不重复提交。回执不是当前方案，继续制作前回读当前任务。",
      parameters: {type: "object", properties: {request_id: {type: "string", format: "uuid"}}, required: ["request_id"], additionalProperties: false},
      execute: async (_id: string, params: {request_id: string}) => {
        const receipt = await request(voiceRequestPath(params.request_id));
        if (receipt.task_id !== taskId || receipt.request_id !== params.request_id) throw new Error("原声音选择回执身份不一致");
        const current = await readCurrentTaskFacts();
        const details = {kind: "voice_selection_receipt", receipt, current_task_facts: current};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      }});
    pi.registerTool({name: "select_narration_voice", label: "选择配音声音",
      description: "用户明确要求选声，或已委托在工作区已授权声音范围内自主完成时，可为当前已采用稿选择候选中的固定版本、修正系统代选声音；用户点名或锁定的声音不可擅改。保留同工作区、固定版本及方案/任务修订校验，限配音未开始或失败状态，不改默认声音、不生成配音。未知结果先查询原请求，不重复提交。",
      parameters: {type: "object", properties: {voice_profile_id: {type: "string", minLength: 1, maxLength: 120},
        voice_version: {type: "string", minLength: 1, maxLength: 120}}, required: ["voice_profile_id", "voice_version"], additionalProperties: false},
      execute: async (callId: string, params: {voice_profile_id: string; voice_version: string}) => {
        const source = readActionSource();
        if (!source.turnId || !isTaskRead()) throw new Error("请先读取当前任务和可信要求，再选择声音");
        const inputKey = source.inputId ?? source.turnId;
        if (voiceAttempt?.inputKey === inputKey) {
          if (voiceAttempt.submissionEffect && voiceAttempt.submissionEffect !== 'unknown')
            throw new Error('本轮声音选择已被拒绝；先处理原回执原因并读取当前任务，不重复写入。');
          throw new Error(`本轮声音选择已提交，先查询原请求 ${voiceAttempt.requestId}，不重复写入`);
        }
        const before = await readCurrentTaskFacts();
        if (before.task_id !== taskId || before.stage !== "narration" || !["not_started", "failed"].includes(before.stage_status))
          throw new Error("当前任务不在可选择声音的配音状态");
        const selected = before.available_voices?.find((voice: any) => voice.profile_id === params.voice_profile_id && voice.version === params.voice_version);
        if (!selected?.runnable) throw new Error("所选固定声音不在当前可用候选中，请按实际候选重新选择");
        if (typeof before.plan?.updated_at !== "string" || typeof before.updated_at !== "string")
          throw new Error("当前任务缺少方案或任务修订，没有提交声音选择");
        if ((readActionSource().inputId ?? readActionSource().turnId) !== inputKey) throw new Error("当前要求已变化，没有提交旧输入的声音选择");
        const requestId = typedRequestId(source.turnId, callId);
        voiceAttempt = {inputKey, requestId};
        const attempt = voiceAttempt;
        let receipt;
        try {
          receipt = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/narration/voice`, {method: "POST",
            body: JSON.stringify({...params, request_id: requestId, assistant_turn_id: source.turnId,
              expected_plan_updated_at: before.plan.updated_at, expected_task_updated_at: before.updated_at})});
        } catch (error) {
          const original = error instanceof BusinessRequestError || error instanceof RequestNotSubmitted || error instanceof BusinessResultUnknown
            ? error.businessFailure : undefined;
          const safe = safeBusinessFailure({...original,
            reason: original?.reason ?? (error instanceof Error ? error.message : undefined),
            repair_action: original?.repair_action ?? (error instanceof BusinessRequestError ? error.repairAction : undefined)});
          const effect = safe.submission_effect ?? (error instanceof RequestNotSubmitted ? 'not_submitted'
            : error instanceof BusinessRequestError ? 'rejected' : 'unknown');
          attempt.submissionEffect = effect;
          if (effect === 'not_submitted' && voiceAttempt === attempt) voiceAttempt = null;
          const unknown = effect === 'unknown';
          const failure = {...safe, submission_effect: effect,
            reason: safe.reason || (unknown ? '声音选择响应未知，尚未宣称绑定完成。' : '声音选择未提交成功。'),
            repair_action: unknown ? `先查询原请求 ${requestId}；不要重复提交声音选择。${safe.repair_action ? ' 确定原结果后再处理：'+safe.repair_action : ''}`
              : safe.repair_action || '按原拒绝原因处理，读取当前任务后再决定下一步，不原样重提。'};
          const details = {kind: unknown ? 'voice_selection_unknown' : effect === 'not_submitted' ? 'voice_selection_not_submitted' : 'voice_selection_rejected',
            request_id: requestId, ...(unknown ? {query_url: voiceRequestPath(requestId)} : {}), failure};
          return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
        }
        if (receipt.task_id !== taskId || receipt.request_id !== requestId
            || receipt.selected_voice?.profile_id !== params.voice_profile_id || receipt.selected_voice?.version !== params.voice_version)
          throw new Error(`声音采用回执不匹配，先查询原请求 ${requestId}`);
        const current = await readCurrentTaskFacts();
        if (current.task_id !== taskId || current.plan?.selected_voice?.profile_id !== params.voice_profile_id
            || current.plan?.selected_voice?.version !== params.voice_version
            || current.plan.updated_at !== receipt.plan_updated_at || current.updated_at !== receipt.task_updated_at)
          throw new Error(`声音采用后的任务版本已变化，先查询原请求 ${requestId}并重新读取当前任务`);
        const details = {kind: "voice_selected", receipt, current_task_facts: current};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      }});
    pi.registerTool({
      name: "read_segment_visual_decision", label: "读取原段落画面候选",
      description: "只读原作业等待的画面候选、已保留音频和采用后状态。明确采用须由用户在制作面板确认；本工具不采用、不重配音、不换作业。",
      parameters: {type:"object",properties:{job_id:{type:"string",minLength:1},section_id:{type:"string",minLength:1},
        offset:{type:"integer",minimum:0},limit:{type:"integer",minimum:1,maximum:8}},
        required:["job_id","section_id"],additionalProperties:false},
      async execute(_id: string,params: {job_id:string;section_id:string;offset?:number;limit?:number}) {
        const result=await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/execution/jobs/${encodeURIComponent(params.job_id)}/sections/${encodeURIComponent(params.section_id)}/visual-decision?offset=${params.offset || 0}&limit=${params.limit || 8}`);
        if (result.task_id!==taskId || result.job_id!==params.job_id || result.section_id!==params.section_id) throw new Error("原画面决定响应身份不一致");
        const details={kind:"segment_visual_decision",decision:result};
        return {content:[{type:"text",text:JSON.stringify(details)}],details};
      },
    });
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
    let executionSubmissionPending = false;
    const submitExecution = async (action: "start" | "resume" | "cancel", binding: string, body: object) => {
      if (executionSubmissionPending || hasCreationAction()) throw new RequestNotSubmitted("本轮制作动作已经提交或正在提交，不重复执行");
      executionSubmissionPending = true;
      try {
        const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/${action}${binding}`, {
          method: "POST", body: JSON.stringify(body)});
        markCreationAction();
        return result;
      } catch (error) {
        // Transport performs its own version GET before POST. Only a known
        // not-submitted receipt can release this attempt; an unknown write cannot.
        if (!(error instanceof RequestNotSubmitted)) markCreationAction();
        throw error;
      } finally {
        executionSubmissionPending = false;
      }
    };
    const perform = async (action: "start" | "resume" | "cancel") => {
      if (!isTaskRead()) throw new RequestNotSubmitted("请先读取当前创作任务");
      if (executionSubmissionPending || hasCreationAction()) throw new RequestNotSubmitted("本轮制作动作已经提交或正在提交，不重复执行");
      if (directEditUnverified?.()) throw new RequestNotSubmitted("本次直接编辑结果未核实，不能继续制作；请先查询原请求");
      if (hasEditControlWrite()) throw new RequestNotSubmitted("本轮已提交历史控制动作，不能同时开始、继续或取消制作");
      if (hasPreview()) throw new RequestNotSubmitted("本轮已请求修改预览，不能同时开始、继续或取消制作");
      const turn = readActionSource().turnId;
      if (action !== "cancel" && !turn) throw new RequestNotSubmitted("制作请求缺少当前助手轮次，未提交");
      const binding = action !== "cancel" ? `?assistant_turn_id=${encodeURIComponent(turn!)}` : "";
      let status: Record<string, unknown>;
      if (action === "start") {
        const preview = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/preview`);
        const requirements = preview.requirements as { can_continue?: boolean; continuation_blocker?: string | null } | undefined;
        if (!requirements?.can_continue) {
          throw new RequestNotSubmitted(requirements?.continuation_blocker || "当前制作条件尚未就绪，未提交制作");
        }
        const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution`);
        const existingJob = current.job as { input_digest?: string; status?: string } | null;
        const canRetryCurrent = existingJob?.input_digest === preview.input_digest
          && (existingJob.status === "failed" || existingJob.status === "interrupted"
            || existingJob.status === "cancelled" || !current.video_ready);
        status = canRetryCurrent
          ? await submitExecution("resume", binding, {})
          : await submitExecution("start", binding, {script_id: preview.script_id, input_digest: preview.input_digest, requirements_confirmed: true});
      } else {
        status = await submitExecution(action, binding, {});
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
              kind: "execution_action_failed", action, failure: {reason, ...(repair ? {repair_action: repair} : {}),
                ...(error instanceof RequestNotSubmitted ? {error: "request_not_submitted", stage: "execution_preflight",
                  submission_effect: "not_submitted", task_id: taskId,
                  repair_action: "重新读取当前任务和原请求，处理阻断后再提交。"} : {})}}};
          }
        },
      });
    }
  }
}
