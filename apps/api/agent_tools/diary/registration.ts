import {AsyncLocalStorage} from 'node:async_hooks';
import type {ToolRegistrar} from './contracts.ts';

export function createToolCallScope() {
  const signal = new AsyncLocalStorage<AbortSignal | undefined>();
  return {
    getSignal: () => signal.getStore(),
    bind: (agent: ToolRegistrar): ToolRegistrar => ({registerTool: (tool: any) => agent.registerTool({...tool,
      outputSchema: {type: "object", additionalProperties: true},
      execute: (...args: any[]) => signal.run(args[2], async () => {
        const result = await tool.execute(...args);
        return {...result, structuredContent: result.details};
      }),
    })}),
  };
}
