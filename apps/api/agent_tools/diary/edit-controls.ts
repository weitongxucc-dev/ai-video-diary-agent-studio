import type {BusinessRequest, ToolRegistrar, TaskVersion, ActionSource} from './contracts.ts';
import {BusinessRequestError, BusinessResultUnknown, RequestNotSubmitted, safeBusinessFailure} from './transport.ts';

export function registerEditControls(pi: ToolRegistrar, {request, taskId, selectedTaskVersion, isTaskRead, markTaskRead, verifyTaskVersion, readActionSource, typedRequestId, hasPreview, markPreview, hasCreationAction, hasEditControlWrite, markEditControlWrite, releaseNotSubmittedEditControl}: {
  request: BusinessRequest; taskId: string | undefined; selectedTaskVersion: TaskVersion | null; isTaskRead: () => boolean; verifyTaskVersion: (workspace: any) => void; readActionSource: () => ActionSource; hasPreview: () => boolean; markPreview: () => void; hasCreationAction: () => boolean; hasEditControlWrite: () => boolean; markEditControlWrite: () => void;
  markTaskRead: () => void; typedRequestId: (turn: string, call: string) => string;
  releaseNotSubmittedEditControl: () => void;
}) {
  type ControlStep = { index: number; command: string; enabled: boolean };
  type ControlProposal = { id: string; summary: string; operation_type: string; steps: ControlStep[] };
  type ControlContext = { task_id: string; timeline_version_id: string | null; timeline_sequence: number;
    can_undo: boolean; can_redo: boolean; proposals: ControlProposal[] };
  let editControls: ControlContext | null = null;
  const historicalOperations = new Set<string>();
  let historyRead = false;
  const restorePath = (id: string) => `/api/creation-tasks/${encodeURIComponent(taskId!)}/timeline/restore-requests/${encodeURIComponent(id)}`;
  const restoreResult = (action: 'undo' | 'redo' | 'query', requestId: string, receipt: any, error?: unknown) => {
    const original = error instanceof BusinessRequestError || error instanceof BusinessResultUnknown || error instanceof RequestNotSubmitted
      ? error.businessFailure : undefined;
    const failure = error ? {...safeBusinessFailure({...original,
      reason: original?.reason ?? (error instanceof Error ? error.message : String(error))}),
      submission_effect: original?.submission_effect ?? (error instanceof RequestNotSubmitted ? 'not_submitted'
        : error instanceof BusinessRequestError ? 'rejected' : 'unknown')} : undefined;
    const details = {kind: 'timeline_restore_request', action, request_id: requestId, receipt, ...(failure ? {failure} : {})};
    return {isError: Boolean(error), content: [{type: 'text' as const, text: JSON.stringify(details)}], details};
  };
  pi.registerTool({name: 'read_edit_history', label: '读取修改历史',
    description: '分页读取本任务真实版本和原操作ID、类型、目标引用及来源请求。用户指定较早修改时据此定位；不能猜ID，也不能用最近修改替代。历史可播放不代表当前采用。',
    parameters: {type: 'object', properties: {limit: {type: 'integer', minimum: 1, maximum: 50},
      before_sequence: {type: 'integer', minimum: 1}}, additionalProperties: false},
    execute: async (_id: string, params: {limit?: number; before_sequence?: number} = {}) => {
      if (!taskId) throw new Error('当前任务不可用');
      const query = new URLSearchParams({limit: String(params.limit ?? 20)});
      if (params.before_sequence !== undefined) query.set('before_sequence', String(params.before_sequence));
      const details = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/versions?${query}`);
      if (details.task_id !== taskId || !Array.isArray(details.items)) throw new Error('历史版本不属于当前任务');
      for (const item of details.items) if (typeof item.operation_id === 'string') historicalOperations.add(item.operation_id);
      historyRead = true;
      markTaskRead();
      return {content: [{type: 'text', text: JSON.stringify(details)}], details};
    }});
  pi.registerTool({name: 'read_timeline_restore_request', label: '查询原撤销重做请求',
    description: '仅按原UUID查询撤销或重做实际结果，不重发；404不能证明未提交，历史版本存在不代表当前恢复成功。',
    parameters: {type: 'object', properties: {request_id: {type: 'string', format: 'uuid'}}, required: ['request_id'], additionalProperties: false},
    execute: async (_id: string, params: {request_id: string}) => {
      const receipt = await request(restorePath(params.request_id));
      if (receipt.task_id !== taskId || receipt.request_id !== params.request_id) throw new Error('恢复原请求身份不一致');
      return restoreResult('query', params.request_id, receipt);
    }});

  function requireControlScope() {
    if (!taskId || !isTaskRead()) throw new Error("请先读取当前创作任务，再读取编辑控制范围");
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
    const assistantTurnId = readActionSource().turnId;
    if (!assistantTurnId || !readActionSource().userInstruction) {
      throw new Error("本轮原始要求不可核验，未提交编辑控制操作");
    }
    if (hasEditControlWrite()) throw new Error("本轮已提交一次撤销、重做或提案取消，不重复执行");
    if (hasCreationAction()) throw new Error("本轮已提交制作动作，不再修改编辑历史");
    return { assistant_turn_id: assistantTurnId,
      expected_timeline_version_id: selectedTaskVersion.timeline_version_id,
      expected_timeline_sequence: selectedTaskVersion.timeline_sequence };
  }
  function controlResult(result: unknown) {
    return { content: [{ type: "text", text: JSON.stringify({action: 'reject', receipt_returned: true,
      instruction: '这是取消当前待确认提案的实际回执，没有取消制作作业。普通回复不展示内部编号。'}) }],
      details: {kind: 'edit_control', action: 'reject', task_id: taskId, result} };
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
        instruction: "proposal_index从1开始；selection使用实际步骤index。指定原修改先读read_edit_history并传其target_operation_id；已应用复合操作内部单项逆编辑尚未实现。" }) }],
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
      if (hasPreview()) throw new Error("本轮已请求修改预览，请检查结果后再决定下一步");
      if (hasCreationAction() || hasEditControlWrite()) throw new Error("本轮已提交制作或历史控制动作，不再生成修改预览");
      const proposal = proposalAt(params.proposal_index);
      if (proposal.operation_type !== "EditCompoundTimeline") throw new Error("这项提案不支持部分选择，请查看当前提案内容");
      if (!Array.isArray(params.selection) || !params.selection.length || params.selection.length > 100
          || new Set(params.selection).size !== params.selection.length
          || params.selection.some(index => !Number.isInteger(index) || !proposal.steps.some(step => step.index === index))) {
        throw new Error("保留步骤不在当前复合提案的实际步骤中，未生成新提案");
      }
      await freshControlProposal(proposal);
      markPreview();
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
      markEditControlWrite();
      const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/${encodeURIComponent(proposal.id)}/reject`, {
        method: "POST", body: JSON.stringify(body),
      });
      return controlResult(result);
    },
  });
  for (const action of ["undo", "redo"] as const) {
    pi.registerTool({
      name: action === "undo" ? "undo_last_edit" : "redo_last_edit",
      label: action === "undo" ? "撤销原修改" : "重做原修改",
      description: `${action === "undo" ? "撤销" : "重做"}原编辑。指定较早修改先读read_edit_history并传原target_operation_id；省略才按最近历史。保留无关后续成果，复合操作内部单项逆编辑未开放。宿主绑定原UUID、任务和所见版本，结果未知先查原请求，不换ID重放。`,
      parameters: {type: 'object', properties: {target_operation_id: {type: 'string', minLength: 1}}, additionalProperties: false},
      execute: async (callId: string, params: {target_operation_id?: string} = {}) => {
        const body = controlWriteContext();
        if (params.target_operation_id) {
          if (!historicalOperations.has(params.target_operation_id)) throw new Error('请从本次读取的真实修改历史选择原操作');
        } else if (!editControls && !historyRead) throw new Error('请先读取修改历史或当前编辑控制范围');
        const requestId = typedRequestId(body.assistant_turn_id, callId);
        markEditControlWrite();
        let posted = false;
        try {
          const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/timeline/${action}`, {
            method: 'POST', body: JSON.stringify({...body, request_id: requestId, ...params})});
          posted = true;
          const receipt = result.restore_request ?? await request(restorePath(requestId));
          if (receipt.task_id !== taskId || receipt.request_id !== requestId) throw new Error('恢复回执不属于原请求，请先查询');
          return restoreResult(action, requestId, receipt);
        } catch (error) {
          if (!posted && error instanceof RequestNotSubmitted) releaseNotSubmittedEditControl();
          if (posted) error = new BusinessResultUnknown({reason:'写入已返回，但原撤销重做回执尚未核实。',
            submission_effect:'unknown',request_id:requestId,query_url:restorePath(requestId),
            impact:'写入响应已返回，读取原回执失败不能证明原操作未提交。',repair_action:'先查询原请求，不重复提交。'});
          return restoreResult(action, requestId, null, error);
        }
      },
    });
  }
}
