import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";
import { DirectoryPickerButton } from "../DirectoryPickerButton";

const BROWSER_HINT = "桌面端可选择目录；浏览器中请手动输入绝对路径。";

function mockOpenDirectory(path: string | null) {
  const openDirectory = vi.fn().mockResolvedValue(path);
  window.electronAPI = { openDirectory };
  return openDirectory;
}

describe("DirectoryPickerButton", () => {
  afterEach(() => {
    delete window.electronAPI;
    vi.restoreAllMocks();
  });

  it("writes the picked absolute path into a controlled form via onPick", async () => {
    mockOpenDirectory("/Users/me/ObsidianVault");
    const onPick = vi.fn();
    render(<DirectoryPickerButton onPick={onPick} />);

    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));

    await waitFor(() =>
      expect(onPick).toHaveBeenCalledWith("/Users/me/ObsidianVault"),
    );
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(BROWSER_HINT)).not.toBeInTheDocument();
  });

  it("writes the picked absolute path into the sibling input in uncontrolled mode", async () => {
    mockOpenDirectory("/Users/me/ObsidianVault");
    const { container } = render(
      <div className="settings-field-row">
        <input name="obsidian_vault_path" type="text" />
        <DirectoryPickerButton targetName="obsidian_vault_path" />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));

    const textInput = container.querySelector(
      'input[name="obsidian_vault_path"]',
    ) as HTMLInputElement;
    await waitFor(() =>
      expect(textInput.value).toBe("/Users/me/ObsidianVault"),
    );
  });

  it("writes nothing when the native dialog is canceled", async () => {
    mockOpenDirectory(null);
    const onPick = vi.fn();
    const { container } = render(
      <div className="settings-field-row">
        <input name="obsidian_vault_path" type="text" />
        <DirectoryPickerButton targetName="obsidian_vault_path" />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));

    await waitFor(() =>
      expect(window.electronAPI?.openDirectory).toHaveBeenCalled(),
    );
    expect(onPick).not.toHaveBeenCalled();
    const textInput = container.querySelector(
      'input[name="obsidian_vault_path"]',
    ) as HTMLInputElement;
    expect(textInput.value).toBe("");
  });

  it("shows a non-blocking hint in plain browsers and never fakes a basename", async () => {
    delete window.electronAPI;
    const onPick = vi.fn();
    render(<DirectoryPickerButton onPick={onPick} />);

    expect(await screen.findByText(BROWSER_HINT)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));
    expect(onPick).not.toHaveBeenCalled();
  });

  it("keeps manual input untouched in uncontrolled browser mode", async () => {
    delete window.electronAPI;
    const { container } = render(
      <div className="settings-field-row">
        <input name="obsidian_vault_path" type="text" />
        <DirectoryPickerButton targetName="obsidian_vault_path" />
      </div>,
    );

    expect(await screen.findByText(BROWSER_HINT)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));
    const textInput = container.querySelector(
      'input[name="obsidian_vault_path"]',
    ) as HTMLInputElement;
    expect(textInput.value).toBe("");
  });

  it("uses the desktop WAV picker for a voice reference", async () => {
    const openWavFile = vi.fn().mockResolvedValue("/Volumes/voices/alice.wav");
    window.electronAPI = { openWavFile };
    const onPick = vi.fn();
    render(<DirectoryPickerButton kind="wav" onPick={onPick} />);

    fireEvent.click(screen.getByRole("button", { name: "选择 WAV" }));

    await waitFor(() => expect(onPick).toHaveBeenCalledWith("/Volumes/voices/alice.wav"));
    expect(openWavFile).toHaveBeenCalledTimes(1);
  });
});
