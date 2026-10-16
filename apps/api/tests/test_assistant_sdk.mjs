import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { execFileSync } from "node:child_process";
import { pathToFileURL, fileURLToPath } from "node:url";
import { openCreationSession } from "../agent_runtime/session.mjs";
import { creationInputExtension } from "../agent_runtime/input-delivery.mjs";

// Actual installed SDK, synthetic model responses and editing tool. No provider,
// HTTP service, user media, production DB or device scheduler is used here.
const piExecutable=execFileSync("which",["pi"],{encoding:"utf8"}).trim();
const modulePath=join(dirname(dirname(realpathSync(piExecutable))),"index.js");
const aiRoot = join(dirname(dirname(modulePath)), "node_modules/@earendil-works/pi-ai/dist");
const { AssistantMessageEventStream } = await import(pathToFileURL(join(aiRoot, "utils/event-stream.js")));

test("SDK owns two editing rounds, safe steering/follow-up, persisted history and trusted skills", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "diary-sdk-slice-"));
  const sessionId = "11111111-1111-4111-8111-111111111111";
  let current;
  try {
    await writeFile(join(cwd, "models.json"), JSON.stringify({ providers: { "ai-video-diary": {
      baseUrl: "http://127.0.0.1:9", api: "openai-completions", apiKey: "synthetic-unused",
      models: [{ id: "synthetic-editor", contextWindow: 16384, maxTokens: 1024 }],
    } } }));
    let version = 1;
    let caption = "旧字幕";
    const receipts = [];
    const edit = { name: "edit_caption", label: "修改字幕", description: "合成字幕CAS切片",
      parameters: { type: "object", properties: { version: { type: "number" }, text: { type: "string" } }, required: ["version", "text"] },
      execute: async (id, args) => {
        assert.equal(args.version, version);
        const previous = caption; caption = args.text; version += 1;
        const receipt = { id, previous, caption, version }; receipts.push(receipt);
        return { content: [{ type: "text", text: JSON.stringify(receipt) }], details: receipt };
      } };
    const pending = [];
    const consumed = [], completed = [], rebound = [];
    const inputExtension = creationInputExtension({
      getSession:()=>current.session,
      rebind:async environment=>{rebound.push(environment.input_id);},
      ask:async (phase,id,mode)=>{
        if (phase === "next") return pending.find(item=>!consumed.includes(item.input_id) && (mode === "any" || item.mode===mode)) ?? null;
        if (phase === "consume") { if (id === "revoked") return null; consumed.push(id); return {environment:{input_id:id},frozen_context:{selected_object_refs:[`cue:${id}`]}}; }
        if (phase === "complete") completed.push(id);
        return null;
      },
    });
    const options = { modulePath, cwd, sessionId, sessionDir: join(cwd, "sessions"),
      modelId: "synthetic-editor", systemPrompt: "你是视频编辑助手。按真实工具回执回答。", tools: [edit.name], customTools: [edit], extensionFactories:[inputExtension] };
    current = await openCreationSession(options);
    assert.deepEqual(current.resourceLoader.getSkills().skills.map(s => s.name).sort(), ["local-edit", "narration-materials", "source-speech"]);
    assert.deepEqual(current.session.getActiveToolNames().sort(), ["edit_caption", "read_creation_skill"]);
    const events = [];
    current.session.subscribe(event => events.push(event));
    let calls = 0;
    current.session.agent.streamFunction = (_model, context, options) => {
      const stream = new AssistantMessageEventStream();
      const index = calls++;
      const latestUser = context.messages.findLast(message=>message.role === "user");
      if (JSON.stringify(latestUser.content).includes("用户追加输入")) assert.match(JSON.stringify(latestUser.content), /selected_object_refs/);
      if (options.signal?.aborted) assert.doesNotMatch(JSON.stringify(context.messages), /不应执行的要求/);
      queueMicrotask(() => {
        const content = !options.signal?.aborted && (index === 0 || index === 2)
          ? [{ type: "toolCall", id: `edit-${index}`, name: edit.name, arguments: { version, text: index === 0 ? "第一轮字幕" : "第二轮字幕" } }]
          : [{ type: "text", text: `已回读版本 ${version}` }];
        const message = { role: "assistant", content, api: "openai-completions", provider: "ai-video-diary", model: "synthetic-editor",
          stopReason: options.signal?.aborted ? "aborted" : content[0].type === "toolCall" ? "toolUse" : "stop", timestamp: Date.now(),
          usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
        if (message.stopReason === "aborted") stream.push({type:"error",reason:"aborted",error:message});
        else stream.push({ type: "done", reason: message.stopReason, message }); stream.end(message);
      });
      return stream;
    };
    await current.session.prompt("把选中字幕改为第一轮字幕", { source: "rpc" });
    await current.session.prompt("再改为第二轮字幕", { source: "rpc" });
    assert.equal(caption, "第二轮字幕"); assert.equal(version, 3); assert.equal(receipts.length, 2);
    assert.equal(events.filter(e => e.type === "tool_execution_end").length, 2);
    assert.equal(events.filter(e => e.type === "agent_settled").length, 2);
    pending.push({input_id:"steer-1",message:"当前要求补充",mode:"steer"},{input_id:"later-1",message:"本轮完成后说明",mode:"follow_up"});
    await current.session.prompt("继续处理当前目标", {source:"rpc"});
    assert.deepEqual(consumed,["steer-1","later-1"]);
    assert.deepEqual(completed,["steer-1","later-1"]);
    assert.deepEqual(rebound,["steer-1","later-1"]);
    assert.equal(inputExtension.getLastConsumedInputId(),"later-1","completion must retain final input provenance");
    assert.equal(receipts.length,2,"queue delivery must not replay earlier edits");
    pending.push({input_id:"revoked",message:"不应执行的要求",mode:"steer"});
    await current.session.prompt("保持原目标",{source:"rpc"});
    assert.match(inputExtension.getFailure().message,/未通过安全点/);
    assert.equal(current.session.messages.at(-1).stopReason,"aborted");
    assert.equal(receipts.length,2);
    const count = current.session.messages.length;
    current.session.dispose();
    current = await openCreationSession(options);
    assert.equal(current.session.messages.length, count);
    assert.equal(current.session.messages.filter(m => m.role === "toolResult").length, 2);
    assert.equal(receipts.length, 2, "opening SDK history must not replay business writes");
  } finally { current?.session.dispose(); await rm(cwd, { recursive: true, force: true }); }
});

test("product extension loads with SDK and rebinds its tool closures", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "diary-sdk-extension-"));
  const previousModule=process.env.AI_VIDEO_DIARY_AGENT_PI_MODULE;
  const previousTools=process.env.AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS;
  process.env.AI_VIDEO_DIARY_AGENT_PI_MODULE=modulePath;
  process.env.AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS="read_workspace_context,codemode";
  let current;
  try {
    await writeFile(join(cwd,"models.json"),JSON.stringify({providers:{"ai-video-diary":{
      baseUrl:"http://127.0.0.1:9",api:"openai-completions",apiKey:"unused",
      models:[{id:"extension-test",contextWindow:16384,maxTokens:1024}],
    }}}));
    current=await openCreationSession({modulePath,cwd,sessionDir:join(cwd,"sessions"),
      sessionId:"22222222-2222-4222-8222-222222222222",modelId:"extension-test",systemPrompt:"test",
      extensionPath:fileURLToPath(new URL("../agent_tools/diary_tools.ts",import.meta.url)),
      tools:["read_workspace_context","codemode"]});
    assert.deepEqual(current.session.getActiveToolNames().sort(),["codemode","read_creation_skill","read_workspace_context"]);
    await current.rebindTools({...process.env, AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION:"新的明确要求"});
    let started;
    const modelStarted = new Promise(resolve=>{started=resolve;});
    current.session.agent.streamFunction=(_model,_context,options)=>{
      const stream=new AssistantMessageEventStream();
      options.signal.addEventListener("abort",()=>{
        const message={role:"assistant",content:[],api:"openai-completions",provider:"ai-video-diary",model:"extension-test",
          stopReason:"aborted",timestamp:Date.now(),usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
        stream.push({type:"error",reason:"aborted",error:message});stream.end(message);
      },{once:true});
      started();return stream;
    };
    const running=current.session.prompt("测试停止",{source:"rpc"});
    await modelStarted;
    await current.session.abort();
    await running;
    assert.equal(current.session.messages.at(-1).stopReason,"aborted");

    assert.deepEqual(current.session.getActiveToolNames().sort(),["codemode","read_creation_skill","read_workspace_context"]);
  } finally {
    current?.session.dispose(); await rm(cwd,{recursive:true,force:true});
    if (previousModule===undefined) delete process.env.AI_VIDEO_DIARY_AGENT_PI_MODULE; else process.env.AI_VIDEO_DIARY_AGENT_PI_MODULE=previousModule;
    if (previousTools===undefined) delete process.env.AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS; else process.env.AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS=previousTools;
  }
});


test("SDK keeps original creation receipt input binding in completion", async () => {
  const {createToolCallScope}=await import("../agent_tools/diary/registration.ts");
  const {createSessionResult}=await import("../agent_runtime/result.mjs");
  const cwd=await mkdtemp(join(tmpdir(),"diary-sdk-creation-"));
  let current;
  try {
    await writeFile(join(cwd,"models.json"),JSON.stringify({providers:{"ai-video-diary":{
      baseUrl:"http://127.0.0.1:9",api:"openai-completions",apiKey:"unused",
      models:[{id:"creation-test",contextWindow:16384,maxTokens:1024}],
    }}}));
    const tools=[];
    const scope=createToolCallScope(()=>({turnId:"original-turn"}));
    scope.bind({registerTool:tool=>tools.push(tool)}).registerTool({
      name:"preview_creation_plan",label:"保存目标",description:"SDK receipt regression",
      parameters:{type:"object",properties:{}},
      execute:async()=>({content:[{type:"text",text:"saved"}],details:{kind:"plan_preview",preview:{kind:"creation_request",request:{id:"request-a"}}}}),
    });
    current=await openCreationSession({modulePath,cwd,sessionDir:join(cwd,"sessions"),
      sessionId:"33333333-3333-4333-8333-333333333333",modelId:"creation-test",systemPrompt:"test",
      tools:["preview_creation_plan"],customTools:tools});
    const result=createSessionResult("creation");current.session.subscribe(event=>result.observe(event));
    let calls=0;
    current.session.agent.streamFunction=()=>{
      const stream=new AssistantMessageEventStream();
      const content=calls++===0?[{type:"toolCall",id:"plan-call",name:"preview_creation_plan",arguments:{}}]:[{type:"text",text:"saved"}];
      const message={role:"assistant",content,api:"openai-completions",provider:"ai-video-diary",model:"creation-test",
        stopReason:content[0].type==="toolCall"?"toolUse":"stop",timestamp:Date.now(),
        usage:{input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
      queueMicrotask(()=>{stream.push({type:"done",reason:message.stopReason,message});stream.end(message);});return stream;
    };
    await current.session.prompt("保存目标",{source:"rpc"});
    assert.equal(result.value().tool_results.length,1);
    assert.equal(result.value().tool_results[0].result.details.kind,"plan_preview");
    assert.equal(result.value().tool_results[0].result.details.invocation_input_id,"original-turn");
  } finally {current?.session.dispose();await rm(cwd,{recursive:true,force:true});}
});
