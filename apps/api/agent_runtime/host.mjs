import { createInterface } from "node:readline";
import { openCreationSession } from "./session.mjs";
import { creationInputExtension } from "./input-delivery.mjs";
import { createSessionResult } from "./result.mjs";
import { pathToFileURL } from "node:url";

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
const iterator = lines[Symbol.asyncIterator]();
const initial = await iterator.next();
if (initial.done) throw new Error("缺少宿主发送快照。");
const input = JSON.parse(initial.value);
const originalInputId = process.env.AI_VIDEO_DIARY_AGENT_TURN_ID || null;
if (input.inputEnabled && !originalInputId) throw new Error("原始当前输入未绑定，未启动创作会话。");
const emit = event => process.stdout.write(JSON.stringify(event) + "\n");
// Host stdout is an event protocol. Standalone tool CLI stdout remains one JSON result.
process.env.AI_VIDEO_DIARY_AGENT_HOST_PROTOCOL = "phase-v1";
const responses = new Map();
const businessResponses = new Map();
const cancelledCalls = new Set();
let checkpoint = 0;
let inputClosed = false;
let session;
let abortRequested = false;
let abortRun;
function abortSession() {
  abortRequested = true;
  // Release an input checkpoint that cannot complete during cancellation.
  // A late response may resolve the same rejected promise, never consume anew.
  for (const callback of responses.values()) callback.reject(new Error("本轮已取消，追加输入未执行。"));
  for (const callback of businessResponses.values()) callback.abort();
  if (session && !abortRun) {
    abortRun = session.abort().then(() => emit({ type: "diary_abort_ack" }));
  }
  return abortRun;
}
void (async () => {
  for await (const line of iterator) {
    const response = JSON.parse(line);
    if (response.type === "diary_abort") {
      await abortSession();
      continue;
    }
    if (response.type === 'diary_business_result') {
      const callback = businessResponses.get(response.call_id);
      if (!callback && cancelledCalls.has(response.call_id)) continue;
      if (!callback) throw new Error('业务回执身份不匹配。');
      callback.complete(response);
      continue;
    }
    if (response.type !== "diary_input_response") throw new Error("未知的宿主输入协议。");
    const callback = responses.get(response.checkpoint_id);
    if (!callback) throw new Error("追加输入回执身份不匹配。");
    responses.delete(response.checkpoint_id);
    callback.resolve(response.result);
  }
  inputClosed = true;
  if (responses.size || businessResponses.size) throw new Error("宿主在调用核验完成前关闭了会话。");
})().catch(error => {
  inputClosed = true;
  for (const callback of responses.values()) callback.reject(error);
  responses.clear();
  for (const callback of businessResponses.values()) callback.fail(error);
});
function callBusiness(operation, arguments_, {callId, toolCallId, signal}) {
  if (abortRequested || inputClosed || signal.aborted) return Promise.reject(new Error('本轮业务调用已停止。'));
  return new Promise((resolve, reject) => {
    const cleanup = () => { businessResponses.delete(callId); signal.removeEventListener('abort', abort); };
    const abort = () => {
      cleanup();
      cancelledCalls.add(callId);
      emit({type: 'diary_business_cancel', call_id: callId});
      reject(new Error('业务调用已停止，已提交结果须回读原请求。'));
    };
    businessResponses.set(callId, {
      abort,
      complete: result => { cleanup(); resolve(result); },
      fail: error => { cleanup(); reject(error); },
    });
    signal.addEventListener('abort', abort, {once: true});
    emit({type: 'diary_business_call', call_id: callId, tool_call_id: toolCallId ?? null, operation, arguments: arguments_});
  });
}
function ask(phase, inputId = null, mode = null) {
  if (abortRequested) return Promise.reject(new Error("本轮已取消，不接收追加输入。"));
  if (!input.inputEnabled) return Promise.resolve(null);
  if (inputClosed) return Promise.reject(new Error("宿主输入连接已关闭，追加输入未执行。"));
  const id = ++checkpoint;
  return new Promise((resolve, reject) => {
    responses.set(id, {resolve, reject});
    emit({ type: "diary_input_checkpoint", checkpoint_id: id, phase, input_id: inputId, mode });
  });
}
const businessCall = input.businessEnabled ? callBusiness : undefined;
if (input.factMode) {
  try {
    if (!businessCall || !['task', 'memory'].includes(input.factMode)) throw new Error('前置事实读取未绑定宿主业务管道。');
    const {createDiaryTools} = await import(pathToFileURL(input.extensionPath).href);
    const tools = createDiaryTools(process.env, undefined, businessCall);
    const value = input.factMode === 'memory' ? await tools.readCreatorMemoryFacts() : {
      ...await tools.readCurrentTaskFacts(),
      _edit_capabilities: await tools.readTaskEditCapabilities(),
      _media_script_capabilities: await tools.readMediaScriptCapabilities(),
      _script_tool_capabilities: await tools.readMediaScriptToolCapabilities(),
    };
    emit({type: 'diary_preflight_result', kind: input.factMode, ok: true, value});
  } catch (error) {
    let failure = {error: 'preflight_failed', reason: '发送前宿主事实读取未完成。',
      repair_action: '核对原任务授权和当前对象后继续，不重复提交业务操作。', stage: 'preflight', submission_effect: 'not_submitted'};
    if (error?.businessFailure) {
      const {safeBusinessFailure} = await import(new URL('./diary/transport.ts', pathToFileURL(input.extensionPath)).href);
      failure = {...failure, ...safeBusinessFailure(error.businessFailure)};
    }
    emit({type: 'diary_preflight_result', kind: input.factMode, ok: false,
      failure: {...failure, status_code: Number.isInteger(error?.httpStatus) ? error.httpStatus : 500}});
    process.exitCode = 1;
  } finally { lines.close(); }
} else {
let rebindTools;
const inputExtension = creationInputExtension({ ask, getSession: () => session, rebind: environment => rebindTools({ ...process.env, ...environment }) });
({ session, rebindTools } = await openCreationSession({ ...input, businessCall, extensionFactories: input.inputEnabled ? [inputExtension] : [] }));
const result = createSessionResult(input.sessionId);
session.subscribe(event => { result.observe(event); emit(event); });
try {
  if (abortRequested) await abortSession();
  else await session.prompt(input.message, { source: "rpc" });
  if (inputExtension.getFailure()) throw inputExtension.getFailure();
  emit({...result.value(),input_id:inputExtension.getLastConsumedInputId() ?? originalInputId});
} finally {
  if (abortRun) await abortRun;
  session.dispose();
  lines.close();
}
}
