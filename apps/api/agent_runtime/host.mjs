import { createInterface } from "node:readline";
import { openCreationSession } from "./session.mjs";
import { creationInputExtension } from "./input-delivery.mjs";
import { createSessionResult } from "./result.mjs";

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
const iterator = lines[Symbol.asyncIterator]();
const initial = await iterator.next();
if (initial.done) throw new Error("缺少宿主发送快照。");
const input = JSON.parse(initial.value);
const emit = event => process.stdout.write(JSON.stringify(event) + "\n");
const responses = new Map();
let checkpoint = 0;
let inputClosed = false;
void (async () => {
  for await (const line of iterator) {
    const response = JSON.parse(line);
    if (response.type !== "diary_input_response") throw new Error("未知的宿主输入协议。");
    const callback = responses.get(response.checkpoint_id);
    if (!callback) throw new Error("追加输入回执身份不匹配。");
    responses.delete(response.checkpoint_id);
    callback.resolve(response.result);
  }
  inputClosed = true;
  if (responses.size) throw new Error("宿主在追加输入核验完成前关闭了会话。");
})().catch(error => {
  inputClosed = true;
  for (const callback of responses.values()) callback.reject(error);
  responses.clear();
});
function ask(phase, inputId = null, mode = null) {
  if (!input.inputEnabled) return Promise.resolve(null);
  if (inputClosed) return Promise.reject(new Error("宿主输入连接已关闭，追加输入未执行。"));
  const id = ++checkpoint;
  return new Promise((resolve, reject) => {
    responses.set(id, {resolve, reject});
    emit({ type: "diary_input_checkpoint", checkpoint_id: id, phase, input_id: inputId, mode });
  });
}
let session;
let rebindTools;
const inputExtension = creationInputExtension({ ask, getSession: () => session, rebind: environment => rebindTools({ ...process.env, ...environment }) });
({ session, rebindTools } = await openCreationSession({ ...input, extensionFactories: input.inputEnabled ? [inputExtension] : [] }));
const result = createSessionResult(input.sessionId);
session.subscribe(event => { result.observe(event); emit(event); });
try {
  await session.prompt(input.message, { source: "rpc" });
  if (inputExtension.getFailure()) throw inputExtension.getFailure();
  emit(result.value());
} finally {
  session.dispose();
  lines.close();
}
