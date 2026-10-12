import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationPlanPreview, VideoTemplate } from "@/lib/types";
const mocks = vi.hoisted(() => ({ scope: vi.fn(), preview: vi.fn(), confirm: vi.fn(), assign: vi.fn(), voices: vi.fn(), people: vi.fn(), defaultVoice: vi.fn() }));
vi.mock("@/lib/api", () => ({ previewCreationPlan: mocks.preview, confirmCreationPlan: mocks.confirm, CreationPlanApiError: class extends Error {} }));
vi.mock("@/lib/media-profiles-api", () => ({ listVoiceProfiles: mocks.voices, listPersonProfiles: mocks.people, getDefaultVoice: mocks.defaultVoice }));
vi.mock("@/lib/preflight-draft", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/preflight-draft")>(), readPreflightDraftScope: mocks.scope }));
import { CreateProductionTaskForm } from "@/components/CreateProductionTaskForm";
import { loadPreflightDraft, resolvePreflightRepairContext, savePreflightDraft, type PreflightDraftFields } from "@/lib/preflight-draft";
const scope = { memberId: "local:member-a", workspaceId: "space-a" };
const id = "12345678-1234-4123-8123-123456789abc", nextId = "12345678-1234-4123-8123-123456789abd";
const template: VideoTemplate = { id: "template-fixed", version: 3, name: "60秒模板", visibility: "private", origin: "user", source_task_id: "old", source_timeline_version_id: "old-version", created_at: "2026-10-09", settings: { language: "zh-CN", aspect_ratio: "16:9", duration_target_seconds: 60, voice: null, style_instruction: "短句", fit_mode: "fit", subtitle_style: {} } };
const fields: PreflightDraftFields = { goal: "原制作目标", name: "原名称", targetCount: "2", duration: "30", language: "zh-CN", aspectRatio: "16:9", presenterShare: 40, lipsyncQuality: "fast", voicePin: { profile_id: "voice-a", version: "v2" }, personPin: { profile_id: "person-b", version: "p3" }, voiceEdited: true, template: { id: template.id, version: template.version } };
function preview(overrides: Partial<CreationPlanPreview> = {}): CreationPlanPreview {
  return { goal: fields.goal, name: fields.name, target_count: 2, language: "zh-CN", aspect_ratio: "16:9", duration_target_seconds: 30, knowledge_references: [], source_references: [], selected_voice: null, pronunciation_references: [], approved_asset_count: 0, approved_clip_count: 0, material_rule_summary: "真实预检返回", material_matches: [], model_snapshot: [], confirmation_points: [], blockers: [], confirmable: true, preflight_fingerprint: "n".repeat(64), ...overrides };
}
function check() { fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!); }
function defer<T>() { let resolve!: (value: T) => void; let reject!: (cause: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); mocks.scope.mockResolvedValue(scope);
  mocks.voices.mockResolvedValue({ items: [{ id: "voice-a", version: "v2", display_name: "所选声音" }] });
  mocks.people.mockResolvedValue({ items: [{ id: "person-b", version: "p3", display_name: "独立人物" }] }); mocks.defaultVoice.mockResolvedValue(null);
  mocks.preview.mockResolvedValue(preview()); mocks.confirm.mockResolvedValue({ creation_task: { id: "real-task" } });
});
describe("uncreated task repair return", () => {
  it("preserves only a stable completed-reply origin through repair and sends it on plan confirmation", async () => {
    const replyOrigin = {
      conversation_id: "11111111-1111-4111-8111-111111111111",
      turn_id: "22222222-2222-4222-8222-222222222222",
      request_id: "33333333-3333-4333-8333-333333333333",
    };
    const first = render(<CreateProductionTaskForm initialGoal="任务目标" replyOrigin={replyOrigin} assignLocation={mocks.assign} />);
    check(); await screen.findByRole("button", { name: "确认并创建任务" });
    fireEvent.click(screen.getByRole("button", { name: "前往素材库补充内容" }));
    await waitFor(() => expect(mocks.assign).toHaveBeenCalled());
    const target = new URL(new URL(mocks.assign.mock.calls[0][0], window.location.origin).searchParams.get("return_to")!, window.location.origin);
    const draftId = target.searchParams.get("draft_id")!;
    expect(loadPreflightDraft(sessionStorage, scope, draftId)?.replyOrigin).toEqual(replyOrigin);
    expect(mocks.assign.mock.calls[0][0]).not.toContain("回复正文");
    first.unmount();
    render(<CreateProductionTaskForm draftId={draftId} assignLocation={mocks.assign} />);
    await screen.findByRole("button", { name: "确认并创建任务" });
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ reply_origin: replyOrigin })));
  });
  it("saves before the real resource navigation, restores every input and checks fresh facts without auto-creation", async () => {
    mocks.preview.mockResolvedValueOnce(preview({ confirmable: false, preflight_fingerprint: "o".repeat(64), blockers: [{ reason: "content_sources_missing", stage: "topic", impact: "缺少资料", repair_action: "补充资料", blocks_confirmation: true }] }));
    const first = render(<CreateProductionTaskForm template={template} initialGoal={fields.goal} assignLocation={mocks.assign} />);
    await inspectSelectOptions(screen.getByLabelText("任务声音"),()=>screen.findByRole("option", { name: "所选声音" }));
    fireEvent.change(screen.getByLabelText("任务名称（可选）"), { target: { value: fields.name } });
    fireEvent.change(screen.getByLabelText("成片数量"), { target: { value: fields.targetCount } });
    fireEvent.change(screen.getByLabelText("目标时长（秒）"), { target: { value: fields.duration } });
    await chooseSelectValue(screen.getByLabelText("任务声音"), "voice-a:v2");
    await chooseSelectValue(screen.getByLabelText("任务人物"), "person-b:p3");
    check(); await screen.findByText("缺少资料"); fireEvent.click(screen.getByRole("button", { name: "补充知识资料" }));
    await waitFor(() => expect(mocks.assign).toHaveBeenCalledTimes(1));
    const resource = new URL(mocks.assign.mock.calls[0][0], window.location.origin);
    expect(resource.pathname).toBe("/knowledge");
    const target = new URL(resource.searchParams.get("return_to")!, window.location.origin); const draftId = target.searchParams.get("draft_id")!;
    expect(target.searchParams.get("template_version")).toBe("3"); expect(target.searchParams.has("goal")).toBe(false);
    const saved = loadPreflightDraft(sessionStorage, scope, draftId)!;
    expect(saved).toEqual({ ...fields, presenterShare: 20, replyOrigin: null, knowledgePin: null });
    expect(resolvePreflightRepairContext(sessionStorage, scope, null, window.location.origin)?.target.draftId).toBe(draftId);
    expect(JSON.stringify(saved)).not.toContain("fingerprint");
    first.unmount(); expect(loadPreflightDraft(sessionStorage, scope, draftId)).toEqual(saved);
    const lateDefault = defer<{ id: string; version: string }>(); mocks.defaultVoice.mockReturnValueOnce(lateDefault.promise);
    render(<CreateProductionTaskForm template={template} draftId={draftId} assignLocation={mocks.assign} />);
    await screen.findByText("预检目标时长：30 秒");
    await act(async () => lateDefault.resolve({ id: "different-default", version: "v9" }));
    expect(screen.getByLabelText("制作目标")).toHaveValue(fields.goal); expect(screen.getByLabelText("任务名称（可选）")).toHaveValue(fields.name);
    expect(screen.getByLabelText("成片数量")).toHaveValue(2); expect(screen.getByLabelText("目标时长（秒）")).toHaveValue(30);
    expect(screen.getByLabelText("任务声音")).toHaveAttribute("data-selected-value", "voice-a:v2"); expect(screen.getByLabelText("任务人物")).toHaveAttribute("data-selected-value", "person-b:p3");
    expect(mocks.preview).toHaveBeenLastCalledWith(expect.objectContaining({ goal: fields.goal, target_count: 2, duration_target_seconds: 30, template_id: template.id, template_version: 3, selected_voice: fields.voicePin, selected_person: fields.personPin }));
    expect(mocks.confirm).not.toHaveBeenCalled();
    savePreflightDraft(sessionStorage, scope, nextId, fields);
    mocks.confirm.mockRejectedValueOnce(new Error("真实确认失败")); fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("真实确认失败"); expect(loadPreflightDraft(sessionStorage, scope, draftId)).toEqual(saved);
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    await waitFor(() => expect(mocks.assign).toHaveBeenLastCalledWith(expect.stringContaining("/tasks/real-task?")));
    expect(mocks.confirm).toHaveBeenLastCalledWith(expect.objectContaining({ preflight_fingerprint: "n".repeat(64), duration_target_seconds: 30 }));
    expect(loadPreflightDraft(sessionStorage, scope, draftId)).toBeNull(); expect(loadPreflightDraft(sessionStorage, scope, nextId)).toEqual(fields);
    expect(resolvePreflightRepairContext(sessionStorage, scope, null, window.location.origin)).toBeNull();
  });
  it.each([
    ["content_sources_missing", "topic", "管理信息源", "/sources"],
    ["template_material_matches_missing", "video_editing", "补充素材", "/library"],
    ["approved_material_clips_missing", "video_editing", "补充素材", "/library"],
    ["template_voice_unavailable", "narration", "重新选择模板版本", "/templates"],
    ["voice_reference_missing", "narration", "检查声音版本", "/profiles"],
    ["person_reference_missing", "avatar_lipsync", "检查人物参考", "/profiles"],
    ["voice_model_unavailable", "narration", "检查连接设置", "/settings"],
    ["person_render_unavailable", "avatar_lipsync", "检查本地能力", "/health"],
  ])("maps actual %s/%s to a saved bounded resource entry", async (reason, stage, label, path) => {
    mocks.preview.mockResolvedValue(preview({ blockers: [{ reason, stage, impact: "真实缺项", repair_action: "真实建议", blocks_confirmation: false }] }));
    render(<CreateProductionTaskForm initialGoal="目标" assignLocation={mocks.assign} />); check(); await screen.findByText("真实缺项");
    fireEvent.click(screen.getByRole("button", { name: label })); await waitFor(() => expect(mocks.assign).toHaveBeenCalled());
    expect(new URL(mocks.assign.mock.calls[0][0], window.location.origin).pathname).toBe(path); expect(mocks.confirm).not.toHaveBeenCalled();
  });
  it("explicit abandonment clears only its own draft without creating a task", async () => {
    savePreflightDraft(sessionStorage, scope, id, fields); savePreflightDraft(sessionStorage, scope, nextId, fields);
    render(<CreateProductionTaskForm draftId={id} template={template} assignLocation={mocks.assign} />);
    await screen.findByRole("button", { name: "确认并创建任务" });
    fireEvent.click(screen.getByRole("button", { name: "放弃本次草稿并返回列表" }));
    await waitFor(() => expect(mocks.assign).toHaveBeenCalledWith("/production"));
    expect(loadPreflightDraft(sessionStorage, scope, id)).toBeNull(); expect(loadPreflightDraft(sessionStorage, scope, nextId)).toEqual(fields); expect(mocks.confirm).not.toHaveBeenCalled();
  });
  it("keeps the draft and inputs when abandonment storage is denied", async () => {
    savePreflightDraft(sessionStorage, scope, id, fields); render(<CreateProductionTaskForm draftId={id} template={template} assignLocation={mocks.assign} />);
    await screen.findByRole("button", { name: "确认并创建任务" });
    const denied = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("denied"); });
    fireEvent.click(screen.getByRole("button", { name: "放弃本次草稿并返回列表" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("没有放弃草稿，当前输入仍保留：denied"); expect(mocks.assign).not.toHaveBeenCalled(); expect(screen.getByLabelText("目标时长（秒）")).toHaveValue(30); denied.mockRestore();
  });
  it("keeps actual creation successful and reports a failed storage cleanup in its destination toast", async () => {
    savePreflightDraft(sessionStorage, scope, id, fields); render(<CreateProductionTaskForm draftId={id} template={template} assignLocation={mocks.assign} />);
    await screen.findByRole("button", { name: "确认并创建任务" });
    const denied = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("denied"); });
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    await waitFor(() => expect(mocks.assign).toHaveBeenCalled());
    expect(new URL(mocks.assign.mock.calls[0][0], window.location.origin).searchParams.get("toast_message")).toContain("临时草稿清理失败：denied");
    expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields); expect(screen.getByRole("button", { name: "任务已创建" })).toBeDisabled(); denied.mockRestore();
  });
  it("reports storage quota nearby, keeps 30 seconds and never leaves the form", async () => {
    render(<CreateProductionTaskForm initialGoal="目标" assignLocation={mocks.assign} />);
    fireEvent.change(screen.getByLabelText("目标时长（秒）"), { target: { value: "30" } }); check(); await screen.findByRole("button", { name: "确认并创建任务" });
    const blocked = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    fireEvent.click(screen.getByRole("button", { name: "前往素材库补充内容" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前输入仍保留：quota"); expect(screen.getByLabelText("目标时长（秒）")).toHaveValue(30);
    expect(mocks.assign).not.toHaveBeenCalled(); blocked.mockRestore();
  });
  it.each([{ memberId: "local:other-member", workspaceId: "space-a" }, { ...scope, workspaceId: "space-b" }])("refuses another member/space's draft without sending its goal", async other => {
    savePreflightDraft(sessionStorage, scope, id, fields); mocks.scope.mockResolvedValue(other);
    render(<CreateProductionTaskForm draftId={id} template={template} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("没有这份临时草稿"); expect(screen.getByLabelText("制作目标")).toHaveValue(""); expect(mocks.preview).not.toHaveBeenCalled();
  });
  it("does not restore a template version mismatch", async () => {
    savePreflightDraft(sessionStorage, scope, id, fields); render(<CreateProductionTaskForm draftId={id} template={{ ...template, version: 4 }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("模板固定版本与入口不符"); expect(mocks.preview).not.toHaveBeenCalled();
  });
  it("does not show a former member's late profile list after a trusted scope switch", async () => {
    const oldProfiles = defer<{ items: { id: string; version: string; display_name: string }[] }>(); mocks.voices.mockReturnValueOnce(oldProfiles.promise);
    render(<CreateProductionTaskForm initialGoal="当前目标" />);
    await waitFor(() => expect(mocks.voices).toHaveBeenCalled());
    mocks.scope.mockResolvedValue({ memberId: "local:member-b", workspaceId: "space-b" });
    await act(async () => oldProfiles.resolve({ items: [{ id: "old-voice", version: "v1", display_name: "前成员声音" }] }));
    expect(await screen.findByRole("alert")).toHaveTextContent("成员或工作区"); await inspectSelectOptions(screen.getByLabelText("任务声音"),()=>expect(screen.queryByRole("option", { name: /前成员声音/ })).toBeNull());
  });
  it("discards a preflight when the workspace changes while the actual request is in flight", async () => {
    const late = defer<CreationPlanPreview>(); mocks.preview.mockReturnValueOnce(late.promise);
    render(<CreateProductionTaskForm initialGoal="当前目标" />); check(); await waitFor(() => expect(mocks.preview).toHaveBeenCalled());
    mocks.scope.mockResolvedValue({ ...scope, workspaceId: "space-b" }); await act(async () => late.resolve(preview()));
    expect(await screen.findByRole("alert")).toHaveTextContent("成员或工作区已切换"); expect(screen.queryByRole("button", { name: "确认并创建任务" })).toBeNull(); expect(mocks.confirm).not.toHaveBeenCalled();
  });
  it("does not overwrite edited inputs with a late draft identity reply", async () => {
    savePreflightDraft(sessionStorage, scope, id, fields); const late = defer<typeof scope>(); mocks.scope.mockReturnValue(late.promise);
    render(<CreateProductionTaskForm draftId={id} template={template} />);
    fireEvent.change(screen.getByLabelText("制作目标"), { target: { value: "最新手工目标" } });
    await act(async () => late.resolve(scope));
    expect(await screen.findByRole("alert")).toHaveTextContent("晚到的草稿没有覆盖"); expect(screen.getByLabelText("制作目标")).toHaveValue("最新手工目标"); expect(mocks.preview).not.toHaveBeenCalled();
  });
  it.each(["success", "failure"])("ignores a late %s from an adjacent draft and confirms only its fresh fingerprint", async outcome => {
    savePreflightDraft(sessionStorage, scope, id, fields); savePreflightDraft(sessionStorage, scope, nextId, { ...fields, goal: "第二目标", duration: "45" });
    const late = defer<CreationPlanPreview>(); mocks.preview.mockReturnValueOnce(late.promise).mockResolvedValueOnce(preview({ goal: "第二目标", duration_target_seconds: 45, preflight_fingerprint: "b".repeat(64) }));
    const view = render(<CreateProductionTaskForm draftId={id} template={template} assignLocation={mocks.assign} />);
    await waitFor(() => expect(mocks.preview).toHaveBeenCalledTimes(1));
    view.rerender(<CreateProductionTaskForm draftId={nextId} template={template} assignLocation={mocks.assign} />);
    await screen.findByText("预检目标时长：45 秒");
    await act(async () => { if (outcome === "success") late.resolve(preview()); else late.reject(new Error("旧草稿失败")); });
    expect(screen.getByLabelText("制作目标")).toHaveValue("第二目标"); expect(screen.queryByText("预检目标时长：30 秒")).toBeNull(); expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ goal: "第二目标", duration_target_seconds: 45, preflight_fingerprint: "b".repeat(64) })));
    expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields);
  });
  it("clears the previous draft while a new UUID is unresolved and cannot preview the former goal", async () => {
    savePreflightDraft(sessionStorage, scope, id, fields);
    const view = render(<CreateProductionTaskForm draftId={id} template={template} />);
    await screen.findByRole("button", { name: "确认并创建任务" });
    const late = defer<typeof scope>(); mocks.scope.mockReturnValueOnce(late.promise);
    view.rerender(<CreateProductionTaskForm draftId={nextId} template={template} />);
    expect(screen.getByLabelText("制作目标")).toHaveValue(""); expect(screen.getByLabelText("任务声音")).toHaveAttribute("data-selected-value", "");
    expect(screen.getByRole("button", { name: "检查可用内容" })).toBeDisabled(); check(); expect(mocks.preview).toHaveBeenCalledTimes(1);
    await act(async () => late.resolve({ memberId: "local:member-b", workspaceId: "space-b" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("没有这份临时草稿");
    expect(screen.getByLabelText("制作目标")).toHaveValue(""); expect(mocks.preview).toHaveBeenCalledTimes(1); expect(mocks.confirm).not.toHaveBeenCalled();
  });
  it("blocks stale scope before preview/confirm and does not misreport an actual success after a scope switch", async () => {
    savePreflightDraft(sessionStorage, scope, id, fields); render(<CreateProductionTaskForm draftId={id} template={template} assignLocation={mocks.assign} />);
    await screen.findByRole("button", { name: "确认并创建任务" });
    mocks.scope.mockResolvedValue({ ...scope, workspaceId: "changed" }); fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("成员或工作区已切换"); expect(mocks.confirm).not.toHaveBeenCalled();
    mocks.scope.mockResolvedValue(scope); const actual = defer<{ creation_task: { id: string } }>(); mocks.confirm.mockReturnValueOnce(actual.promise);
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" })); await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    mocks.scope.mockResolvedValue({ ...scope, workspaceId: "changed" }); await act(async () => actual.resolve({ creation_task: { id: "actual-task" } }));
    expect(screen.getByText(/任务已创建，但当前成员或工作区未重新确认/)).toBeInTheDocument(); expect(mocks.assign).not.toHaveBeenCalled();
    expect(loadPreflightDraft(sessionStorage, scope, id)).toBeNull(); expect(screen.getByRole("button", { name: "任务已创建" })).toBeDisabled();
  });
});
