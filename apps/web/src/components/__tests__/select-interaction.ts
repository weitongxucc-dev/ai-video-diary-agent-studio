import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect } from "vitest";

function option(value: string) {
  const item = screen.getAllByRole("option").find(item => item.getAttribute("data-app-select-value") === value);
  if (!item) throw new Error(`The open Select has no option with value ${value}`);
  return item;
}
export async function chooseSelectValue(trigger: HTMLElement, value: string) {
  const user = userEvent.setup();
  await user.click(trigger);
  await waitFor(() => expect(option(value)).toBeInTheDocument());
  await user.click(option(value));
}
export async function inspectSelectOptions(trigger: HTMLElement, check: () => void | Promise<unknown>) {
  const user = userEvent.setup();
  await user.click(trigger);
  await screen.findByRole("listbox");
  await check();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(trigger).toHaveFocus());
}
