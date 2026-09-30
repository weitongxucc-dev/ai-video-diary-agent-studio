import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listPersonProfiles: vi.fn(), listVoiceProfiles: vi.fn(), listPronunciationEntries: vi.fn(), listAuthorizations: vi.fn(), listApprovedMaterials: vi.fn(), getMediaCapability: vi.fn(), listMediaPreviews: vi.fn(), listMediaPreviewAttempts: vi.fn(), savePersonProfile: vi.fn(), saveVoiceProfile: vi.fn(), savePronunciationEntry: vi.fn(), importVoiceReference: vi.fn(), createVoicePreview: vi.fn(), createAvatarLipsyncPreview: vi.fn(), mediaProfilesErrorMessage: vi.fn(() => "请求失败"),
}));

vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));
vi.mock("@/lib/media-profiles-api", () => api);

import { PeopleAndVoiceWorkspace } from "@/components/PeopleAndVoiceWorkspace";

const auth = { id: "auth-1", workspace_id: "local", version: "v1", rights_holder: "本人", content_types: ["voice"], usage_scopes: ["narration", "avatar_lipsync"], effective_from: "2026-08-28T00:00:00Z", expires_at: null, evidence_reference: "workspace://proof/1", status: "active" as const };
const personAuthorization = { ...auth, id: "person-auth-1", content_types: ["person_likeness", "avatar_video"], usage_scopes: ["avatar_lipsync"] };
const person = { id: "person-1", workspace_id: "local", display_name: "主讲人", version: "v1", avatar_reference: "workspace://media/person", background_capabilities: [], shot_capabilities: [], authorization: personAuthorization, current_authorization: personAuthorization, runnable: true, runnable_reason: null };
const voice = { id: "voice-1", workspace_id: "local", person_profile_id: "person-1", person_version: "v1", version: "v1", language: "zh" as const, reference_audio_path: "workspace://media/voice", reference_text: "你好", model_id: "voice-clone", authorization: auth, current_authorization: auth, runnable: true, runnable_reason: null };
const voicePreview = { id: "voice-preview-1", workspace_id: "local", preview_type: "voice" as const, identity: "voice-identity-1", attempt: 2, status: "succeeded" as const, capability_id: "voice_clone", model_id: "local", language: "zh", person_profile_id: null, person_version: null, voice_profile_id: "voice-1", voice_version: "v1", authorization_id: null, authorization_version: null, output_url: "/api/media/previews/voice/voice-identity-1/media", reason: null, impact: null, repair_action: null, started_at: "2026-08-28T03:00:00Z", finished_at: "2026-08-28T03:00:02Z" };
const failedVoicePreview = { ...voicePreview, id: "voice-preview-failed", identity: "voice-identity-failed", attempt: 1, status: "failed" as const, output_url: null, reason: "模型服务不可用", repair_action: "检查模型服务后重新尝试。", finished_at: "2026-08-28T03:00:02Z" };

function arrange() {
  api.listPersonProfiles.mockResolvedValue({ items: [person], total_count: 1 }); api.listVoiceProfiles.mockResolvedValue({ items: [voice], total_count: 1 }); api.listPronunciationEntries.mockResolvedValue({ items: [], total_count: 0 }); api.listAuthorizations.mockResolvedValue({ items: [auth], total_count: 1 }); api.listApprovedMaterials.mockResolvedValue({ assets: [{ id: "asset-1", file_name: "已批准声音.wav", duration_seconds: 3, review_status: "approved", workspace_reference: "workspace://approved/asset-1" }, { id: "asset-2", file_name: "不可引用.mp4", duration_seconds: 3, review_status: "approved", workspace_reference: null }], total_count: 2 }); api.getMediaCapability.mockResolvedValue({ available: true, model_id: "local", message: "可用", impact: null, repair_action: null }); api.listMediaPreviews.mockResolvedValue({ items: [], total_count: 0 }); api.listMediaPreviewAttempts.mockResolvedValue({ items: [], total_count: 0 }); api.createVoicePreview.mockResolvedValue(voicePreview);
}

afterEach(() => { vi.clearAllMocks(); });

describe("PeopleAndVoiceWorkspace", () => {
  it("submits fixed person authorization and only a server workspace reference", async () => {
    arrange(); api.savePersonProfile.mockResolvedValue(person); render(<PeopleAndVoiceWorkspace />);
    await screen.findByText("人物预览");
    fireEvent.click(screen.getByRole("button", { name: "新建人物" }));
    expect(screen.getByRole("option", { name: "已批准声音.wav" })).toHaveValue("workspace://approved/asset-1");
    expect(screen.queryByRole("option", { name: "不可引用.mp4" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("档案编号")).not.toBeInTheDocument();
    fireEvent.submit(screen.getByRole("button", { name: "保存人物" }).closest("form")!);
    await waitFor(() => expect(api.savePersonProfile).toHaveBeenCalled());
    expect(api.savePersonProfile.mock.calls[0]?.[0]).toMatchObject({ id: "person-1" });
    expect(api.savePersonProfile.mock.calls[0]?.[0].authorization).toMatchObject({ content_types: ["person_likeness", "avatar_video"], usage_scopes: ["avatar_lipsync"] });
  });

  it("shows authorization and model details in ordinary Chinese without raw references", async () => {
    arrange(); render(<PeopleAndVoiceWorkspace />);
    await screen.findByText("人物预览");
    expect(screen.getAllByText("人物形象").length).toBeGreaterThan(0);
    expect(screen.getByText("人物口型")).toBeInTheDocument();
    expect(screen.getByText("已保存授权凭证")).toBeInTheDocument();
    expect(screen.getByText("人物口型模型已配置")).toBeInTheDocument();
    expect(screen.queryByText("avatar_lipsync", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("workspace://proof/1", { exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "授权记录" }));
    expect(await screen.findByText("声音 · 配音生成、人物口型 · v1")).toBeInTheDocument();
    expect(screen.queryByText("narration", { exact: true })).not.toBeInTheDocument();
  });

  it("starts a voice preview for the selected version with the schema field names", async () => {
    arrange(); render(<PeopleAndVoiceWorkspace />); await screen.findByText("人物预览");
    fireEvent.click(screen.getByRole("button", { name: "声音" }));
    await screen.findByText("声音预览");
    fireEvent.click(screen.getByRole("button", { name: "试听声音" }));
    await waitFor(() => expect(api.createVoicePreview).toHaveBeenCalledWith({ voice_profile_id: "voice-1", voice_version: "v1", preview_text: "你好", retry: false }));
  });

  it("imports WAV bytes before saving a logical voice reference", async () => {
    arrange();
    api.importVoiceReference.mockResolvedValue({ reference: "workspace://approved/voices/voice-safe.wav", file_name: "已导入声音参考.wav", size_bytes: 32044, duration_seconds: 1 });
    api.saveVoiceProfile.mockResolvedValue(voice);
    render(<PeopleAndVoiceWorkspace />);
    await screen.findByText("人物预览");
    fireEvent.click(screen.getByRole("button", { name: "声音" }));
    fireEvent.click(screen.getByRole("button", { name: "登记声音" }));
    const form = screen.getByRole("button", { name: "保存声音" }).closest("form")!;
    expect(screen.getByText("声音文件", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText(/声音声音文件/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("选择已授权 WAV"), { target: { files: [new File(["wav"], "owner.wav", { type: "audio/wav" })] } });
    await screen.findByText("WAV 已安全导入 · 1.0 秒");
    expect(screen.getByText("声音文件已保存到当前工作区；请继续填写参考文字和授权信息。")).toBeInTheDocument();
    expect(screen.queryByText(/声音声音文件/)).not.toBeInTheDocument();
    fireEvent.change(form.querySelector('input[name="version"]')!, { target: { value: "v2" } });
    fireEvent.change(form.querySelector('textarea[name="reference_text"]')!, { target: { value: "这是授权参考文字" } });
    fireEvent.change(form.querySelector('input[name="voice_rights_holder"]')!, { target: { value: "本人" } });
    fireEvent.change(form.querySelector('input[name="voice_evidence_reference"]')!, { target: { value: "workspace://proof/voice" } });
    fireEvent.submit(form);

    await waitFor(() => expect(api.saveVoiceProfile).toHaveBeenCalled());
    expect(api.importVoiceReference).toHaveBeenCalledTimes(1);
    expect(api.saveVoiceProfile.mock.calls[0]?.[0]).toMatchObject({
      reference_audio_path: "workspace://approved/voices/voice-safe.wav",
      language: "zh",
      reference_text: "这是授权参考文字",
      model_id: "local",
    });
  });

  it("uses only the selected voice run, renders its returned media URL, history, and explicit retry", async () => {
    arrange();
    const otherVoiceRun = { ...voicePreview, id: "other-run", identity: "other-identity", voice_profile_id: "voice-other", output_url: "/api/media/previews/voice/other/media" };
    api.listMediaPreviews.mockResolvedValue({ items: [otherVoiceRun, voicePreview], total_count: 2 });
    api.listMediaPreviewAttempts.mockResolvedValue({ items: [voicePreview, { ...voicePreview, id: "voice-preview-old", attempt: 1, status: "failed", output_url: null, reason: "首次失败" }], total_count: 2 });
    render(<PeopleAndVoiceWorkspace />);
    await screen.findByText("人物预览");
    fireEvent.click(screen.getByRole("button", { name: "声音" }));
    await screen.findByLabelText("声音预览");
    expect(screen.getByLabelText("声音预览")).toHaveAttribute("src", "/api/media/previews/voice/voice-identity-1/media");
    expect(await screen.findByText(/第 1 次/)).toBeInTheDocument();

    api.listMediaPreviews.mockResolvedValue({ items: [failedVoicePreview], total_count: 1 });
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await screen.findByRole("button", { name: "重新制作" });
    expect(screen.queryByLabelText("声音预览")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新制作" }));
    await waitFor(() => expect(api.createVoicePreview).toHaveBeenLastCalledWith({ voice_profile_id: "voice-1", voice_version: "v1", preview_text: "你好", retry: true }));
  });

  it("uses the selected person, voice, authorization and succeeded voice identity for lip sync", async () => {
    arrange();
    const avatarRun = { ...voicePreview, id: "avatar-run", preview_type: "avatar_lipsync" as const, identity: "avatar-identity", capability_id: "avatar_lipsync", model_id: "avatar-model", person_profile_id: "person-1", person_version: "v1", authorization_id: "person-auth-1", authorization_version: "v1", output_url: "/api/media/previews/avatar-lipsync/avatar-identity/media" };
    api.listMediaPreviews.mockResolvedValue({ items: [voicePreview], total_count: 1 });
    api.createAvatarLipsyncPreview.mockResolvedValue(avatarRun);
    render(<PeopleAndVoiceWorkspace />);
    await screen.findByText("人物预览");
    fireEvent.click(screen.getByRole("button", { name: "试做口型" }));
    await waitFor(() => expect(api.createAvatarLipsyncPreview).toHaveBeenCalledWith({ person_profile_id: "person-1", person_version: "v1", voice_profile_id: "voice-1", voice_version: "v1", voice_preview_identity: "voice-identity-1", motion: "natural", expression: "neutral", authorization_id: "person-auth-1", authorization_version: "v1", model_id: "local", retry: false }));
    expect(await screen.findByLabelText("人物口型预览")).toHaveAttribute("src", "/api/media/previews/avatar-lipsync/avatar-identity/media");
  });

  it("labels a revoked authorization without pretending the profile is runnable", async () => {
    arrange();
    const revoked = { ...auth, status: "revoked" as const };
    api.listPersonProfiles.mockResolvedValue({ items: [{ ...person, runnable: false, runnable_reason: "授权已撤销", current_authorization: revoked }], total_count: 1 });
    render(<PeopleAndVoiceWorkspace />);
    await screen.findByText("授权已撤销");
    expect(screen.getByText("授权已撤销")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "试做口型" })).toBeDisabled();
  });
});
