import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeSettings } from "@/lib/types";

const api = vi.hoisted(() => ({
  getRuntimeSettings: vi.fn(), getLocalModels: vi.fn(), getProductionFoundation: vi.fn(),
  getVoiceProfiles: vi.fn(), updateRuntimeSettings: vi.fn(),
  createVoiceProfile: vi.fn(), updateProductionProfile: vi.fn(), updateVoiceProfile: vi.fn(),
  redirect: vi.fn(), revalidatePath: vi.fn(),
}));
vi.mock("@/lib/api.server", () => api);
vi.mock("next/navigation", () => ({ redirect: api.redirect }));
vi.mock("next/cache", () => ({ revalidatePath: api.revalidatePath }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("@/components/DirectoryPickerButton", () => ({ DirectoryPickerButton: () => null }));
vi.mock("@/components/ModelInstallPanel", () => ({ ModelInstallPanel: () => null }));
import SettingsPage from "@/app/settings/page";

beforeEach(() => {
  vi.resetAllMocks();
  api.getLocalModels.mockResolvedValue({ models: [] });
  api.getProductionFoundation.mockResolvedValue(null);
  api.getVoiceProfiles.mockResolvedValue({ voices: [], total_count: 0 });
  api.updateRuntimeSettings.mockResolvedValue({});
});

describe("SettingsPage MiniMax 地址保存（原首次设置表单已退役）", () => {
  it.each([
    ["新配置", "", "", "https://api.minimax.cn/v1"],
    ["已有国际", "minimax", "https://api.minimax.io/v1", "https://api.minimax.io/v1"],
    ["已有自定义", "minimax", "https://example.com/v1", "https://example.com/v1"],
    ["主动从DeepSeek连接MiniMax", "deepseek", "https://api.deepseek.com", "https://api.minimax.cn/v1"],
  ])("%s", async (_name, provider, address, expected) => {
    api.getRuntimeSettings.mockResolvedValue({
      settings: [
        { name: "LLM_PROVIDER", configured: Boolean(provider), value: provider, secret: false },
        { name: "LLM_BASE_URL", configured: Boolean(address), value: address, secret: false },
      ],
      status: {
        llm: { configured: Boolean(provider), provider, base_url: address, model: "MiniMax-M3" },
        asr: { provider: "tencent", configured: false, region: "ap-shanghai", engine_model_type: "16k_zh_en", max_local_audio_mb: 0, local_whisper_configured: false, local_whisper_python_path: null, local_whisper_model_path: null },
        external_sources: { configured: false, urls: [] },
        obsidian: { configured: false, exists: false, path: null, markdown_count: null },
        product_hunt: { configured: false, api_url: "" },
        hermes_radar: { configured: false, exists: false, path: null, latest_report: null, total_count: 0, product_hunt_count: 0, watcha_count: 0 },
        video: { ffmpeg_available: false, inbox_path: "", output_path: "", inbox_exists: false, output_exists: false, pending_count: 0 },
        storage: { database_exists: false, database_path: "" },
      },
    } satisfies RuntimeSettings);
    render(await SettingsPage({ searchParams: Promise.resolve({ section: "model" }) }));
    if (provider === "deepseek") {
      fireEvent.click(screen.getByRole("button", { name: "模型服务" }));
      fireEvent.click(screen.getByRole("option", { name: "MiniMax" }));
    }
    fireEvent.change(screen.getByLabelText("模型 API Key"), { target: { value: "synthetic-offline-key" } });
    expect(screen.getByLabelText("模型服务地址")).toHaveValue(expected);
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
    await waitFor(() => expect(api.updateRuntimeSettings).toHaveBeenCalledWith({
      llm_provider: "minimax", llm_base_url: expected, llm_model: "MiniMax-M3", llm_api_key: "synthetic-offline-key",
    }));
    expect(api.updateRuntimeSettings).toHaveBeenCalledOnce();
    await waitFor(() => expect(api.revalidatePath).toHaveBeenCalledWith("/settings"));
    const destination = new URL(api.redirect.mock.calls[0][0], "http://localhost");
    expect(destination.pathname).toBe("/settings");
    expect(destination.searchParams.get("toast_message")).toBe("配置已保存");
    expect(screen.queryByText(/最近调用成功/)).not.toBeInTheDocument();
  });
});
