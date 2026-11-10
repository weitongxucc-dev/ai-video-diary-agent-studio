import {pathToFileURL} from 'node:url';

export async function registerControlledCodemode(agent: any) {
  const module = process.env.AI_VIDEO_DIARY_AGENT_PI_MODULE;
  if (!module) return;
  const { createCodemodeExtension } = await import(pathToFileURL(module).href);
  const allowed = new Set([process.env.AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS,process.env.AI_VIDEO_DIARY_AGENT_NATIVE_TOOLS]
    .filter(Boolean).join(',').split(",").filter(name => name && name !== "codemode"));
  const proxy = new Proxy(agent, { get(target, property) {
    if (property !== "registerTool") {
      const value = Reflect.get(target, property);
      return typeof value === "function" ? value.bind(target) : value;
    }
    return (tool: any) => target.registerTool({...tool,
      description: "在 QuickJS 中组合本轮已接通的业务及原生工具。QuickJS本身没有 models、宿主文件、Shell、网络或计时器接口；文件和程序操作只能调用当前任务的真实工具。必须 await 每个调用；最多8次、30秒、2048输出token。脚本失败不撤销已返回回执，不盲重放未知结果。",
      parameters: {...tool.parameters, properties: {code: {type: "string", maxLength: 16000}}},
      execute: async (id: string, input: {code: string}, signal: AbortSignal | undefined, onUpdate: any, ctx: any) => {
        if (typeof input.code !== "string" || input.code.length > 16000) throw new Error("编排代码超过本轮允许范围");
        const receipts: any[] = [];
        let count = 0;
        const deadline = AbortSignal.timeout(30000);
        const boundedSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
        const boundedContext = {...ctx,
          tools: ctx.tools.filter((item: any) => allowed.has(item.name)),
          executeTool: async (name: string, args: unknown, options: any) => {
            if (!allowed.has(name) || name === "codemode") throw new Error("编排不能调用本轮未授权工具或递归编排");
            if (boundedSignal.aborted || ++count > 8) throw new Error("编排已停止或达到8次业务调用上限");
            const callSignal = options?.signal ? AbortSignal.any([boundedSignal, options.signal]) : boundedSignal;
            const outcome = await ctx.executeTool(name, args, {...options, signal: callSignal});
            receipts.push({tool_name: name, tool_call_id: outcome.toolCall.id, is_error: outcome.isError,
              result: outcome.result.structuredContent ?? outcome.result.details ?? outcome.result});
            if (outcome.isError) throw new Error(JSON.stringify(outcome.result));
            return outcome;
          },
        };
        // The upstream pragma is first-line-only. Replace it with host-owned limits.
        const newline = input.code.indexOf("\n");
        const firstLine = (newline < 0 ? input.code : input.code.slice(0, newline)).trimStart();
        const source = firstLine.startsWith("// @options:") ? (newline < 0 ? "" : input.code.slice(newline + 1)) : input.code;
        if (!source.trim()) throw new Error("编排缺少可执行代码");
        const result = await tool.execute(id, {code: '// @options: {"timeout_ms":30000,"max_output_tokens":2048}\n' + source},
          boundedSignal, onUpdate, boundedContext);
        const calls = result.details?.calls || [];
        const incomplete = calls.some((call: any) => call.status === "cancelled" || call.status === "running");
        const failed = receipts.some(receipt => receipt.is_error);
        const details = {...result.details, business_receipts: receipts, incomplete_calls: incomplete,
          limits: {calls: 8, timeout_ms: 30000, max_output_tokens: 2048, models: false}};
        return {...result, ...(incomplete || failed ? {isError: true,
          content: [...result.content.map((item: any) => item.type === "text" && item.text.startsWith("Script completed\n")
            ? {...item, text: item.text.replace("Script completed\n", "Script failed\n")} : item),
          {type: "text", text: "存在失败或未等待完成的业务调用；本轮编排未完成。已返回回执保留，未知结果不可盲重放。"}]} : {}),
          details, structuredContent: details};
      },
    });
  }});
  createCodemodeExtension({mode: "on", models: false})(proxy);
}
