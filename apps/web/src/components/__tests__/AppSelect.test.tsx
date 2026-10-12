import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { AppSelect, AppSelectOption } from "../AppSelect";
import { HomeCardDialog } from "../HomeCardDialog";
import { chooseSelectValue } from "./select-interaction";

it("shows the real initial label before opening and submits the selected field value", async () => {
 const {container}=render(<form><label>运行方式<AppSelect name="engine" defaultValue="local"><AppSelectOption value="local">本地模型</AppSelectOption><AppSelectOption value="cloud">已配置云服务</AppSelectOption></AppSelect></label></form>);
 const trigger=screen.getByRole("combobox",{name:"运行方式"});
 expect(trigger).toHaveTextContent("本地模型");
 expect(new FormData(container.querySelector("form")!).get("engine")).toBe("local");
 await chooseSelectValue(trigger,"cloud");
 expect(trigger).toHaveTextContent("已配置云服务");
 expect(new FormData(container.querySelector("form")!).get("engine")).toBe("cloud");
 expect(container.querySelector("select")).toBeNull();
});
it("keeps empty choices, disabled items and disabled form fields truthful", async()=>{
 const onValueChange=vi.fn();const {container}=render(<form><AppSelect aria-label="版本" defaultValue="" name="version" onValueChange={onValueChange}><AppSelectOption value="">请选择</AppSelectOption><AppSelectOption value="old" disabled>不可用的版本</AppSelectOption><AppSelectOption value="v1">版本一</AppSelectOption></AppSelect><AppSelect name="disabled" disabled defaultValue="v1"><AppSelectOption value="v1">版本一</AppSelectOption></AppSelect></form>);
 expect(screen.getByRole("combobox",{name:"版本"})).toHaveTextContent("请选择");
 expect(new FormData(container.querySelector("form")!).has("disabled")).toBe(false);
 const user=userEvent.setup();await user.click(screen.getByRole("combobox",{name:"版本"}));
 expect(screen.getByRole("option",{name:"不可用的版本"})).toHaveAttribute("aria-disabled","true");
 await user.click(screen.getByRole("option",{name:"不可用的版本"}));expect(onValueChange).not.toHaveBeenCalled();
 await user.click(screen.getByRole("option",{name:"版本一"}));expect(onValueChange).toHaveBeenCalledWith("v1");
});
it("uses mature arrow, Home/End, typeahead and Escape focus handling",async()=>{
 render(<AppSelect aria-label="语言" defaultValue="zh"><AppSelectOption value="zh">Chinese</AppSelectOption><AppSelectOption value="skip" disabled>Disabled</AppSelectOption><AppSelectOption value="en">English</AppSelectOption><AppSelectOption value="fr">French</AppSelectOption></AppSelect>);
 const user=userEvent.setup();const trigger=screen.getByRole("combobox",{name:"语言"});trigger.focus();
 await user.keyboard("{ArrowDown}");await screen.findByRole("listbox");
 await user.keyboard("{End}");await waitFor(()=>expect(screen.getByRole("option",{name:"French"})).toHaveAttribute("data-highlighted"));
 await user.keyboard("{Home}");await waitFor(()=>expect(screen.getByRole("option",{name:"Chinese"})).toHaveAttribute("data-highlighted"));
 await user.keyboard("{ArrowDown}");await waitFor(()=>expect(screen.getByRole("option",{name:"Disabled"})).toHaveAttribute("data-highlighted"));await user.keyboard("{Enter}");expect(trigger).toHaveTextContent("Chinese");
 await user.keyboard("{ArrowDown}");await waitFor(()=>expect(screen.getByRole("option",{name:"English"})).toHaveAttribute("data-highlighted"));
 await user.keyboard("f");await waitFor(()=>expect(screen.getByRole("option",{name:"French"})).toHaveAttribute("data-highlighted"));
 await user.keyboard("{Enter}");expect(trigger).toHaveTextContent("French");await waitFor(()=>expect(trigger).toHaveFocus());
 await user.keyboard("{ArrowDown}");await screen.findByRole("listbox");await user.keyboard("{Escape}");await waitFor(()=>expect(trigger).toHaveFocus());expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
it("portals within the owning card, closes Select before the card and restores trigger focus",async()=>{
 const close=vi.fn();function Card(){const[value,setValue]=useState("one");const[open,setOpen]=useState(true);return open ? <HomeCardDialog title="设置" kind="config" onClose={()=>{close();setOpen(false);}}><AppSelect aria-label="选择" value={value} onValueChange={setValue}><AppSelectOption value="one">一</AppSelectOption><AppSelectOption value="two">二</AppSelectOption></AppSelect></HomeCardDialog> : null;}
 render(<Card/>);const user=userEvent.setup();const trigger=screen.getByRole("combobox",{name:"选择"});await user.click(trigger);
 expect(within(screen.getByRole("dialog")).getByRole("listbox")).toBeVisible();await user.keyboard("{Escape}");expect(close).not.toHaveBeenCalled();await waitFor(()=>expect(trigger).toHaveFocus());
 await chooseSelectValue(trigger,"two");expect(trigger).toHaveTextContent("二");expect(close).not.toHaveBeenCalled();await waitFor(()=>expect(trigger).toHaveFocus());
 await user.keyboard("{Escape}");expect(close).toHaveBeenCalledTimes(1);await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("defaults to the first enabled option when no value is supplied",()=>{
 render(<AppSelect aria-label="默认选择"><AppSelectOption value="blocked" disabled>不可选</AppSelectOption><AppSelectOption value="one">可选的一项</AppSelectOption></AppSelect>);
 expect(screen.getByRole("combobox",{name:"默认选择"})).toHaveTextContent("可选的一项");
});

it("keeps the popup within an open native dialog's top layer container",async()=>{
 render(<dialog open aria-label="原生卡片"><AppSelect aria-label="模式"><AppSelectOption value="one">一</AppSelectOption><AppSelectOption value="two">二</AppSelectOption></AppSelect></dialog>);
 const trigger=screen.getByRole("combobox",{name:"模式"});const user=userEvent.setup();await user.click(trigger);
 expect(await within(screen.getByRole("dialog",{name:"原生卡片"})).findByRole("listbox")).toBeVisible();
 await user.click(screen.getByRole("option",{name:"二"}));await waitFor(()=>expect(trigger).toHaveFocus());expect(trigger).toHaveTextContent("二");
});
