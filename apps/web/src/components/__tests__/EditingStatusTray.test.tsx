import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { EditingStatusTray } from "../creation-task/EditingStatusTray";

afterEach(cleanup);

function Draft() {
  const [text, setText] = useState("");
  return <input aria-label="未保存的配音说明" value={text} onChange={event => setText(event.target.value)}/>;
}

const tray = (compact: boolean) => <EditingStatusTray compact={compact} status="当前剪辑可编辑" attention={false}><Draft/></EditingStatusTray>;

describe("editing status tray", () => {
  it("preserves child identity and draft through compact and full-flow layouts", async () => {
    const user = userEvent.setup();
    const view = render(tray(false));
    const input = screen.getByRole("textbox", {name: "未保存的配音说明"});
    await user.type(input, "沿用现场原声");
    view.rerender(tray(true));
    expect(input).toBeInTheDocument();
    expect(input).not.toBeVisible();
    await user.click(screen.getByRole("button", {name: /制作与检查$/}));
    expect(screen.getByRole("textbox", {name: "未保存的配音说明"})).toBe(input);
    expect(input).toHaveValue("沿用现场原声");
    view.rerender(tray(false));
    expect(screen.getByRole("textbox", {name: "未保存的配音说明"})).toBe(input);
    expect(input).toHaveValue("沿用现场原声");
  });

  it("reveals a new failure or recovery request but respects dismissal of the same reason", async () => {
    const user = userEvent.setup();
    const failure = {message: "源文件不可读取"};
    const recovery = {requestId: "original-request", message: "保存结果尚未核实"};
    const renderTray = (reason: typeof failure | typeof recovery | null, status = "剪辑可编辑") => <EditingStatusTray compact status={status} attention={Boolean(reason)} revealReason={reason}>
      {reason ? <p role="alert">{reason.message}</p> : null}
      {reason === recovery ? <button>查询原保存结果</button> : null}
    </EditingStatusTray>;
    const view = render(renderTray(null));
    const entry = screen.getByRole("button", {name: /制作与检查$/});
    expect(entry).toHaveAttribute("aria-expanded", "false");
    view.rerender(renderTray(failure));
    expect(screen.getByRole("alert")).toHaveTextContent("源文件不可读取");
    expect(entry).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("button", {name: "收起制作与检查"}));
    expect(entry).toHaveFocus();
    view.rerender(renderTray(failure, "已读取新进度"));
    expect(entry).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    view.rerender(renderTray(recovery));
    expect(entry).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("保存结果尚未核实");
    expect(screen.getByRole("button", {name: "查询原保存结果"})).toBeVisible();
    await user.click(entry);
    view.rerender(renderTray(recovery, "已读取原请求"));
    expect(entry).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", {name: "查询原保存结果"})).not.toBeInTheDocument();
  });

  it("returns focus to the status entry when closed by button or Escape", async () => {
    const user = userEvent.setup();
    render(tray(true));
    const entry = screen.getByRole("button", {name: /制作与检查$/});
    await user.click(entry);
    await user.click(screen.getByRole("textbox", {name: "未保存的配音说明"}));
    await user.click(screen.getByRole("button", {name: "收起制作与检查"}));
    expect(entry).toHaveFocus();
    expect(entry).toHaveAttribute("aria-expanded", "false");
    await user.click(entry);
    await user.click(screen.getByRole("textbox", {name: "未保存的配音说明"}));
    await user.keyboard("{Escape}");
    expect(entry).toHaveFocus();
    expect(entry).toHaveAttribute("aria-expanded", "false");
  });
});
