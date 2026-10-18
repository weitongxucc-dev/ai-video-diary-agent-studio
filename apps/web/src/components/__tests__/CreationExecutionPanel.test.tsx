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
