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
  it("selects immutable versions and routes with identity instead of copied goals", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template, { ...template, version: 2 }]} />);
    expect(screen.getByRole("link", { name: /使用模板创作/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=2");
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    fireEvent.click(screen.getByRole("button", { name: /视频日记 · v1/ }));
    expect(screen.getByRole("link", { name: /使用模板创作/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=1");
    expect(screen.getByRole("link", { name: "查看来源任务" })).toHaveAttribute("href", "/tasks/task_1");
  });
  it("keeps built-in seeds separate from saved versions", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, origin: "built_in" }]} />);
    expect(screen.queryByRole("link", { name: /使用模板创作/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    expect(screen.getByRole("link", { name: /使用模板创作/ })).toBeInTheDocument();
  });
});
