import { chooseSelectValue } from "./select-interaction";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AsrFields, FieldRow, type FieldDef } from "../SettingsFields";

const fields: FieldDef[] = [
  { name: "asr_provider", label: "转写方式", kind: "select", defaultValue: "local_whisper", options: [{ value: "local_whisper", label: "本地" }, { value: "tencentcloud", label: "腾讯云" }] },
  { name: "local_whisper_backend", label: "本地运行方式", kind: "select", defaultValue: "openai_whisper", options: [{ value: "openai_whisper", label: "Python" }, { value: "whisper_cpp", label: "原生" }] },
  { name: "local_whisper_python_path", label: "Python", kind: "file", defaultValue: "/fixture/python" },
  { name: "local_whisper_cli_path", label: "原生程序", kind: "file", defaultValue: "/fixture/whisper-cli" },
  { name: "local_whisper_model_path", label: "模型", kind: "file", defaultValue: "/fixture/model.pt" },
  { name: "local_whisper_language", label: "语言", kind: "text", defaultValue: "auto" },
  { name: "tencentcloud_secret_key", label: "云密钥", kind: "secret" },
];

describe("ASR selected mode", () => {
  it("shows only selected fields, preserves edits across modes, and submits only active configuration", async () => {
    const { container } = render(<form><AsrFields fields={fields} /></form>);
    const form = container.querySelector("form")!;
    const payload = () => new FormData(form);
    expect(screen.getByLabelText("Python")).toBeVisible();
    expect(screen.getByLabelText("原生程序")).not.toBeVisible();
    expect(screen.getByLabelText(/云密钥/)).not.toBeVisible();
    expect(payload().has("local_whisper_cli_path")).toBe(false);
    fireEvent.change(screen.getByLabelText("Python"), { target: { value: "/fixture/custom-python" } });
    await chooseSelectValue(screen.getByLabelText("本地运行方式"), "whisper_cpp");
    expect(screen.getByLabelText("Python")).not.toBeVisible();
    expect(screen.getByLabelText("原生程序")).toBeVisible();
    const modelHelp = screen.getByText(/GGML .bin/).closest("details")!;
    expect(modelHelp).not.toHaveAttribute("open");
    fireEvent.click(modelHelp.querySelector("summary")!);
    expect(screen.getByText(/GGML .bin/)).toBeVisible();
    expect(payload().has("local_whisper_python_path")).toBe(false);
    await chooseSelectValue(screen.getByLabelText("转写方式"), "tencentcloud");
    expect(screen.getByLabelText("模型")).not.toBeVisible();
    expect(screen.getByLabelText(/云密钥/)).toBeVisible();
    expect(screen.getByText(/听写时会把音频发送到腾讯云/)).toBeVisible();
    fireEvent.change(screen.getByLabelText(/云密钥/), { target: { value: "fixture-secret" } });
    expect(payload().has("local_whisper_backend")).toBe(false);
    await chooseSelectValue(screen.getByLabelText("转写方式"), "local_whisper");
    await chooseSelectValue(screen.getByLabelText("本地运行方式"), "openai_whisper");
    expect(screen.getByLabelText("Python")).toHaveValue("/fixture/custom-python");
    expect(payload().has("tencentcloud_secret_key")).toBe(false);
    await chooseSelectValue(screen.getByLabelText("转写方式"), "tencentcloud");
    expect(screen.getByLabelText(/云密钥/)).toHaveValue("fixture-secret");
  });
});

it("keeps folded field help separate from the submitted value", () => {
  const { container } = render(<form><FieldRow field={{ name: "fixture_path", label: "程序路径", kind: "file", defaultValue: "/fixture/program", helper: "原始程序运行说明。" }}/></form>);
  expect(screen.getByLabelText("程序路径")).toHaveValue("/fixture/program");
  expect(screen.getByText("原始程序运行说明。")).not.toBeVisible();
  fireEvent.click(screen.getByText("说明"));
  expect(screen.getByText("原始程序运行说明。")).toBeVisible();
  fireEvent.change(screen.getByLabelText("程序路径"), { target: { value: "/fixture/edited-program" } });
  fireEvent.click(screen.getByText("说明"));
  expect(new FormData(container.querySelector("form")!).get("fixture_path")).toBe("/fixture/edited-program");
});
