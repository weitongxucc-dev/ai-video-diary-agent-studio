import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistantConversation } from "@/lib/types";
import { AssistantConversationPanel } from "../AssistantConversationPanel";

vi.mock("@/lib/api", () => ({ getAssistantCapabilities: vi.fn().mockRejectedValue(new Error("controlled fixture")) }));
afterEach(cleanup);
function state(running = false, loading = false) {
  const conversation: AssistantConversation = { id: "fixture", workspace_id: "fixture", scope_kind: "home", scope_id: "home", title: "新对话", draft_text: "",
    draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: running ? [{ id: "real-persisted-turn", sequence: 1, user_message: "要求",
      assistant_text: "正在回复", status: "running", events: [], result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" }] : [] };
  return { conversation, conversations: [], loading, error: null, stop: vi.fn().mockResolvedValue(undefined) } as never;
}
describe("shared compact assistant send control", () => {
  it.each([true, false])("uses an icon-only accessible send control with compact=%s", (compact) => {
    render(<AssistantConversationPanel state={state()} input="要求" onInput={vi.fn()} onSend={vi.fn()} configured busy={false} compact={compact} scopeLabel="fixture" />);
    const button = screen.getByRole("button", { name: "发送" });
    expect(button).toBeEnabled(); expect(button.textContent).toBe("");
    expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(button).toHaveAttribute("title", "Enter 发送，Shift+Enter 换行");
    button.focus(); expect(document.activeElement).toBe(button);
  });
  it.each([
    ["", true, false, false, false, "发送", "先填写对话要求"],
    ["要求", false, false, false, false, "发送", "请先配置对话服务"],
    ["要求", true, true, false, false, "发送", "正在恢复对话"],
    ["要求", true, false, true, false, "本轮运行中", "此对话已有正在运行的轮次；可先停止本轮或切换到另一段对话"],
    ["要求", true, false, false, true, "正在处理", "本轮正在处理，请稍后再发送"],
  ])("exposes the actual disabled state for %s / %s / %s / %s / %s", (input, configured, loading, running, busy, label, reason) => {
    render(<AssistantConversationPanel state={state(running, loading)} input={input} onInput={vi.fn()} onSend={vi.fn()} configured={configured} busy={busy} scopeLabel="fixture" />);
    if (running) {
      expect(screen.getByRole("button", { name: "停止本轮" })).toBeEnabled();
      expect(screen.getByRole("textbox", { name: "对话要求" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
      return;
    }
    const button = screen.getByRole("button", { name: label });
    expect(button).toBeDisabled(); expect(button).toHaveAttribute("title", reason);
    expect(screen.getByRole("textbox", { name: "对话要求" })).toHaveAccessibleDescription(expect.stringContaining(reason));
  });
  it("preserves application commands and the existing real stop callback while running", () => {
    const current = state(true) as unknown as { stop: ReturnType<typeof vi.fn> };
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={current as never} input="/help" onInput={vi.fn()} onSend={onSend} configured busy scopeLabel="fixture" />);
    const send = screen.getByRole("button", { name: "发送" });
    expect(send).toBeDisabled();
    fireEvent.click(send);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });
    expect(screen.getByRole("textbox", { name: "对话要求" })).toHaveValue("/help");
    fireEvent.click(screen.getByRole("button", { name: "停止本轮" }));
    expect(current.stop).toHaveBeenCalledOnce(); expect(onSend).not.toHaveBeenCalled();
  });
  it("preserves plain Enter sending, Shift+Enter/IME editing and input focus", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<AssistantConversationPanel state={state()} input="要求" onInput={vi.fn()} onSend={onSend} configured busy={false} scopeLabel="fixture" />);
    const input = screen.getByRole("textbox", { name: "对话要求" });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.compositionStart(input); fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled(); fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" }); expect(onSend).toHaveBeenCalledWith("要求");
    await Promise.resolve(); expect(document.activeElement).toBe(input);
  });
});
