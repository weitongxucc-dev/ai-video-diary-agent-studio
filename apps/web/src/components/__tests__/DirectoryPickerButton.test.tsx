import { describe, it, expect } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";
import { DirectoryPickerButton } from "../DirectoryPickerButton";

describe("DirectoryPickerButton", () => {
  it("writes the picked folder name into the sibling text input", () => {
    const { container } = render(
      <div className="settings-field-row">
        <input name="obsidian_vault_path" type="text" />
        <DirectoryPickerButton targetName="obsidian_vault_path" />
      </div>,
    );

    const pickerInput = container.querySelector(
      'input[name="obsidian_vault_path__picker"]',
    ) as HTMLInputElement;
    expect(pickerInput).toBeTruthy();

    const file = new File([""], "MyFolder/note.md");
    Object.defineProperty(file, "webkitRelativePath", { value: "MyFolder/note.md" });
    fireEvent.change(pickerInput, { target: { files: [file] } });

    const textInput = container.querySelector(
      'input[name="obsidian_vault_path"]',
    ) as HTMLInputElement;
    expect(textInput.value).toBe("MyFolder");
    expect(screen.getByText("选择目录")).toBeInTheDocument();
  });
});
