import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Undo2 } from "lucide-react";
import { IconButton } from "../ui/icon-button";

afterEach(cleanup);
it("explains a disabled action on keyboard focus without enabling it", async () => {
  const onClick = vi.fn();
  render(<IconButton label="撤销" description="没有可撤销的修改" disabled onClick={onClick}><Undo2/></IconButton>);
  const user = userEvent.setup();
  const control = screen.getByRole("button", { name: "撤销" });
  expect(control).toBeDisabled();
  await user.tab();
  expect(await screen.findByRole("tooltip")).toHaveTextContent("没有可撤销的修改");
  await user.click(control.parentElement!);
  expect(onClick).not.toHaveBeenCalled();
});
it("preserves the actual submit action when providing hover help", async () => {
  const onSubmit = vi.fn(event => event.preventDefault());
  render(<form onSubmit={onSubmit}><IconButton label="发送" type="submit"><Undo2/></IconButton></form>);
  await userEvent.setup().click(screen.getByRole("button", { name: "发送" }));
  expect(onSubmit).toHaveBeenCalledOnce();
});
