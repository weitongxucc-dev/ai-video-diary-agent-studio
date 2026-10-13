import type {BusinessRequest, ToolRegistrar, TaskVersion, ActionSource} from './contracts.ts';

export function registerEditControls(pi: ToolRegistrar, {request, taskId, selectedTaskVersion, isTaskRead, verifyTaskVersion, readActionSource, hasPreview, markPreview, hasCreationAction, hasEditControlWrite, markEditControlWrite}: {
  request: BusinessRequest; taskId: string | undefined; selectedTaskVersion: TaskVersion | null; isTaskRead: () => boolean; verifyTaskVersion: (workspace: any) => void; readActionSource: () => ActionSource; hasPreview: () => boolean; markPreview: () => void; hasCreationAction: () => boolean; hasEditControlWrite: () => boolean; markEditControlWrite: () => void;
}) {
  type ControlStep = { index: number; command: string; enabled: boolean };
  type ControlProposal = { id: string; summary: string; operation_type: string; steps: ControlStep[] };
  type ControlContext = { task_id: string; timeline_version_id: string | null; timeline_sequence: number;
    can_undo: boolean; can_redo: boolean; proposals: ControlProposal[] };
  let editControls: ControlContext | null = null;

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
  function controlResult(action: "undo" | "redo" | "reject", result: unknown) {
    return { content: [{ type: "text", text: JSON.stringify({ action, receipt_returned: true,
      instruction: action === "reject" ? "这是取消当前待确认提案的实际回执，没有取消制作作业。普通回复不展示内部编号。"
        : "这是整个上一编辑的撤销或重做回执；包括复合编辑中的全部子项，不能称为只撤销其中调色等单项。未自动开始新的渲染。普通回复不展示内部编号。" }) }],
      details: { kind: "edit_control", action, task_id: taskId, result } };
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
        instruction: "proposal_index从1开始；selection使用实际步骤index。撤销只支持整个上一编辑；已应用复合编辑中的单项逆编辑尚未实现。" }) }],
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
      return controlResult("reject", result);
    },
  });
  for (const action of ["undo", "redo"] as const) {
    pi.registerTool({
      name: action === "undo" ? "undo_last_edit" : "redo_last_edit",
      label: action === "undo" ? "撤销上一修改" : "重做上一修改",
      description: `${action === "undo" ? "撤销" : "重做"}整个上一编辑，包括复合编辑全部子项。必须先读取当前编辑控制范围；不能将其用于已应用复合编辑中的单项逆编辑。模型不传任务或版本，服务器核用户本轮明确要求。每轮最多一次历史控制写入，失败不自动重试。`,
      parameters: { type: "object", properties: {}, additionalProperties: false },
      execute: async () => {
        const body = controlWriteContext();
        readControlContext();
        const current = await loadEditControls();
        if (!(action === "undo" ? current.can_undo : current.can_redo)) {
          throw new Error(action === "undo" ? "当前版本没有可撤销的上一编辑" : "当前版本没有可重做的编辑");
        }
        markEditControlWrite();
        const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/timeline/${action}`, {
          method: "POST", body: JSON.stringify(body),
        });
        return controlResult(action, result);
      },
    });
  }
}
