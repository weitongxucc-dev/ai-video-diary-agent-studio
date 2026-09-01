import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</>,
}));

import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";

const template = {
  template_id: "video_diary",
  name: "视频日记",
  scenario: "把一次真实经历讲清楚，并给出自己的判断。",
  recommended_duration: "30-90 秒",
  topic_structure: ["真实事件", "冲突问题", "个人判断"],
  script_structure: ["开头 3 秒", "主要经历", "观点反思"],
  material_requirements: ["口播录制", "知识库笔记"],
  jianying_style: "diary_minimal",
  subtitle_style: "清晰白底字幕",
  title_rules: ["保留真实冲突"],
  cover_copy_rules: ["一句冲突标题"],
  publish_copy_rules: ["先讲真实经历"],
  default_feedback_dimensions: ["真实感"],
};

describe("TemplateLibraryWorkspace", () => {
  it("shows ordinary-language template facts without exposing implementation ids", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    expect(screen.getAllByText("视频日记").length).toBeGreaterThan(0);
    expect(screen.getByText("清晰白底字幕")).toBeInTheDocument();
    expect(screen.queryByText("video_diary", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("diary_minimal", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /用“视频日记”开始/ })).toHaveAttribute("href", expect.stringContaining("/production?new=1&goal="));
    expect(screen.getAllByRole("link", { name: /开始/ })).toHaveLength(1);
  });

  it("does not pretend built-in templates are user-saved versions", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    fireEvent.click(screen.getByRole("button", { name: /我的模板/ }));
    expect(screen.getByText("还没有可复用的视频模板")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    expect(screen.getByText("模板版本记录尚未建立")).toBeInTheDocument();
  });
});
