import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeSettings } from "@/lib/types";

const api = vi.hoisted(() => ({
  getRuntimeHealth: vi.fn(), getRuntimeSettings: vi.fn(), getSetupStatus: vi.fn(),
  getSystemStatus: vi.fn(), updateRuntimeSettings: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/DirectoryPickerButton", () => ({ DirectoryPickerButton: () => null }));
import WelcomePage from "@/app/welcome/page";

beforeEach(() => {
  vi.clearAllMocks();
  api.updateRuntimeSettings.mockResolvedValue({});
  api.getRuntimeHealth.mockResolvedValue({ items: [{ id: "deepseek", label: "对话与文字模型", status: "ready" }] });
});

describe("WelcomePage MiniMax 地址保存", () => {
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
      status: { llm: { configured: Boolean(provider), provider, base_url: address, model: "MiniMax-M3" } },
    } as RuntimeSettings);
    render(<WelcomePage />);
    fireEvent.click(screen.getByRole("button", { name: "开始配置 →" }));
    fireEvent.change(screen.getByLabelText("MiniMax API Key"), { target: { value: "synthetic-offline-key" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(api.updateRuntimeSettings).toHaveBeenCalledWith({
      llm_provider: "minimax", llm_base_url: expected, llm_model: "MiniMax-M3", llm_api_key: "synthetic-offline-key",
    }));
    expect(await screen.findByText("MiniMax 配置已保存；实际模型请求尚未验证。")).toBeVisible();
  });
});
