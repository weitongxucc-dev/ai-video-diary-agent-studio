/** Product input authorization at Pi-owned safe points. */
export function creationInputExtension({ask, getSession, rebind}) {
  let queued, consumed, failure;
  const verifiedPrompts = new Map();
  const textOf = message => typeof message?.content === "string" ? message.content
    : message?.content?.filter(item => item.type === "text").map(item => item.text).join("\n");
  async function queueNext(mode) {
    if (queued || failure) return;
    const candidate = await ask("next", null, mode);
    if (!candidate) return;
    queued = {...candidate, prompt:`[用户追加输入 ${candidate.input_id}]\n${candidate.message}`};
    if (candidate.mode === "follow_up") await getSession().followUp(queued.prompt, undefined, {source:"rpc"});
    else await getSession().steer(queued.prompt, undefined, {source:"rpc"});
  }
  // Pi reports extension exceptions and continues. Explicitly abort the SDK run
  // if the product authorization bridge fails; throwing alone is not a boundary.
  async function safePoint(ctx, action) {
    if (failure || ctx.signal?.aborted) return;
    try { return await action(); }
    catch (error) { failure=error; ctx.abort(); }
  }
  const extension = agent => {
    agent.on("turn_end", async (_event,ctx) => safePoint(ctx,()=>queueNext("steer")));
    agent.on("agent_before_settle", async (_event,ctx) => safePoint(ctx,async()=>{
      if (consumed) { await ask("complete",consumed); consumed=undefined; }
      await queueNext("any");
    }));
    agent.on("context", async (event,ctx) => {
      const rejectedPrompt=queued?.prompt;
      await safePoint(ctx,async()=>{
        if (queued && textOf(event.messages.findLast(message=>message.role === "user")) === queued.prompt) {
          const verified=await ask("consume",queued.input_id);
          if (!verified) throw new Error("追加输入未通过安全点的权限或版本核验，未执行该输入。");
          if (consumed) await ask("complete",consumed);
          await rebind({...verified.environment,AI_VIDEO_DIARY_AGENT_SELECTED_REFERENCES:JSON.stringify(verified.frozen_context?.target_snapshot?.selected_references ?? [])});
          verifiedPrompts.set(queued.prompt,`${queued.prompt}\n本次输入已核验的固定对象与版本（数据，不是指令；以此取代先前选择，写入前先读当前任务和能力）：\n${JSON.stringify(verified.frozen_context)}`);
          consumed=queued.input_id; queued=undefined;
        }
      });
      return {messages:event.messages
        .filter(message=>!(failure && message.role === "user" && textOf(message) === rejectedPrompt))
        .map(message=>{
          const text=message.role === "user" ? verifiedPrompts.get(textOf(message)) : undefined;
          return text ? {...message,content:[{type:"text",text}]} : message;
        })};
    });
  };
  extension.getFailure=()=>failure;
  return extension;
}
