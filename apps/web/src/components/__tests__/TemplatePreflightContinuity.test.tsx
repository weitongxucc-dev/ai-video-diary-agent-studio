import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { CreationPlanPreview, VideoTemplate } from "@/lib/types";
const mocks = vi.hoisted(() => ({ raw: "", scope: vi.fn(), push: vi.fn(), preview: vi.fn(), confirm: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }), useSearchParams: () => new URLSearchParams(mocks.raw) }));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/lib/preflight-draft", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/preflight-draft")>(), readPreflightDraftScope: mocks.scope }));
vi.mock("@/lib/api", () => ({ setVideoTemplateVisibility: vi.fn(), previewCreationPlan: mocks.preview, confirmCreationPlan: mocks.confirm, CreationPlanApiError: class extends Error {} }));
vi.mock("@/lib/media-profiles-api", () => ({ listVoiceProfiles: vi.fn().mockResolvedValue({ items: [] }), listPersonProfiles: vi.fn().mockResolvedValue({ items: [] }), getDefaultVoice: vi.fn().mockResolvedValue({ id: "late-default", version: "v9" }) }));
import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";
import { CreateProductionTaskForm } from "@/components/CreateProductionTaskForm";
import { loadPreflightDraft, preflightReturnUrl, resolvePreflightRepairContext, savePreflightDraft, savePreflightRepairContext, type PreflightDraftFields } from "@/lib/preflight-draft";
const scope = { memberId: "local:member", workspaceId: "space-a" };
const id = "12345678-1234-4123-8123-123456789abc", next = "12345678-1234-4123-8123-123456789abd";
const fields: PreflightDraftFields = { goal: "原目标", name: "原名称", targetCount: "1", duration: "30", language: "zh-CN", aspectRatio: "16:9", presenterShare: 40, lipsyncQuality: "fast", voicePin: { profile_id: "voice", version: "v3" }, personPin: { profile_id: "person", version: "p2" }, voiceEdited: true, template: { id: "old", version: 1 } };
const selected: VideoTemplate = { id: "selected", version: 7, name: "新模板", visibility: "private", origin: "user", source_task_id: "old-task", source_timeline_version_id: "old-version", created_at: "2026-10-09", settings: { language: "en-US", aspect_ratio: "9:16", duration_target_seconds: 90, voice: { profile_id: "template-default", version: "v8" }, style_instruction: "短句", fit_mode: "fit", subtitle_style: {} } };
function seed() { savePreflightDraft(sessionStorage, scope, id, fields); savePreflightRepairContext(sessionStorage, scope, id, fields.template); }
beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear(); mocks.raw = ""; mocks.scope.mockResolvedValue(scope); });
it("the actual detail use action changes the same draft's fixed template and returns for a fresh 30-second preflight", async () => {
  seed(); const page = render(<TemplateLibraryWorkspace offline={false} templates={[selected]} />);
  await screen.findByRole("button", { name: "换用此模板并返回创作" }); expect(mocks.push).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
  const dialog = screen.getByRole("dialog", { name: "模板详情" });
  fireEvent.click(within(dialog).getByRole("button", { name: "换用此模板并返回创作" }));
  const template = { id: selected.id, version: selected.version };
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(preflightReturnUrl(id, template)));
  expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual({ ...fields, template });
  expect(resolvePreflightRepairContext(sessionStorage, scope, null, window.location.origin)?.target.template).toEqual(template);
  expect(mocks.preview).not.toHaveBeenCalled(); expect(mocks.confirm).not.toHaveBeenCalled(); page.unmount();
  mocks.preview.mockResolvedValue({ name: fields.name, goal: fields.goal, target_count: 1, language: "zh-CN", aspect_ratio: "16:9", duration_target_seconds: 30, knowledge_references: [], source_references: [], selected_voice: null, pronunciation_references: [], approved_asset_count: 0, approved_clip_count: 0, material_rule_summary: "真实检查", material_matches: [], model_snapshot: [], confirmation_points: [], blockers: [], confirmable: true, preflight_fingerprint: "n".repeat(64) } as CreationPlanPreview);
  // The profile APIs above remain request substitutes; no media/default is generated.
  const profile = await import("@/lib/media-profiles-api");
  vi.mocked(profile.listVoiceProfiles).mockResolvedValue({ items: [], total_count: 0 }); vi.mocked(profile.listPersonProfiles).mockResolvedValue({ items: [], total_count: 0 }); vi.mocked(profile.getDefaultVoice).mockResolvedValue({ id: "late-default", version: "v9" } as Awaited<ReturnType<typeof profile.getDefaultVoice>>);
  render(<CreateProductionTaskForm draftId={id} template={selected} />);
  await screen.findByText("预检目标时长：30 秒");
  expect(mocks.preview).toHaveBeenCalledWith(expect.objectContaining({ template_id: selected.id, template_version: 7, goal: fields.goal, name: fields.name, target_count: 1, duration_target_seconds: 30, language: "zh-CN", aspect_ratio: "16:9", presenter_share_target: 40, lipsync_quality: "fast", selected_voice: fields.voicePin, selected_person: fields.personPin }));
  expect(screen.getByLabelText("任务声音")).toHaveAttribute("data-selected-value", "voice:v3"); expect(screen.getByLabelText("任务人物")).toHaveAttribute("data-selected-value", "person:p2"); expect(mocks.confirm).not.toHaveBeenCalled();
});
it("preserves the original normal creation link when no repair context exists", async () => {
  render(<TemplateLibraryWorkspace offline={false} templates={[selected]} />);
  expect(await screen.findByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=selected&template_version=7"); expect(mocks.push).not.toHaveBeenCalled();
});
it("never turns a malformed explicit URL or an orphan marker into a new task goal", async () => {
  seed(); mocks.raw = "return_to=https%3A%2F%2Fevil.test"; const view = render(<TemplateLibraryWorkspace offline={false} templates={[selected]} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("地址无效"); expect(screen.queryByRole("link", { name: /用这个做/ })).toBeNull(); expect(mocks.push).not.toHaveBeenCalled(); view.unmount();
  mocks.raw = ""; const key = Object.keys(sessionStorage).find(key => key.includes("preflight-draft"))!; sessionStorage.removeItem(key);
  render(<TemplateLibraryWorkspace offline={false} templates={[selected]} />); expect(await screen.findByRole("alert")).toHaveTextContent("失联"); expect(screen.queryByRole("button", { name: "换用此模板并返回创作" })).toBeNull();
});
it("keeps the original draft and page on marker quota failure after the real toolbar use click", async () => {
  seed(); render(<TemplateLibraryWorkspace offline={false} templates={[selected]} />); await screen.findByRole("button", { name: "换用此模板并返回创作" });
  const set = Storage.prototype.setItem;
  const quota = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function(this: Storage, key, value) { if (key.includes("preflight-repair")) throw new Error("quota"); set.call(this, key, value); });
  fireEvent.click(screen.getByRole("button", { name: "换用此模板并返回创作" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("quota"); expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields); expect(mocks.push).not.toHaveBeenCalled(); quota.mockRestore();
  fireEvent.click(screen.getByRole("button", { name: "换用此模板并返回创作" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(preflightReturnUrl(id, { id: selected.id, version: selected.version })));
  expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual({ ...fields, template: { id: selected.id, version: selected.version } });
});
it.each(["scope", "uuid"])("refuses a changed %s before template use without touching another draft", async change => {
  seed(); render(<TemplateLibraryWorkspace offline={false} templates={[selected]} />); await screen.findByRole("button", { name: "换用此模板并返回创作" });
  if (change === "scope") mocks.scope.mockResolvedValue({ ...scope, memberId: "local:other" });
  else { savePreflightDraft(sessionStorage, scope, next, { ...fields, goal: "第二草稿" }); savePreflightRepairContext(sessionStorage, scope, next, fields.template); }
  fireEvent.click(screen.getByRole("button", { name: "换用此模板并返回创作" })); expect(await screen.findByRole("alert")).toHaveTextContent(change === "scope" ? "成员或工作区已变化" : "定位已变化"); expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields); expect(mocks.push).not.toHaveBeenCalled();
});
it("does not apply a late identity reply after leaving templates", async () => {
  seed(); const view = render(<TemplateLibraryWorkspace offline={false} templates={[selected]} />); await screen.findByRole("button", { name: "换用此模板并返回创作" });
  let resolve!: (value: typeof scope) => void; mocks.scope.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  fireEvent.click(screen.getByRole("button", { name: "换用此模板并返回创作" })); view.unmount(); await act(async () => resolve(scope)); expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields); expect(mocks.push).not.toHaveBeenCalled();
});
