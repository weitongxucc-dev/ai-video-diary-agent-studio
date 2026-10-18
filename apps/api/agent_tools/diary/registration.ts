import {AsyncLocalStorage} from 'node:async_hooks';
import type {ToolRegistrar, ActionSource} from './contracts.ts';

const inputBoundActions = new Set([
  'apply_typed_edit', 'preview_typed_edit', 'preview_media_script', 'preview_media_script_tool',
  'observe_task_output', 'save_media_script_tool', 'update_media_script_tool', 'revoke_media_script_tool',
  'start_creation_execution', 'resume_creation_execution', 'cancel_creation_execution',
  'read_creation_execution',
  'regenerate_storyboard', 'read_storyboard_regeneration',
  'select_narration_voice',
  'preview_edit_selection', 'discard_edit_proposal', 'undo_last_edit', 'redo_last_edit',
]);

export function createToolCallScope(readActionSource: () => ActionSource) {
  const signal = new AsyncLocalStorage<AbortSignal | undefined>();
  return {
    getSignal: () => signal.getStore(),
    bind: (agent: ToolRegistrar): ToolRegistrar => new Proxy(agent,{get(target,property) {
      if (property !== 'registerTool') {
        const value=Reflect.get(target,property);
        return typeof value === 'function' ? value.bind(target) : value;
      }
      return (tool: any) => target.registerTool({...tool,
      outputSchema: {type: "object", additionalProperties: true},
      execute: (...args: any[]) => signal.run(args[2], async () => {
        const source = readActionSource();
        const result = await tool.execute(...args);
        const details = {...result.details, invocation_input_id: source.inputId || source.turnId || null,
          ...(inputBoundActions.has(tool.name)
            ? {input_id: source.inputId ?? null, input_turn_id: source.inputTurnId ?? null} : {})};
        return {...result, details, structuredContent: details};
      }),
      });
    }}),
  };
}
