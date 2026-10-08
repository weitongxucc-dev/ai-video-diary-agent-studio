import { render, screen, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeSettings } from "@/lib/types";

const api = vi.hoisted(() => ({
  getRuntimeSettings: vi.fn(),
  getLocalModels: vi.fn(),
  getProductionFoundation: vi.fn(),
  getVoiceProfiles: vi.fn(),
}));
vi.mock("@/lib/api.server", () => api);
vi.mock("@/app/actions", () => ({
  createVoiceProfileAction: vi.fn(),
  setDefaultVoiceProfileAction: vi.fn(),
  updateProductionProfileAction: vi.fn(),
  updateRuntimeSettingsAction: vi.fn(),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...props}>{children}</a>
  ),
}));
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, inspector }: { children: ReactNode; inspector?: ReactNode }) => (
    <><div>{children}</div>{inspector ? <aside aria-label="设置状态">{inspector}</aside> : null}</>
  ),
}));
vi.mock("@/components/FormErrorBanner", () => ({
  FormErrorBanner: ({ children }: { children: ReactNode }) => <form>{children}</form>,
}));
vi.mock("@/components/DirectoryPickerButton", () => ({ DirectoryPickerButton: () => null }));
vi.mock("@/components/ModelInstallPanel", () => ({ ModelInstallPanel: () => null }));

import SettingsPage from "@/app/settings/page";

/**
 * The backend shape under test is exactly what the real service returns:
 * the four-tier label arrives as the `LLM_SERVICE_STATE` row of
 * `/api/settings/runtime`, and `status.llm` stays the old two-field shape.
 */
const SECRET = "sk-synthetic-not-a-real-key-000111222333";

function runtimeWithServiceState(serviceState: string | undefined, llmConfigured = true): RuntimeSettings {
  return {
    settings: [
      { name: "LLM_PROVIDER", configured: true, value: "minimax", secret: false },
      { name: "LLM_API_KEY", configured: llmConfigured, value: llmConfigured ? "已填写" : "未填写", secret: true },
      { name: "LLM_BASE_URL", configured: true, value: "https://api.minimax.io/v1", secret: false },
      { name: "LLM_MODEL", configured: true, value: "MiniMax-M3", secret: false },
      ...(serviceState === undefined
        ? []
        : [{ name: "LLM_SERVICE_STATE", configured: true, value: serviceState, secret: false }]),
    ],
    status: {
      llm: {
        provider: "minimax",
        configured: llmConfigured,
        model: "MiniMax-M3",
        base_url: "https://api.minimax.io/v1",
      },
      obsidian: { configured: false, exists: false, path: null, markdown_count: null },
      product_hunt: { configured: false, api_url: "" },
      external_sources: { configured: false, urls: [] },
      hermes_radar: {
        configured: false, exists: false, path: null, latest_report: null,
        total_count: 0, product_hunt_count: 0, watcha_count: 0,
      },
      video: {
        inbox_path: "", output_path: "", inbox_exists: false, output_exists: false,
        pending_count: 0, ffmpeg_available: false,
      },
      storage: { database_path: "", database_exists: false },
      asr: {
        provider: "tencent", configured: false, region: "ap-shanghai",
        engine_model_type: "16k_zh_en", max_local_audio_mb: 0,
        local_whisper_configured: false, local_whisper_python_path: null,
        local_whisper_model_path: null,
      },
    },
  } as RuntimeSettings;
}

/** The page is an async server component: await it, then render the tree. */
async function renderSettings() {
  return render(await SettingsPage({}));
}

beforeEach(() => {
  api.getRuntimeSettings.mockReset();
  api.getLocalModels.mockReset();
  api.getProductionFoundation.mockReset();
  api.getVoiceProfiles.mockReset();
  api.getLocalModels.mockResolvedValue({ models: [] });
  api.getProductionFoundation.mockResolvedValue(null);
  api.getVoiceProfiles.mockResolvedValue({ voices: [], total_count: 0 });
});

describe("SettingsPage · LLM 服务状态徽章四档", () => {
  it("新配置回填国内默认，同时已有国际地址原样保留", async () => {
    const existing = runtimeWithServiceState("已填写，未验证");
    api.getRuntimeSettings.mockResolvedValue(existing);
    const view = await renderSettings();
    expect(screen.getByLabelText("模型服务地址")).toHaveValue("https://api.minimax.io/v1");
    view.unmount();
    const fresh = runtimeWithServiceState("未填写", false);
    fresh.settings = [];
    api.getRuntimeSettings.mockResolvedValue(fresh);
    await renderSettings();
    expect(screen.getByLabelText("模型服务地址")).toHaveValue("https://api.minimax.cn/v1");
  });
  it("服务真的失败时不许显示绿色「已配置」", async () => {
    // B03 回归线：后端已经记下一次真实失败，但字段仍然「填了」。
    // 用户今天看到的必须不是绿色「已配置」。
    api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState("失败"));
    await renderSettings();
    const node = screen.getByTestId("llm-service-badge");
    expect(node).toHaveTextContent("失败");
    expect(node.textContent).not.toContain("已配置");
    expect(node.className).not.toContain("is-ready");
  });

  it("已填写但没有真实观测时不许染绿", async () => {
    api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState("已填写，未验证"));
    await renderSettings();
    const node = screen.getByTestId("llm-service-badge");
    expect(node).toHaveTextContent("已配置·待验证");
    expect(node.className).not.toContain("is-ready");
    expect(node.textContent).not.toContain("可用");
  });

  it("只有真实观测到成功才显示可用并染绿", async () => {
    api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState("可用"));
    await renderSettings();
    const node = screen.getByTestId("llm-service-badge");
    expect(node).toHaveTextContent("最近调用成功");
    expect(node.className).toContain("is-ready");
  });

  it("未填写时说明缺什么，并给下一步", async () => {
    api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState("未填写", false));
    await renderSettings();
    const node = screen.getByTestId("llm-service-badge");
    expect(node).toHaveTextContent("未配置");
    const detail = screen.getByTestId("llm-service-detail");
    expect(detail).toHaveTextContent("API Key");
  });

  it("接不到后端状态线时如实说接不到，不拿 configured 硬凑四档", async () => {
    // 字段是填了的（configured=true），但没有 LLM_SERVICE_STATE 这一行。
    api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState(undefined));
    await renderSettings();
    const node = screen.getByTestId("llm-service-badge");
    expect(node).toHaveTextContent("暂时无法读取连接状态");
    expect(node.className).not.toContain("is-ready");
    expect(node.textContent).not.toContain("可用");
    expect(node.textContent).not.toContain("已填写，未验证");
  });

  it("失败时后端没给原因就不许编原因", async () => {
    api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState("失败"));
    await renderSettings();
    const detail = screen.getByTestId("llm-service-detail");
    expect(detail).toHaveTextContent("拿不到更具体的原因");
    // 不许把某个具体失败原因当成已知事实编上去
    expect(detail.textContent).not.toContain("拒绝了这把密钥");
    expect(detail.textContent).not.toContain("超时");
    expect(detail.textContent).not.toContain("地址填错");
  });

  it("展示后端安全失败原因和修复动作，不保留可用徽章", async () => {
    const runtime = runtimeWithServiceState("失败");
    runtime.settings.push(
      { name: "LLM_SERVICE_CAUSE", configured: true, value: "transport_failed", secret: false },
      { name: "LLM_SERVICE_DETAIL", configured: true, value: "文字模型连接失败。", secret: false },
      { name: "LLM_SERVICE_REPAIR_ACTION", configured: true, value: "检查网络后重试。", secret: false },
    );
    api.getRuntimeSettings.mockResolvedValue(runtime);
    await renderSettings();
    expect(screen.getByTestId("llm-service-detail")).toHaveTextContent("文字模型连接失败。");
    expect(screen.getByTestId("llm-service-detail")).toHaveTextContent("下一步：检查网络后重试。");
    expect(screen.getByTestId("llm-service-detail")).not.toHaveTextContent("拿不到更具体的原因");
    expect(screen.getByTestId("llm-service-badge").className).not.toContain("is-ready");
  });

  it("任何形态下都不许显示密钥本身", async () => {
    api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState("可用"));
    const { container } = await renderSettings();
    screen.getByTestId("llm-service-badge");
    expect(container.innerHTML).not.toContain(SECRET);
    expect(document.body.textContent ?? "").not.toContain(SECRET);
    expect(document.body.textContent ?? "").not.toMatch(/sk-[A-Za-z0-9_-]{8,}/);
  });

  it("四档文案与用户下一步的对照在各档都可读", async () => {
    for (const [state, expectedTone] of [
      ["未填写", "missing"],
      ["已填写，未验证", "unverified"],
      ["可用", "available"],
      ["失败", "failed"],
    ] as const) {
      api.getRuntimeSettings.mockResolvedValue(runtimeWithServiceState(state));
      const view = await renderSettings();
      const node = screen.getByTestId("llm-service-badge");
      expect(node.getAttribute("data-llm-service-state")).toBe(expectedTone);
      const detail = within(view.container).getByTestId("llm-service-detail");
      expect(detail.textContent?.trim().length ?? 0).toBeGreaterThan(0);
      view.unmount();
    }
  });
});
