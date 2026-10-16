import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listVoiceSamplingTasks: vi.fn(), createVoiceSamplingTask: vi.fn(), getVoiceSamplingTask: vi.fn(), submitVoiceSample: vi.fn(),
  getMediaCapability: vi.fn(), getVoiceProfile: vi.fn(), listMediaPreviews: vi.fn(), createVoicePreview: vi.fn(),
}));
vi.mock("@/lib/media-profiles-api", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/media-profiles-api")>(), ...api }));
const conversion = vi.hoisted(() => vi.fn());
vi.mock("./pcm-wav", () => ({ recordedVoiceWav: conversion }));
vi.mock("@/components/DesktopVoiceRecorder", () => ({ DesktopVoiceRecorder: ({ purpose, onRecorded }: { purpose: string; onRecorded: (blob: Blob) => Promise<void> }) => <button type="button" onClick={() => void onRecorded(new Blob(["synthetic capture"], {type:"audio/webm"})).catch(() => {})}>提交{purpose}录音</button> }));
import { VoiceSamplingSetup } from "./VoiceSamplingSetup";
import { MediaProfilesApiError, type VoiceSamplingTask } from "@/lib/media-profiles-api";

const task: VoiceSamplingTask = {
  id:"sample-1", workspace_id:"w1", owner_user_id:"member-1", request_id:"create-1", purpose:"voice_profile",
  display_name:"我的声音", language:"zh", reference_text:"这是真实任务冻结的朗读文字。", text_version:"text-v1", status:"awaiting_sample",
  created_at:"2026-10-16T00:00:00Z", updated_at:"2026-10-16T00:00:00Z", received_at:null,
  source_device:null, submission_request_id:null, audio_sha256:null, duration_seconds:null, size_bytes:null,
  voice_profile_id:null, voice_version:null, last_error:null, failed_request_id:null,
};
const receipt = (request = "submit-1"): VoiceSamplingTask => ({...task,status:"received",submission_request_id:request,voice_profile_id:"voice-1",voice_version:"v1",duration_seconds:2,received_at:"2026-10-16T01:00:00Z"});

beforeEach(() => {
  vi.clearAllMocks();
  api.listVoiceSamplingTasks.mockResolvedValue({items:[],total_count:0});
  api.createVoiceSamplingTask.mockResolvedValue(task);
  api.getMediaCapability.mockResolvedValue({available:false,message:"本地模型未配置"});
  api.getVoiceProfile.mockResolvedValue({reference_url:"/api/media/voice-profiles/voice-1/reference?version=v1"});
  api.listMediaPreviews.mockResolvedValue({items:[]});
  api.submitVoiceSample.mockImplementation((_task, input) => Promise.resolve(receipt(input.request_id)));
  conversion.mockResolvedValue(new Blob(["synthetic WAV"], {type:"audio/wav"}));
});

describe("voice sampling contract (synthetic transport only)", () => {
  it("skips without creating a sample or invoking capture/model work", async () => {
    const skip = vi.fn(); render(<VoiceSamplingSetup onSkip={skip}/>);
    await screen.findByRole("button",{name:"开始声音采样"});
    fireEvent.click(screen.getByRole("button",{name:"稍后设置"}));
    expect(skip).toHaveBeenCalledOnce();
    expect(api.createVoiceSamplingTask).not.toHaveBeenCalled();
    expect(api.createVoicePreview).not.toHaveBeenCalled();
  });
  it("restores exactly the server task text/version without creating another task", async () => {
    api.listVoiceSamplingTasks.mockResolvedValue({items:[task]});
    render(<VoiceSamplingSetup onSkip={vi.fn()}/>);
    await screen.findByText(task.reference_text);
    expect(screen.getByText("文本版本：text-v1")).toBeInTheDocument();
    expect(screen.getByRole("button",{name:"提交voice-sample录音"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"用手机录制"}));
    expect(screen.getByText(/手机录制尚未接通/)).toBeInTheDocument();
    expect(api.createVoiceSamplingTask).not.toHaveBeenCalled();
  });
  it("submits explicit sample purpose and shows reception without claiming clone readiness", async () => {
    const onReceived = vi.fn(); render(<VoiceSamplingSetup onSkip={vi.fn()} onReceived={onReceived}/>);
    fireEvent.click(await screen.findByRole("button",{name:"开始声音采样"}));
    fireEvent.click(await screen.findByRole("button",{name:"提交voice-sample录音"}));
    await screen.findByText(/主机已接收 · 2.0 秒/);
    expect(api.createVoiceSamplingTask).toHaveBeenCalledWith(expect.objectContaining({purpose:"voice_profile"}));
    expect(api.submitVoiceSample).toHaveBeenCalledWith(task, expect.objectContaining({source_device:"desktop",audio:expect.any(Blob)}));
    expect(onReceived).toHaveBeenCalledOnce();
    expect(api.createVoicePreview).not.toHaveBeenCalled();
    expect(await screen.findByText(/本地模型未配置/)).toBeInTheDocument();
    expect(screen.queryByText("此版本声音已生成，可试听实际结果")).not.toBeInTheDocument();
  });
  it("queries the original request after a lost reply instead of resubmitting or generating", async () => {
    api.listVoiceSamplingTasks.mockResolvedValue({items:[task]});
    api.submitVoiceSample.mockImplementation((_task, input) => { api.getVoiceSamplingTask.mockResolvedValue(receipt(input.request_id)); return Promise.reject(new MediaProfilesApiError(0,"reply lost")); });
    render(<VoiceSamplingSetup onSkip={vi.fn()}/>);
    fireEvent.click(await screen.findByRole("button",{name:"提交voice-sample录音"}));
    await screen.findByText(/主机已接收 · 2.0 秒/);
    expect(api.getVoiceSamplingTask).toHaveBeenCalledWith(task.id);
    expect(api.submitVoiceSample).toHaveBeenCalledOnce();
    expect(api.createVoicePreview).not.toHaveBeenCalled();
  });
  it("does not promote rejected input to a saved reference", async () => {
    api.listVoiceSamplingTasks.mockResolvedValue({items:[task]});
    api.submitVoiceSample.mockRejectedValue(new MediaProfilesApiError(422,"声音样本接收不完整"));
    const onReceived = vi.fn(); render(<VoiceSamplingSetup onSkip={vi.fn()} onReceived={onReceived}/>);
    fireEvent.click(await screen.findByRole("button",{name:"提交voice-sample录音"}));
    await screen.findByRole("alert");
    expect(onReceived).not.toHaveBeenCalled();
    expect(screen.queryByText(/主机已接收 ·/)).not.toBeInTheDocument();
  });
  it("only an actual succeeded preview receipt exposes generated audio", async () => {
    api.listVoiceSamplingTasks.mockResolvedValue({items:[receipt()]});
    api.listMediaPreviews.mockResolvedValue({items:[{id:"preview-1",voice_profile_id:"voice-1",voice_version:"v1",status:"succeeded",output_url:"/api/media/previews/voice/result/output",preview_text:"实际试听文字"}]});
    render(<VoiceSamplingSetup onSkip={vi.fn()}/>);
    expect(await screen.findByLabelText("试听生成的声音")).toHaveAttribute("src","/api/media/previews/voice/result/output");
    expect(screen.getByText("本次试听文字：实际试听文字")).toBeInTheDocument();
    expect(api.createVoicePreview).not.toHaveBeenCalled();
  });
});
