import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { VideoTemplate } from "@/lib/types";
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));
import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";
const template: VideoTemplate = {
  id: "template_1", version: 1, name: "视频日记", origin: "user", source_task_id: "task_1", source_timeline_version_id: "timeline_1", created_at: "2026-09-12",
  settings: { language: "zh-CN", aspect_ratio: "9:16", duration_target_seconds: 30, voice: null, style_instruction: "先讲结论", script_section_count: 3, fit_mode: "fit", subtitle_style: {} },
};
describe("TemplateLibraryWorkspace", () => {
  it("shows saved color parameters and distinguishes mixed and legacy templates", () => {
    const { rerender } = render(<TemplateLibraryWorkspace offline={false} templates={[{
      ...template, settings: { ...template.settings, aspect_ratio: "16:9", color_style: { exposure: .5, contrast: 1.1, saturation: .8, temperature: .2 } },
    }]} />);
    expect(screen.getByText("曝光 0.5 · 对比度 1.1 · 饱和度 0.8 · 冷暖 0.2")).toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, settings: { ...template.settings, color_style: null } }]} />);
    expect(screen.getByText("原镜头调色不同，不统一复用")).toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    expect(screen.getByText("未保存调色，使用新素材原色")).toBeInTheDocument();
  });
  it("selects immutable versions and routes with identity instead of copied goals", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template, { ...template, version: 2 }]} />);
    expect(screen.getByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=2");
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    fireEvent.click(screen.getByRole("button", { name: /视频日记 · 第1版/ }));
    expect(screen.getByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=1");
    expect(screen.getByRole("link", { name: "查看原片" })).toHaveAttribute("href", "/tasks/task_1");
  });
  it("keeps built-in seeds separate from saved versions", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, origin: "built_in" }]} />);
    expect(screen.queryByRole("link", { name: /用这个做/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    expect(screen.getByRole("link", { name: /用这个做/ })).toBeInTheDocument();
  });
});
