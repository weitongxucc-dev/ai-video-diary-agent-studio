import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { DeliveryVoiceSample } from "../creation-task/DeliveryVoiceSample";
import { BilingualDeliveryControls } from "../creation-task/BilingualDeliveryControls";
import { ScriptEditor } from "../creation-task/ScriptStage";
import type { CreationTaskScript } from "@/lib/types";
import { getCreationTaskScriptEditSources, getCreationTaskScriptSections } from "@/lib/api/creation-tasks";

const api = vi.hoisted(() => ({
  create: vi.fn(), attempts: vi.fn(), profiles: vi.fn(), voice: vi.fn(), dictionary: vi.fn(),
  resources: vi.fn(), prepare: vi.fn(), read: vi.fn(), finalize: vi.fn(),
}));
vi.mock("@/lib/media-profiles-api", () => ({ createVoicePreview: api.create, listMediaPreviewAttempts: api.attempts, listVoiceProfiles:api.profiles, mediaProfilesErrorMessage: (e: Error) => e.message,
  getVoiceProfile: api.voice, listPronunciationEntries: api.dictionary,
}));
vi.mock("@/lib/api/device-preparation", () => ({
  getDeviceResourceStatus: api.resources, prepareNarrationResources: api.prepare,
  getDevicePreparation: api.read, finalizeDevicePreparation: api.finalize,
}));

vi.mock("@/lib/api/creation-tasks", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api/creation-tasks")>(),
  getCreationTaskScriptEditSources: vi.fn(),
  getCreationTaskScriptSections: vi.fn(),
}));

const configured = {
  platform: "darwin", actor_scope: "a".repeat(64), preparation_configured: true,
  profile_status: "available", available_operations: ["narration"],
};
const needsPreparation = "实际声音、来源、参考文案、语言或发音词典与已准备形状不一致，请按真实输入重新准备";
const receipt = { id: "work-id", plan_id: "plan-id", state: "waiting", wait_reason: null };
const done = { ...receipt, state: "done" };
const activated = { ...done, profile_published: true, normal_work_started: false };

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  api.resources.mockResolvedValue(configured);
  api.voice.mockResolvedValue({ id: "studio", version: "v3", person_profile_id: "host", workspace_id: "local" });
  api.dictionary.mockResolvedValue({ items: [] });
  vi.mocked(getCreationTaskScriptEditSources).mockResolvedValue({task_id:"task",script_id:"script",sources:[]});
});

it("plays the chosen voice, language and submitted sentence from the exact completed attempt", async () => {
  const run = {id:"sample-en", identity:"voice-identity", preview_text:"Line seven.", status:"queued"};
  api.create.mockResolvedValue(run);
  api.attempts.mockResolvedValue({items:[
    {...run, id:"previous", status:"succeeded", output_url:"/old.wav"},
    {...run, status:"succeeded", output_url:"/current.wav"},
  ]});
  render(<DeliveryVoiceSample language="en" profileId="studio" version="v3" />);
  fireEvent.change(screen.getByLabelText("英文试听文字"), { target:{value:"Line seven."} });
  const audition = screen.getByRole("button",{name:"生成英文试听"});
  await waitFor(() => expect(audition).toBeEnabled());
  fireEvent.click(audition);
  await screen.findByText("已排队，等待设备");
  expect(api.create).toHaveBeenCalledWith({voice_profile_id:"studio",voice_version:"v3",language:"en",preview_text:"Line seven.",retry:false});
  await waitFor(() => expect(screen.getByLabelText("英文样句录音")).toHaveAttribute("src","/current.wav"), {timeout:4000});
  expect(api.create).toHaveBeenCalledTimes(1);
});

it.each([true, false])("auditions the saved text after editing the manuscript locally (complete: %s)", async (complete) => {
  const section = {id:"opening",title:"Opening",content:"Saved opening.",visual_prompt:"Original footage",evidence_reference_ids:[]};
  const revision = "2026-10-24T00:00:00Z";
  const common = {id:"script",task_id:"task",run_id:"run",topic_id:"topic",title:"Talk",
    language:"en-US",selection_status:"draft",created_at:revision,updated_at:revision,confirmed_at:null} as const;
  const script: CreationTaskScript = complete
    ? {...common,draft_complete:true,sections_loaded:true,sections:[section],sections_total_count:1}
    : {...common,draft_complete:false,sections_loaded:false,sections:null,sections_total_count:36};
  vi.mocked(getCreationTaskScriptSections).mockResolvedValue({
    script_id:"script",task_id:"task",updated_at:revision,title:"Talk",language:"en-US",
    selection_status:"draft",total_count:36,items:[{ordinal:1,section,digest:"test"}],
    next_after_ordinal:1,truncated:true,
  });
  api.create.mockResolvedValue({id:"saved-audition", preview_text:section.content, status:"succeeded", output_url:"/saved.wav"});
  render(<ScriptEditor taskId="task" script={script} busy={false} repairing={false} onConfirm={vi.fn()}
    auditionVoice={{language:"en",profileId:"studio",version:"v3"}}/>);
  fireEvent.change(await screen.findByLabelText("第 1 段正文"),{target:{value:"Unsaved local edit."}});
  fireEvent.click(screen.getByText("试听已保存段落"));
  fireEvent.change(screen.getByLabelText("选择试听段落"),{target:{value:section.id}});
  expect(screen.getByLabelText("英文试听文字")).toHaveValue(section.content);
  expect(screen.getByLabelText("英文试听文字")).toHaveAttribute("readonly");
  await click("生成英文试听");
  await screen.findByLabelText("英文样句录音");
  expect(api.create).toHaveBeenCalledExactlyOnceWith({
    voice_profile_id:"studio",voice_version:"v3",language:"en",preview_text:section.content,retry:false,
    saved_section:{task_id:"task",script_id:script.id,section_id:section.id},
  });
});

it("asks for a new selection when the chosen version becomes unavailable", async () => {
  api.profiles.mockResolvedValueOnce({items:[{id:"studio", version:"v3", language:"zh-en", runnable:true, display_name:"演示声音"}]}).mockResolvedValue({items:[]});
  const user = userEvent.setup();
  const prepare = vi.fn();
  render(<BilingualDeliveryControls pending={false} onPrepare={prepare}/>);
  await user.click(screen.getByText("分别指定中英文声音"));
  await user.click(await screen.findByRole("combobox",{name:"英文版声音"}));
  await user.click(screen.getByRole("option",{name:"演示声音 · v3"}));
  await user.click(screen.getByRole("button",{name:"刷新声音列表"}));
  expect(await screen.findByText("英文版声音已不可用，请重新选择声音。")).toBeVisible();
  expect(screen.getByRole("button",{name:"准备中文版和英文版"})).toBeDisabled();
  expect(prepare).not.toHaveBeenCalled();
});


function sample() {
  return render(<DeliveryVoiceSample language="en" profileId="studio" version="v3" />);
}

async function click(name: string) {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
}

it("prepares the fixed voice and current sentence, then runs the requested audition", async () => {
  api.create.mockRejectedValueOnce(new Error(needsPreparation)).mockResolvedValueOnce({
    id: "new-audition", status: "succeeded", preview_text: "Line seven.", output_url: "/prepared.wav",
  });
  api.dictionary.mockResolvedValue({ items: [
    { id: "workspace", version: "v2", workspace_id: "local", scope: "workspace", scope_reference: null },
    { id: "person", version: "v1", workspace_id: "local", scope: "person", scope_reference: "host" },
    { id: "other", version: "v1", workspace_id: "local", scope: "person", scope_reference: "other" },
  ] });
  api.prepare.mockResolvedValue(receipt);
  api.read.mockResolvedValue(done);
  api.finalize.mockResolvedValue(activated);
  sample();
  fireEvent.change(screen.getByLabelText("英文试听文字"), { target: { value: "Line seven." } });
  await click("生成英文试听");
  await click("准备本机试听");
  expect(await screen.findByText("已排队，等待本机")).toBeVisible();
  expect(api.prepare).toHaveBeenCalledWith(expect.objectContaining({
    kind: "narration", request_id: expect.any(String),
    shape: {
      voice_profile_id: "studio", voice_version: "v3", language: "en", text: "Line seven.",
      output_duration_seconds: 6, pronunciation_references: [{id:"workspace",version:"v2"},{id:"person",version:"v1"}],
    },
  }), configured.actor_scope, expect.any(AbortSignal));
  expect(await screen.findByText("本机试听准备已启用，可以生成试听。", {}, {timeout:4000})).toBeVisible();
  expect(api.finalize).toHaveBeenCalledTimes(1);
  expect(api.create).toHaveBeenCalledTimes(1);
  await click("生成英文试听");
  expect(await screen.findByLabelText("英文样句录音")).toHaveAttribute("src", "/prepared.wav");
  expect(api.create).toHaveBeenLastCalledWith({
    voice_profile_id:"studio", voice_version:"v3", language:"en", preview_text:"Line seven.", retry:true,
  });
});

it("reuses the original submitted payload after a timeout and refresh", async () => {
  api.resources.mockResolvedValue({ ...configured, available_operations: [] });
  api.prepare.mockRejectedValueOnce(new DOMException("timed out","TimeoutError")).mockResolvedValueOnce(receipt);
  api.read.mockResolvedValue(receipt);
  const original = sample();
  fireEvent.change(screen.getByLabelText("英文试听文字"), { target: { value: "Restore my sentence." } });
  await click("准备本机试听");
  expect(await screen.findByText("暂时无法确认设备准备状态，请核对原请求。")).toBeVisible();
  const payload = api.prepare.mock.calls[0][0];
  original.unmount();
  sample();
  await waitFor(() => expect(screen.getByLabelText("英文试听文字")).toHaveValue("Restore my sentence."));
  expect(api.prepare).toHaveBeenCalledTimes(1);
  await click("核对原准备状态");
  await screen.findByText("已排队，等待本机");
  expect(api.prepare).toHaveBeenCalledTimes(2);
  expect(api.prepare.mock.calls[1][0]).toEqual(payload);
});

it("reads a known work id after refresh without submitting it again", async () => {
  api.resources.mockResolvedValue({ ...configured, available_operations: [] });
  api.prepare.mockResolvedValue(receipt);
  const original = sample();
  await click("准备本机试听");
  await screen.findByText("已排队，等待本机");
  original.unmount();
  api.read.mockResolvedValue(done);
  api.finalize.mockImplementation(async () => {
    api.resources.mockResolvedValue(configured);
    return activated;
  });
  sample();
  expect(await screen.findByText("本机试听准备已启用，可以生成试听。")).toBeVisible();
  expect(api.read).toHaveBeenCalledWith(receipt.id, configured.actor_scope, expect.any(AbortSignal));
  expect(api.prepare).toHaveBeenCalledTimes(1);
  expect(api.create).not.toHaveBeenCalled();
});

it("verifies the same work id after a lost finalization response", async () => {
  api.resources.mockResolvedValue({ ...configured, available_operations: [] });
  api.prepare.mockResolvedValue(done);
  api.read.mockResolvedValue(done);
  api.finalize.mockImplementationOnce(async () => {
    api.resources.mockResolvedValue(configured);
    throw new DOMException("timed out", "TimeoutError");
  }).mockResolvedValueOnce(activated);
  const original = sample();
  await click("准备本机试听");
  expect(await screen.findByText("暂时无法确认设备准备状态，请核对原请求。")).toBeVisible();
  expect(screen.getByRole("button", { name: "生成英文试听" })).toBeDisabled();
  original.unmount();
  sample();
  expect(await screen.findByText("本机试听准备已启用，可以生成试听。")).toBeVisible();
  expect(api.finalize).toHaveBeenCalledTimes(2);
  for (const call of api.finalize.mock.calls) {
    expect(call).toEqual([receipt.id, configured.actor_scope, expect.any(AbortSignal)]);
  }
  expect(api.prepare).toHaveBeenCalledTimes(1);
  expect(api.create).not.toHaveBeenCalled();
});

it("recovers the audition rejected before preparation without recalibrating after refresh", async () => {
  const sentence = "Preview this exact sentence.";
  const rejected = {
    id: "previous-audition", preview_text: sentence, status: "failed",
    reason: "device_submission_failed", repair_action: needsPreparation,
  };
  api.create.mockRejectedValueOnce(new Error(needsPreparation)).mockResolvedValueOnce(rejected)
    .mockResolvedValueOnce({ ...rejected, id: "completed-audition", status: "succeeded",
      reason: null, repair_action: null, output_url: "/completed.wav" });
  api.prepare.mockResolvedValue(done);
  api.read.mockResolvedValue(done);
  api.finalize.mockResolvedValue(activated);
  const original = sample();
  fireEvent.change(screen.getByLabelText("英文试听文字"), { target: { value: sentence } });
  await click("生成英文试听");
  await click("准备本机试听");
  await screen.findByText("本机试听准备已启用，可以生成试听。");
  original.unmount();
  sample();
  await screen.findByText("本机试听准备已启用，可以生成试听。");
  await click("生成英文试听");
  expect(await screen.findByText(/可再次生成试听/)).toBeVisible();
  expect(api.create).toHaveBeenNthCalledWith(2, expect.objectContaining({ preview_text: sentence, retry: false }));
  await click("生成英文试听");
  expect(await screen.findByLabelText("英文样句录音")).toHaveAttribute("src", "/completed.wav");
  expect(api.create).toHaveBeenNthCalledWith(3, expect.objectContaining({ preview_text: sentence, retry: true }));
  expect(api.prepare).toHaveBeenCalledTimes(1);
});

it("retains the original preparation when scope changes before finalization", async () => {
  api.resources.mockResolvedValue({ ...configured, available_operations: [] });
  api.prepare.mockResolvedValue(receipt);
  api.read.mockImplementation(async () => {
    api.resources.mockResolvedValue({ ...configured, actor_scope: "b".repeat(64) });
    return done;
  });
  sample();
  await click("准备本机试听");
  await screen.findByText("已排队，等待本机");
  expect(await screen.findByText("账号、工作区或设备已切换。原准备请求仍保留。", {}, {timeout:4000})).toBeVisible();
  expect(api.finalize).not.toHaveBeenCalled();
  expect(api.prepare).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(1);
});


it("finalizes a failed device receipt without starting another preparation", async () => {
  api.resources.mockResolvedValue({ ...configured, available_operations: [] });
  const failed = { ...receipt, state: "failed", failure_reason: "本机内存不足。" };
  api.prepare.mockResolvedValue(failed);
  api.read.mockResolvedValue(failed);
  api.finalize.mockResolvedValue({ ...failed, profile_published: false, normal_work_started: false });
  sample();
  await click("准备本机试听");
  expect(await screen.findByText("本次准备未完成，原请求已收尾。")).toBeVisible();
  expect(screen.getByText("本机内存不足。")).toBeVisible();
  expect(api.prepare).toHaveBeenCalledTimes(1);
  expect(api.finalize).toHaveBeenCalledTimes(1);
  expect(api.create).not.toHaveBeenCalled();
  const first = api.prepare.mock.calls[0][0].request_id;
  await click("重新准备本机试听");
  await waitFor(() => expect(api.prepare).toHaveBeenCalledTimes(2));
  expect(api.prepare.mock.calls[1][0].request_id).not.toBe(first);
});

it("can prepare the same sentence again when another language replaced its device profile", async () => {
  api.create.mockRejectedValue(new Error(needsPreparation));
  api.prepare.mockResolvedValue(done);
  api.read.mockResolvedValue(done);
  api.finalize.mockResolvedValue(activated);
  sample();
  await click("生成英文试听");
  await click("准备本机试听");
  expect(await screen.findByText("本机试听准备已启用，可以生成试听。")).toBeVisible();
  await click("生成英文试听");
  const again = await screen.findByRole("button", { name: "准备本机试听" });
  expect(again).toBeVisible();
  await click("准备本机试听");
  await waitFor(() => expect(api.prepare).toHaveBeenCalledTimes(2));
  expect(api.prepare.mock.calls[1][0].request_id).not.toBe(api.prepare.mock.calls[0][0].request_id);
});
