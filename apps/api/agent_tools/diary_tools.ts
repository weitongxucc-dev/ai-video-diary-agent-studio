// Public host/Pi entry. A process has one explicitly assembled trusted session;
// capability caches, task-read facts and action guards have separate owners.
import {createDiaryTools} from './diary/runtime.ts';
import type {ToolRegistrar} from './diary/contracts.ts';

let activeRuntime: ReturnType<typeof createDiaryTools> | undefined;
function runtime() {
  return activeRuntime ??= createDiaryTools(process.env);
}

export function typedRequestId(turn: string, call: string) { return runtime().typedRequestId(turn, call); }
export async function readTaskEditCapabilities() { return runtime().readTaskEditCapabilities(); }
export async function readMediaScriptCapabilities() { return runtime().readMediaScriptCapabilities(); }
export async function readMediaScriptToolCapabilities() { return runtime().readMediaScriptToolCapabilities(); }
export async function readCreatorMemoryFacts() { return runtime().readCreatorMemoryFacts(); }
export async function readCurrentTaskFacts() { return runtime().readCurrentTaskFacts(); }
export default function (agent: ToolRegistrar) { return runtime().register(agent); }
