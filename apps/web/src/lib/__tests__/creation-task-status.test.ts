import { describe, expect, it } from "vitest";
import { creationTaskFailureReason, creationTaskRepairAction, creationTaskShortStatus } from "../creation-task-status";

describe("task failures shown to creators", () => {
  it("names the actual stage and distinguishes waiting, running, failure and completion", () => {
    expect(creationTaskShortStatus("waiting_for_user", "script")).toBe("文案待确认");
    expect(creationTaskShortStatus("running", "narration")).toBe("正在配音");
    expect(creationTaskShortStatus("failed", "narration")).toBe("配音未完成");
    expect(creationTaskShortStatus("waiting_for_user", "review")).toBe("成片待检查");
    expect(creationTaskShortStatus("completed", "review")).toBe("成片检查已完成");
    expect(creationTaskShortStatus("needs_action", "future-stage")).toBe("任务待处理");
  });
  it.each([
    ["文字模型返回的内容不是有效 JSON。", "topic", "选题没有生成成功"],
    ["provider_output_invalid", "script", "文案没有生成成功"],
    ["narration_generation_failed", "narration", "配音没有完成"],
    ["HTTP 503: device_budget_unavailable", "montage", "视频剪辑没有完成"],
    ["ValueError: /Volumes/example/file", "review", "成片检查没有完成"],
  ])("keeps %s out of the primary task explanation", (raw, stage, label) => {
    expect(creationTaskFailureReason(raw, stage)).toBe(label);
    expect(raw).not.toBe(label);
  });

  it("keeps an actionable ordinary explanation", () => {
    expect(creationTaskFailureReason("这份资料暂时不可用，请重新选择。", "topic"))
      .toBe("这份资料暂时不可用，请重新选择。");
    expect(creationTaskFailureReason(null, "topic")).toBeNull();
  });

  it("describes the next action without making users troubleshoot JSON", () => {
    expect(creationTaskRepairAction("检查模型的 JSON 输出能力或更换兼容模型后重试。"))
      .toBe("可以重新尝试；仍未完成时，到设置检查对话服务。");
    expect(creationTaskRepairAction("文案预计时长超过创作方案上限，请先压缩并重新确认文案"))
      .toBe("缩短文案后重新确认。");
    expect(creationTaskRepairAction("重新选择声音。" )).toBe("重新选择声音。");
  });
});
