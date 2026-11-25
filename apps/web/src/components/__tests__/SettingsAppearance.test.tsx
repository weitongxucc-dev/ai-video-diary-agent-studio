import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { APPEARANCE_PALETTE_KEY } from "@/lib/appearance-preferences";
import { SettingsAppearance } from "../SettingsAppearance";
import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";

const applyTheme = vi.hoisted(() => vi.fn());
vi.mock("../AppShell", () => ({ applyTheme }));

const themeKey = "ai-video-diary-theme";
const root = document.documentElement;

afterEach(() => {
  localStorage.clear();
  delete root.dataset.themeMode;
  delete root.dataset.theme;
  delete root.dataset.palette;
  applyTheme.mockClear();
});

it("opens an older profile with blue-orange and preserves its existing theme", async () => {
  localStorage.setItem(themeKey, "dark");
  render(<SettingsAppearance />);

  const theme = screen.getByLabelText("界面主题");
  const palette = screen.getByLabelText("配色");
  await waitFor(() => expect(theme).toHaveAttribute("data-selected-value", "dark"));
  expect(palette).toHaveAttribute("data-selected-value", "blue-orange");
  expect(localStorage.getItem(APPEARANCE_PALETTE_KEY)).toBeNull();
  expect(applyTheme).not.toHaveBeenCalled();

  await chooseSelectValue(theme, "system");
  expect(localStorage.getItem(themeKey)).toBe("system");
  expect(applyTheme).toHaveBeenCalledWith("system");
  expect(localStorage.getItem(APPEARANCE_PALETTE_KEY)).toBeNull();
});

it("reflects changes made by the existing theme shortcut", async () => {
  localStorage.setItem(themeKey, "system");
  render(<SettingsAppearance />);
  const theme = screen.getByLabelText("界面主题");
  await inspectSelectOptions(theme, () => expect(screen.getAllByRole("option")).toHaveLength(3));

  localStorage.setItem(themeKey, "light");
  root.dataset.themeMode = "light";
  await waitFor(() => expect(theme).toHaveAttribute("data-selected-value", "light"));
});

it("applies, remembers and resets the palette independently of light and dark", async () => {
  root.dataset.theme = root.dataset.themeMode = "dark";
  localStorage.setItem(themeKey, "dark");
  const { unmount } = render(<SettingsAppearance />);
  await chooseSelectValue(screen.getByLabelText("配色"), "warm-orange");

  expect(root.dataset.palette).toBe("warm-orange");
  expect(localStorage.getItem(APPEARANCE_PALETTE_KEY)).toBe("warm-orange");
  expect(root.dataset.theme).toBe("dark");
  expect(localStorage.getItem(themeKey)).toBe("dark");
  expect(applyTheme).not.toHaveBeenCalled();
  unmount();

  render(<SettingsAppearance />);
  const palette = screen.getByLabelText("配色");
  await waitFor(() => expect(palette).toHaveAttribute("data-selected-value", "warm-orange"));
  await chooseSelectValue(screen.getByLabelText("界面主题"), "light");
  expect(localStorage.getItem(APPEARANCE_PALETTE_KEY)).toBe("warm-orange");
  expect(root.dataset.palette).toBe("warm-orange");

  fireEvent.click(screen.getByRole("button", { name: "恢复默认配色" }));
  expect(palette).toHaveAttribute("data-selected-value", "blue-orange");
  expect(root.dataset.palette).toBe("blue-orange");
  expect(localStorage.getItem(APPEARANCE_PALETTE_KEY)).toBeNull();
  expect(localStorage.getItem(themeKey)).toBe("light");
});

it("shows blue-orange for an unrecognized saved palette", () => {
  localStorage.setItem(APPEARANCE_PALETTE_KEY, "old-preview");
  render(<SettingsAppearance />);
  expect(screen.getByLabelText("配色")).toHaveAttribute("data-selected-value", "blue-orange");
});
