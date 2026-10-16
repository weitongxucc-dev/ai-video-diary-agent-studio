// Public host/Pi entry. A process has one explicitly assembled trusted session;
// capability caches, task-read facts and action guards have separate owners.
import {createDiaryTools} from './diary/runtime.ts';
import {readAgentContext} from './diary/trusted-context.ts';
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
/** One product tool binding per SDK session; rebind only after a verified input. */
export function createDiaryExtension(environment: Record<string, string | undefined>) {
  const declarations = readAgentContext(environment);
  let current = createDiaryTools(environment, declarations);
  let api: ToolRegistrar;
  let activeTools = new Map<string, any>();
  function registry(register: boolean, next: Map<string, any>) {
    return new Proxy(api, { get(target, property) {
      if (property !== "registerTool") {
        const value=Reflect.get(target,property);
        return typeof value === "function" ? value.bind(target) : value;
      }
      return (tool: any) => {
        next.set(tool.name,tool);
        if (register) target.registerTool({...tool,execute:(...args:any[])=>{
          const implementation=activeTools.get(tool.name);
          if (!implementation) throw new Error("该操作不在当前已消费输入的能力范围。");
          return implementation.execute(...args);
        }});
      };
    }});
  }
  return {
    extension(agent: ToolRegistrar) { api=agent; return current.register(registry(true,activeTools)); },
    async rebind(nextEnvironment: Record<string,string|undefined>) {
      current=createDiaryTools(nextEnvironment, declarations);
      const next=new Map<string,any>();
      await current.register(registry(false,next));
      activeTools=next;
    },
  };
}
export default function (agent: ToolRegistrar) { return createDiaryExtension(process.env).extension(agent); }
