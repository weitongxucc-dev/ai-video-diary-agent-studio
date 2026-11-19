import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { DeliveryVoiceSample } from "../creation-task/DeliveryVoiceSample";
import { BilingualDeliveryControls } from "../creation-task/BilingualDeliveryControls";

const api = vi.hoisted(() => ({ create: vi.fn(), attempts: vi.fn(), profiles: vi.fn() }));
vi.mock("@/lib/media-profiles-api", () => ({ createVoicePreview: api.create, listMediaPreviewAttempts: api.attempts, listVoiceProfiles:api.profiles, mediaProfilesErrorMessage: (e: Error) => e.message }));

it("plays the chosen voice, language and submitted sentence from the exact completed attempt", async () => {
  const run = {id:"sample-en", identity:"voice-identity", preview_text:"Line seven.", status:"queued"};
  api.create.mockResolvedValue(run);
  api.attempts.mockResolvedValue({items:[
    {...run, id:"previous", status:"succeeded", output_url:"/old.wav"},
    {...run, status:"succeeded", output_url:"/current.wav"},
  ]});
  render(<DeliveryVoiceSample language="en" profileId="studio" version="v3" />);
  fireEvent.change(screen.getByLabelText("英文试听文字"), { target:{value:"Line seven."} });
  fireEvent.click(screen.getByRole("button",{name:"生成英文试听"}));
  await screen.findByText("已排队，等待设备");
  expect(api.create).toHaveBeenCalledWith({voice_profile_id:"studio",voice_version:"v3",language:"en",preview_text:"Line seven.",retry:false});
  await waitFor(() => expect(screen.getByLabelText("英文样句录音")).toHaveAttribute("src","/current.wav"), {timeout:4000});
  expect(api.create).toHaveBeenCalledTimes(1);
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
