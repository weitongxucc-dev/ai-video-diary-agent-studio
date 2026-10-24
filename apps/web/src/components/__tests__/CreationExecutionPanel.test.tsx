import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CreationExecutionPreview, CreationExecutionStatus } from "@/lib/types";
import { CreationExecutionPanel } from "../creation-task/CreationExecutionPanel";

const waitingReason = "故事板仍有镜头等待确认；确认后点击继续制作。";
type JobStatus = NonNullable<CreationExecutionStatus["job"]>["status"];

function execution(status: JobStatus, failureReason: string): CreationExecutionStatus {
  return {
    task_id: "task-1",
    job: {
      id: "job-1", kind: "creation_task_execution", status, progress: 66,
      failure_reason: failureReason, impact: null, repair_action: null,
    },
    stages: [], current_stage: "montage_plan",
    waiting_reason: status === "queued" ? waitingReason : null,
    status_updated_at: "2026-10-18T06:57:13Z",
    video_ready: false, cover_status: "pending", cover_message: null,
  };
}

function panel(compact: boolean, status: JobStatus, failureReason: string) {
  return <CreationExecutionPanel taskStage="narration" taskReason={null}
    voice={null} availableVoices={[]} preview={null} execution={execution(status, failureReason)}
    busy={false} compact={compact} onStart={vi.fn()} onResume={vi.fn()}
    onCancel={vi.fn()} onOpenMontage={vi.fn()} />;
}

describe.each([false, true])("CreationExecutionPanel compact=%s", compact => {
  it("shows storyboard confirmation without reporting narration failure from the old task stage", async () => {
    render(panel(compact, "queued", "waiting_for_user"));
    const visible = screen.getByRole("region", { name: compact ? "制作状态" : "连续制作" });
    expect(within(visible).getByText(waitingReason, { selector: "p" })).toBeVisible();
    expect(screen.queryByText("配音没有完成")).not.toBeInTheDocument();
    expect(screen.queryByText("原始原因：waiting_for_user")).not.toBeInTheDocument();

    if (compact) {
      await userEvent.click(screen.getByRole("button", { name: "查看制作详情" }));
      const details = await screen.findByRole("dialog", { name: "制作文案与要求" });
      expect(within(details).getByText(waitingReason, { selector: "p" })).toBeVisible();
      expect(within(details).queryByText("配音没有完成")).not.toBeInTheDocument();
      expect(within(details).queryByText("原始原因：waiting_for_user")).not.toBeInTheDocument();
    }
  });

  it.each(["failed", "interrupted", "cancelled"] as const)("keeps the actual %s failure reason", async status => {
    render(panel(compact, status, "narration_source_unavailable"));
    const visible = screen.getByRole("region", { name: compact ? "制作状态" : "连续制作" });
    expect(within(visible).getByText("配音没有完成")).toBeVisible();
    if (compact) {
      await userEvent.click(screen.getByRole("button", { name: "查看制作详情" }));
      const details = await screen.findByRole("dialog", { name: "制作文案与要求" });
      expect(within(details).getByText("配音没有完成")).toBeVisible();
      expect(within(details).getByText("原始原因：narration_source_unavailable")).toBeInTheDocument();
    } else {
      expect(screen.getByText("原始原因：narration_source_unavailable")).toBeInTheDocument();
    }
  });

  it("resumes only the original user-waiting job after confirmation and an allowed preview", async () => {
    const onResume = vi.fn();
    const onOpenMontage = vi.fn();
    const waiting = execution("queued", "waiting_for_user");
    const preview: CreationExecutionPreview = {
      task_id: "task-1", script_id: "script-1", script_title: "已确认文案", script_sections: [],
      plan_id: "plan-1", narration_source: { source_kind: "generated", source_artifact_ref: "voice-1", duration_seconds: 25.2 },
      requirements: { aspect_ratio: "16:9", duration_target_seconds: 30, presenter_share_target: 0,
        lipsync_quality: "fast", selected_voice: "voice-1", can_continue: true,
        continuation_blocker: null, template_settings: {} },
      input_digest: "digest-1",
    };
    const view = (status: CreationExecutionStatus, currentPreview: CreationExecutionPreview | null) =>
      <CreationExecutionPanel taskStage="montage" taskReason={null} voice={null} availableVoices={[]}
        preview={currentPreview} execution={status} busy={false} compact={compact}
        onStart={vi.fn()} onResume={onResume} onCancel={vi.fn()} onOpenMontage={onOpenMontage} />;
    const { rerender } = render(view(waiting, preview));
    const resumeName = compact ? "继续制作" : "确认以上文案和要求，继续制作";
    expect(screen.queryByRole("button", { name: resumeName })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "审核镜头" }));
    expect(onOpenMontage).toHaveBeenCalledOnce();
    expect(onResume).not.toHaveBeenCalled();

    const ready = { ...waiting, current_stage: null, waiting_reason: null };
    rerender(view(ready, null));
    expect(screen.queryByRole("button", { name: resumeName })).not.toBeInTheDocument();
    rerender(view(ready, { ...preview, requirements: { ...preview.requirements,
      can_continue: false, continuation_blocker: "请先选择已授权声音。" } }));
    if (!compact) expect(screen.getByText("请先选择已授权声音。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: resumeName })).not.toBeInTheDocument();

    rerender(view(ready, preview));
    expect(onResume).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: resumeName }));
    expect(onResume).toHaveBeenCalledOnce();
    if (compact) {
      await userEvent.click(screen.getByRole("button", { name: "查看制作详情" }));
      const details = await screen.findByRole("dialog", { name: "制作文案与要求" });
      await userEvent.click(within(details).getByRole("button", { name: "确认以上文案和要求，继续制作" }));
      expect(onResume).toHaveBeenCalledTimes(2);
      await userEvent.click(within(details).getByRole("button", { name: "收起制作详情" }));
    }

    rerender(view(execution("queued", "resource_busy"), preview));
    expect(screen.queryByRole("button", { name: resumeName })).not.toBeInTheDocument();
  });
});

function visualDecision(adopted = false): Extract<import('@/lib/types/creation-tasks').SegmentVisualDecision, { decision_digest: string }> {
  return { task_id: 'task-1', job_id: 'job-1', section_id: 'section-1', decision_digest: 'a'.repeat(64),
    status: adopted ? 'adopted' : 'waiting_for_user', adopted, audio: { persisted: true, artifact_reference: 'narration://original' },
    usage: { settled: true, used_seconds: 1, used_output_bytes: 24000 },
    candidate_clips: [{ id: 'clip-original', label: '实际段落的原画面', preview_url: '/api/production/materials/original/media#t=0,2',
      needs_confirmation: true, source_start_seconds: 0, source_end_seconds: 2, timeline_start_seconds: 0, timeline_end_seconds: 2 }],
    candidate_total_count: 1, candidate_offset: 0, next_offset: null, required_confirmation_ids: ['clip-original'],
    confirmed_clip_ids: adopted ? ['clip-original'] : [], device_request_id: adopted ? 'video-request-original' : null };
}

it('shows concrete candidates and requires a visible choice before confirming the original job', async () => {
  const user = userEvent.setup();
  const adopt = vi.fn(); const resume = vi.fn(); const read = vi.fn();
  const state = execution('queued', 'waiting_for_visual_decision');
  state.visual_decisions = { items: [{ job_id: 'job-1', section_id: 'section-1', section_title: '第一段', adopted: false,
    phase: 'segment_video', decision_digest: 'a'.repeat(64), detail_url: '/original/decision' }], total_count: 1, truncated: false };
  const props = { taskStage: 'montage' as const, taskReason: null, voice: null, availableVoices: [], preview: null,
    execution: state, busy: false, onStart: vi.fn(), onResume: resume, onCancel: vi.fn(), onOpenMontage: vi.fn(),
    visualDecision: visualDecision(), onAdoptVisualDecision: adopt, onReadVisualDecision: read, onCloseVisualDecision: vi.fn() };
  const { rerender } = render(<CreationExecutionPanel {...props} />);
  const confirm = screen.getByRole('button', { name: '确认画面并继续原作业' });
  expect(confirm).toBeDisabled();
  expect(screen.getByText('实际段落的原画面')).toBeVisible();
  expect(screen.getByLabelText('画面 1 预览')).toHaveAttribute('src', '/api/production/materials/original/media#t=0,2');
  expect(adopt).not.toHaveBeenCalled();
  await user.click(screen.getByRole('checkbox', { name: '明确采用画面 1' }));
  await user.click(confirm);
  expect(adopt).toHaveBeenCalledWith(['clip-original']);
  expect(resume).not.toHaveBeenCalled();
  rerender(<CreationExecutionPanel {...props} visualDecision={visualDecision(true)} />);
  expect(screen.getByRole('button', { name: '确认画面并继续原作业' })).toBeDisabled();
  expect(screen.getByText('画面采用已保存；沿原作业查看排队与完成状态。')).toBeVisible();
});

it('keeps stale decisions non-adoptable and exposes only original-receipt readback after an unknown queue result', async () => {
  const user = userEvent.setup(); const adopt = vi.fn(); const read = vi.fn();
  const decision = { ...visualDecision(), status: 'stale' as const };
  render(<CreationExecutionPanel taskStage="montage" taskReason={null} voice={null} availableVoices={[]} preview={null}
    execution={execution('queued', 'waiting_for_visual_decision')} busy={false} onStart={vi.fn()} onResume={vi.fn()}
    onCancel={vi.fn()} onOpenMontage={vi.fn()} visualDecision={decision} visualError="原作业排队状态未知，请回查原决定"
    onReadVisualDecision={read} onAdoptVisualDecision={adopt} onCloseVisualDecision={vi.fn()} />);
  expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(screen.getByRole('button', { name: '确认画面并继续原作业' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '回查原决定' }));
  expect(read).toHaveBeenCalledWith(decision);
  expect(adopt).not.toHaveBeenCalled();
});

it('continues a saved adoption only after a user action using the recorded original choices', async () => {
  const user=userEvent.setup(); const adopt=vi.fn();
  const decision={...visualDecision(true),device_request_id:null};
  render(<CreationExecutionPanel taskStage="montage" taskReason={null} voice={null} availableVoices={[]} preview={null}
    execution={execution('queued','')} busy={false} onStart={vi.fn()} onResume={vi.fn()} onCancel={vi.fn()} onOpenMontage={vi.fn()}
    visualDecision={decision} onAdoptVisualDecision={adopt} onReadVisualDecision={vi.fn()} onCloseVisualDecision={vi.fn()} />);
  expect(adopt).not.toHaveBeenCalled();
  expect(screen.getByRole('button',{name:'确认画面并继续原作业'})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:'继续已采用的原作业'}));
  expect(adopt).toHaveBeenCalledWith(['clip-original']);
});

it.each([{compact:false,status:'running'},{compact:true,status:'queued'},{compact:false,status:'interrupted'}] as const)(
  'does not expose initial-job controls for a revision ($compact, $status)', ({compact,status}) => {
    const state=execution(status,status==='queued'?'waiting_for_user':'');
    state.job!.kind='creation_task_segment_revision';
    render(<CreationExecutionPanel taskStage="montage" taskReason={null} voice={null} availableVoices={[]} preview={null}
      execution={state} busy={false} compact={compact} onStart={vi.fn()} onResume={vi.fn()} onCancel={vi.fn()} onOpenMontage={vi.fn()} />);
    expect(screen.queryByRole('button',{name:'停止制作'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:/继续制作/})).not.toBeInTheDocument();
  });

it('requires original-result readback before either visual submission button can be used again', async () => {
  const user=userEvent.setup(); const adopt=vi.fn();
  const props={taskStage:'montage' as const,taskReason:null,voice:null,availableVoices:[],preview:null,
    execution:execution('queued','waiting_for_user'),busy:false,onStart:vi.fn(),onResume:vi.fn(),onCancel:vi.fn(),onOpenMontage:vi.fn(),
    onReadVisualDecision:vi.fn(),onAdoptVisualDecision:adopt,onCloseVisualDecision:vi.fn()};
  const {rerender}=render(<CreationExecutionPanel {...props} visualDecision={visualDecision()} />);
  await user.click(screen.getByRole('checkbox'));
  rerender(<CreationExecutionPanel {...props} visualDecision={visualDecision()} visualReadRequired />);
  expect(screen.getByRole('button',{name:'确认画面并继续原作业'})).toBeDisabled();
  rerender(<CreationExecutionPanel {...props} visualDecision={{...visualDecision(true),device_request_id:null}} visualReadRequired />);
  expect(screen.getByRole('button',{name:'继续已采用的原作业'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'回查原决定'})).toBeEnabled();
  expect(adopt).not.toHaveBeenCalled();
});

it.each([false, true])("exposes explicit keep-voice storyboard action and unknown-result recovery in compact=%s", async compact => {
  const regenerate = vi.fn(), read = vi.fn();
  const props = { taskStage: "montage" as const, taskReason: null, voice: null, availableVoices: [], preview: null,
    execution: null, busy: false, compact, onStart: vi.fn(), onResume: vi.fn(), onCancel: vi.fn(), onOpenMontage: vi.fn(),
    onRegenerateStoryboard: regenerate, onReadStoryboard: read };
  const storyboard = { busy: false, canSubmit: true, refreshPending: false, requestId: null as string | null, receipt: null, error: null };
  const { rerender } = render(<CreationExecutionPanel {...props} storyboard={storyboard} />);
  if (compact) {
    expect(screen.queryByRole("button", { name: "重新安排画面，保留配音" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "核对制作要求" }));
  }
  await userEvent.click(screen.getByRole("button", { name: "重新安排画面，保留配音" }));
  expect(regenerate).toHaveBeenCalledTimes(1);
  expect(props.onStart).not.toHaveBeenCalled();
  expect(props.onResume).not.toHaveBeenCalled();
  expect(props.onCancel).not.toHaveBeenCalled();
  rerender(<CreationExecutionPanel {...props} storyboard={{ ...storyboard, canSubmit: false, requestId: "original-request" }} />);
  expect(screen.getByRole("button", { name: "重新安排画面，保留配音" })).toBeDisabled();
  expect(screen.getByText("上次操作结果尚未核实，请先检查结果；不会重复提交。")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "检查上次操作结果" }));
  expect(read).toHaveBeenCalledTimes(1);
  rerender(<CreationExecutionPanel {...props} storyboard={{ ...storyboard, busy: true, canSubmit: false, requestId: "original-request" }} />);
  expect(screen.getByRole("button", { name: "检查上次操作结果" })).toBeDisabled();
  const receipt = { request_id: "original-request", task_id: "task-a", job_id: "storyboard-job", status: "succeeded" as const,
    stage: "montage_plan" as const, script_id: "script-a", narration_cache_key: "original-voice", base_timeline_version_id: "version-1",
    base_timeline_sequence: 1, result: null, failure_reason: null, result_url: "/original-result" };
  rerender(<CreationExecutionPanel {...props} storyboard={{ ...storyboard, requestId: "original-request", receipt }} />);
  expect(screen.getByText("画面编排已更新，请检查候选后重新输出。")).toBeVisible();
  expect(props.onResume).not.toHaveBeenCalled();
  expect(props.onStart).not.toHaveBeenCalled();
});

it("shows a real planning conflict and preserved audio without candidate selection or adoption", async () => {
  const read = vi.fn(), adopt = vi.fn();
  const conflict: import('@/lib/types/creation-tasks').SegmentVisualPlanningConflict = {
    task_id: "task-a", job_id: "job-1", section_id: "section-1", status: "planning_conflict", can_adopt: false,
    reason: "当前合法素材无法满足目标时长", decision_digest: null, adopted: false,
    audio: { artifact_reference: "narration:original-audio", persisted: true },
    usage: { settled: true, used_seconds: 4, used_output_bytes: 1024 },
    candidate_clips: [], candidate_total_count: 0, candidate_offset: 0, next_offset: null,
    required_confirmation_ids: [], confirmed_clip_ids: [], device_request_id: null,
    recovery: { action: "resume_original", requires_revalidation: true, detail: "请处理时长或合法素材冲突后恢复原请求。" },
  };
  const props = {taskStage:"montage" as const,taskReason:null,voice:null,availableVoices:[],preview:null,execution:null,busy:false,onReadVisualDecision:read,onAdoptVisualDecision:adopt,onStart:vi.fn(),onResume:vi.fn(),onCancel:vi.fn(),onOpenMontage:vi.fn()};
  const {rerender} = render(<CreationExecutionPanel taskStage="montage" taskReason={null} voice={null} availableVoices={[]} preview={null}
    execution={null} busy={false} visualDecision={conflict} onReadVisualDecision={read} onAdoptVisualDecision={adopt}
    onStart={vi.fn()} onResume={vi.fn()} onCancel={vi.fn()} onOpenMontage={vi.fn()} />);
  expect(screen.getByRole("alert")).toHaveTextContent(conflict.reason);
  expect(screen.getByText("原配音已保留，不会重新生成已就绪配音。")).toBeVisible();
  expect(screen.getByText("当前没有可采用的画面候选，请先处理以上冲突。")).toBeVisible();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "确认画面并继续原作业" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "查看更多原候选" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "回查原决定" }));
  expect(read).toHaveBeenCalledWith(conflict);
  expect(adopt).not.toHaveBeenCalled();
  const recovered: import('@/lib/types/creation-tasks').SegmentVisualPlanningRecovery = {
    ...conflict, status:"adopted",adopted:true,reason:"合法画面已准备，原请求继续执行",
  };
  rerender(<CreationExecutionPanel {...props} visualDecision={recovered}/>);
  expect(screen.getByRole("dialog", {name:"原画面准备记录"})).toBeVisible();
  expect(screen.getByText(recovered.reason)).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name:"确认画面并继续原作业"})).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name:"继续已采用的原作业"})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", {name:"回查原决定"}));
  expect(read).toHaveBeenLastCalledWith(recovered);
  expect(adopt).not.toHaveBeenCalled();
});
