import { afterEach, describe, expect, it, vi } from "vitest";
import { getCreationTaskNarrationVoiceSelection, saveAssistantScriptDraft, confirmCreationTaskScript } from "../api/creation-tasks";
import {
  confirmCreationTaskTopic,
  CreationPlanApiError,
  generateCreationTaskTopics,
  getCreationTaskTopics,
  previewCreationExecution,
  selectCreationTaskNarrationVoice,
} from "../api";

afterEach(() => vi.unstubAllGlobals());

describe("creation-task topic API", () => {
  it("uses the frozen GET and empty generation body", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ task: {} }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ task: {} }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await getCreationTaskTopics("task/1");
    await generateCreationTaskTopics("task/1");

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/creation-tasks/task%2F1/topics", expect.objectContaining({ cache: "no-store" }));
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/creation-tasks/task%2F1/topics/generate", expect.objectContaining({
      method: "POST",
      body: "{}",
    }));
  });

  it("uses the persisted topic identity and an empty confirmation body", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task: {} }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await confirmCreationTaskTopic("task-1", "topic-1");

    expect(fetch).toHaveBeenCalledWith("/api/creation-tasks/task-1/topics/topic-1/confirm", expect.objectContaining({
      method: "POST",
      body: "{}",
    }));
  });

  it("keeps the structured failure fields from the topic service", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        error: "capability_unavailable",
        reason: "capability_unavailable",
        impact: "没有调用文字模型，也没有生成候选。",
        repair_action: "配置文字模型后重新开始选题。",
        retryable: true,
      },
    }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);

    await expect(generateCreationTaskTopics("task-1")).rejects.toMatchObject({
      status: 503,
      code: "capability_unavailable",
      message: "当前文字模型不可用",
      reason: "capability_unavailable",
      impact: "没有调用文字模型，也没有生成候选。",
      repairAction: "配置文字模型后重新开始选题。",
    } satisfies Partial<CreationPlanApiError>);
  });

  it("projects a synthetic missing-authorized-voice refusal through execution preview", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        error: "voice_identity_missing",
        reason: "voice_identity_missing",
        impact: "当前创作任务未生成配音，未调用声音模型。",
        repair_action: "重新预检并选择已授权的中文声音版本。",
        retryable: true,
      },
    }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);

    await expect(previewCreationExecution("task-1")).rejects.toMatchObject({
      status: 503,
      code: "voice_identity_missing",
      message: "缺少已授权的声音版本",
      impact: "当前创作任务未生成配音，未调用声音模型。",
      repairAction: "重新预检并选择已授权的中文声音版本。",
    } satisfies Partial<CreationPlanApiError>);
    expect(fetch).toHaveBeenCalledWith("/api/creation-tasks/task-1/execution/preview", expect.objectContaining({ cache: "no-store" }));
  });

  it("binds an explicit versioned voice without sending a local path", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task: {} }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await selectCreationTaskNarrationVoice("task-1", {
      request_id: "00000000-0000-4000-8000-000000000001",
      expected_plan_updated_at: "2026-10-18T00:00:00Z",
      expected_task_updated_at: "2026-10-18T00:00:01Z",
      voice_profile_id: "voice-1",
      voice_version: "v2",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/creation-tasks/task-1/narration/voice",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ request_id: "00000000-0000-4000-8000-000000000001", expected_plan_updated_at: "2026-10-18T00:00:00Z", expected_task_updated_at: "2026-10-18T00:00:01Z", voice_profile_id: "voice-1", voice_version: "v2" }),
      }),
    );
    expect(fetch.mock.calls[0]?.[1]?.body).not.toContain("/Users/");
  });

  it("queries the original voice selection receipt without posting again", async () => {
    const receipt = {request_id:"original-request", task_id:"task/1", selected_voice:{profile_id:"voice-1", version:"v2"}};
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(receipt), {status:200}));
    vi.stubGlobal("fetch", fetch);
    await expect(getCreationTaskNarrationVoiceSelection("task/1", "original-request")).resolves.toEqual(receipt);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith("/api/creation-tasks/task%2F1/narration/voice/requests/original-request", expect.objectContaining({cache:"no-store"}));
    expect(fetch.mock.calls[0][1].method).toBeUndefined();
  });
});

it.each(['save','confirm'] as const)('preserves explicit replace_all intent for script %s without inventing it',async(action)=>{
  const fetch=vi.fn().mockImplementation(async()=>new Response(JSON.stringify({})));
  vi.stubGlobal('fetch',fetch);
  const input={title:'完整文案',expected_updated_at:'2026-10-24T00:00:00Z',sections:[{
    id:'section-1',title:'正文',content:'完整正文',visual_prompt:'完整画面',evidence_reference_ids:[],
  }]};
  const submit=(value:typeof input & {edit_mode?:'replace_all'})=>action==='save'
    ? saveAssistantScriptDraft('task/1',value)
    : confirmCreationTaskScript('task/1','script/1',value);
  await submit({...input,edit_mode:'replace_all'});
  await submit(input);
  const [explicit,unspecified]=fetch.mock.calls;
  expect(explicit[0]).toBe(action==='save'?'/api/creation-tasks/task%2F1/assistant/script-draft':'/api/creation-tasks/task%2F1/scripts/script%2F1/confirm');
  expect(explicit[1].method).toBe('POST');
  expect(JSON.parse(explicit[1].body)).toEqual({...input,edit_mode:'replace_all'});
  expect(JSON.parse(unspecified[1].body)).toEqual(input);
});
