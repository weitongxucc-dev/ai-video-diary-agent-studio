import { describe, it, expect } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";
import { LlmProviderFields } from "../LlmProviderFields";

function renderFields(apiKeyConfigured: boolean) {
  return render(
    <form>
      <LlmProviderFields
        apiKeyConfigured={apiKeyConfigured}
        baseUrl="https://api.minimax.io/v1"
        model="MiniMax-M3"
        provider="minimax"
      />
    </form>,
  );
}

describe("LlmProviderFields 删除密钥", () => {
  it("保留已有国际地址，主动选择 MiniMax 才填国内默认", () => {
    renderFields(true);
    const address = screen.getByLabelText("模型服务地址");
    expect(address).toHaveValue("https://api.minimax.io/v1");
    fireEvent.change(address, { target: { value: "https://example.com/custom/v1" } });
    expect(address).toHaveValue("https://example.com/custom/v1");
    fireEvent.change(screen.getByLabelText("模型服务"), { target: { value: "deepseek" } });
    expect(address).toHaveValue("https://api.deepseek.com");
    fireEvent.change(screen.getByLabelText("模型服务"), { target: { value: "minimax" } });
    expect(address).toHaveValue("https://api.minimax.cn/v1");
    expect(screen.getByLabelText("模型")).toHaveValue("MiniMax-M3");
  });
  it("已配置时才给删除入口，且要点两次才真的带删除动作", () => {
    const form = renderFields(true).container.querySelector("form")!;

    expect(form.querySelector('[name="llm_key_action"]')).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "删除已保存的密钥" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除密钥" }));
    const action = form.querySelector<HTMLButtonElement>('[name="llm_key_action"]');
    expect(action?.getAttribute("value")).toBe("delete");
  });

  it("取消后不再带删除动作", () => {
    const form = renderFields(true).container.querySelector("form")!;

    fireEvent.click(screen.getByRole("button", { name: "删除已保存的密钥" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(form.querySelector('[name="llm_key_action"]')).toBeNull();
  });

  it("没配置密钥时不出现删除入口", () => {
    renderFields(false);

    expect(screen.queryByRole("button", { name: "删除已保存的密钥" })).toBeNull();
  });
});
