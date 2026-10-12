import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
const apply=vi.hoisted(()=>vi.fn());
vi.mock("../AppShell",()=>({applyTheme:apply}));
import { SettingsAppearance } from "../SettingsAppearance";
it("uses the existing saved three-state theme and writes it only on selection",async()=>{
 localStorage.setItem("ai-video-diary-theme","dark");render(<SettingsAppearance/>);
 await waitFor(()=>expect(screen.getByLabelText("界面主题")).toHaveAttribute("data-selected-value", "dark"));expect(apply).not.toHaveBeenCalled();
 await chooseSelectValue(screen.getByLabelText("界面主题"), "system");expect(localStorage.getItem("ai-video-diary-theme")).toBe("system");expect(apply).toHaveBeenCalledWith("system");
});

it("reflects shortcut changes through the existing theme dataset without a separate theme source",async()=>{
 localStorage.setItem("ai-video-diary-theme","system");render(<SettingsAppearance/>);await inspectSelectOptions(screen.getByLabelText("界面主题"),()=>expect(screen.getAllByRole("option")).toHaveLength(3));
 localStorage.setItem("ai-video-diary-theme","light");document.documentElement.dataset.themeMode="light";
 await waitFor(()=>expect(screen.getByLabelText("界面主题")).toHaveAttribute("data-selected-value", "light"));
});
