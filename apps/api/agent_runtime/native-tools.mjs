import {AsyncLocalStorage} from 'node:async_hooks';
import {createHash} from 'node:crypto';

/** SDK tools keep their editing/rendering behavior; every operation crosses
 * the existing host pipe. This module never opens task files or spawns a shell.
 * The native owner supplies filesystem, process and device authorization.
 */
export function createNativeTaskTools({sdk,callNative,turnId,inputId=turnId}) {
  if (!callNative || !turnId) throw new Error('原生工具缺少可信宿主与原轮次');
  const calls=new AsyncLocalStorage();
  let currentInput=inputId,round=0;
  const unresolvedCommands=new Map();
  const unresolvedRegistrations=new Map();
  const unresolvedFiles=new Map();
  const hash=value=>createHash('sha256').update(value).digest('hex');
  const textMime=mime=>Boolean(mime?.startsWith('text/') || ['application/json','application/javascript','application/xml','application/x-subrip','application/yaml','image/svg+xml'].includes(mime));
  const identity=(_id,kind)=>hash(`${calls.getStore().key}:${kind}`);
  function requireCommandBudget() {
    if (calls.getStore().context.command_budget.status!=='ready') {
      const error=new Error('当前通用执行资源档不可用，未提交程序；文件操作与已有产物保留');
      error.failure={reason:error.message,submission_effect:'not_submitted'};
      throw error;
    }
  }
  async function request(operation,args,signal=calls.getStore().signal) {
    const call=calls.getStore();
    const fileMutation=operation==='native_task_file' && ['write','mkdir'].includes(args.action);
    if (fileMutation && args.action==='mkdir' && call.recoveredMkdir===args.path) return call.fileReceipt;
    const callId=`native:${call.key}:${++call.sequence}`;
    if (fileMutation) {
      call.fileReceipt={request_id:callId,action:args.action,path:args.path};
      unresolvedFiles.set(call.fileKey,call.fileReceipt);
    }
    const result=await callNative(operation,args,{callId,toolCallId:call.id,signal});
    if (!result.ok) {
      if (fileMutation && result.failure.submission_effect==='not_submitted') unresolvedFiles.delete(call.fileKey);
      const error=new Error(result.failure.reason);
      error.failure=result.failure;error.operation=operation;
      throw error;
    }
    if (fileMutation) unresolvedFiles.delete(call.fileKey);
    return result.value;
  }
  async function access(path) {
    const value=await request('native_task_file',{action:'access',path});
    calls.getStore().files.set(path,value);
    return value;
  }
  async function readFile(path) {
    const call=calls.getStore(),meta=call.files.get(path) ?? await access(path);
    if (call.name==='edit') {
      if (!meta.writable || meta.sha256===null || meta.size_bytes>call.context.capabilities.max_editable_file_bytes)
        throw new Error('此文件不能在当前轻量编辑范围内完整读取和比较；原文件未修改');
      const chunks=[];
      for (let offset=0;offset<meta.size_bytes;) {
        const value=await request('native_task_file',{action:'read',path,offset,
          limit:Math.min(call.context.capabilities.max_read_bytes,meta.size_bytes-offset)});
        if (value.sha256!==meta.sha256 || value.next_offset<=offset) throw new Error('文件版本或读取进度发生变化；未提交编辑');
        chunks.push(Buffer.from(value.content_base64,'base64'));offset=value.next_offset;
      }
      return Buffer.concat(chunks);
    }
    const params={action:'read',path,limit:Math.min(call.context.capabilities.max_read_bytes,sdk.DEFAULT_MAX_BYTES)};
    params.line_limit=Math.min(call.params.limit ?? sdk.DEFAULT_MAX_LINES,sdk.DEFAULT_MAX_LINES);
    if (call.params.byte_offset!==undefined) params.offset=call.params.byte_offset;
    else params.line_offset=call.params.offset ?? 1;
    const value=await request('native_task_file',params);
    call.window={path,offset:value.offset,next_offset:value.next_offset,
      actual_line_offset:value.actual_line_offset,next_line_offset:value.next_line_offset,
      truncated:value.truncated,file_revision:value.file_revision};
    return Buffer.from(value.content_base64,'base64');
  }
  async function writeFile(path,content) {
    const call=calls.getStore(),meta=call.files.get(path) ?? await access(path);
    if (!meta.writable || meta.exists && meta.sha256===null) throw new Error('文件不在可写范围或缺少可比较的原版本');
    const receipt=await request('native_task_file',{action:'write',path,
      content_base64:Buffer.from(content,'utf8').toString('base64'),expected_sha256:meta.sha256});
    call.fileReceipt={...call.fileReceipt,...receipt};
  }
  const read=sdk.createReadToolDefinition('/task/work',{operations:{readFile,
    access:async path=>{if (!(await access(path)).readable) throw new Error('文件不存在或当前授权不可读');},
    detectImageMimeType:async path=>{
      const mime=calls.getStore().files.get(path).mime_type;
      if (['image/png','image/jpeg','image/gif','image/webp'].includes(mime))
        throw new Error('此文字模型会话未获图片外发许可；请沿本地局部分析或作品预览检查，不能把原始图片自动发给模型');
      if (!textMime(mime))
        throw new Error('二进制媒体不直接发送给文字模型；可在任务许可目录用程序处理并登记结果');
      return null;
    }}});
  read.parameters={...read.parameters,properties:{...read.parameters.properties,byte_offset:{type:'integer',minimum:0}}};
  const write=sdk.createWriteToolDefinition('/task/work',{operations:{writeFile,
    mkdir:async path=>{await request('native_task_file',{action:'mkdir',path});}}});
  const edit=sdk.createEditToolDefinition('/task/work',{operations:{readFile,writeFile,
    access:async path=>{
      const meta=await access(path);
      if (!meta.writable) throw new Error('文件不存在或当前授权不可修改');
      if (!textMime(meta.mime_type)) throw new Error('二进制媒体不能作为文字读取或编辑；请用任务许可的本地程序处理');
    }}});
  const bash=sdk.createBashToolDefinition('/task/work',{exposeSessionEnvironment:false,operations:{
    exec:async(command,cwd,{onData,signal,timeout})=>{
      const call=calls.getStore();
      const commandKey=JSON.stringify([call.input,command,cwd,call.params.declared_outputs ?? []]);
      const original=unresolvedCommands.get(commandKey);
      if (!original) requireCommandBudget();
      const request_id=original ?? identity(call.id,'command');
      call.command={request_id};
      unresolvedCommands.set(commandKey,request_id);
      try {
        if (!original) call.command=await request('submit_task_command',{request_id,command,cwd,
          ...(timeout===undefined?{}:{timeout_seconds:timeout}),declared_outputs:call.params.declared_outputs ?? []});
        let receipt;
        do {
          receipt=await request('read_task_command',{request_id,offset:0,
            limit:Math.min(call.context.capabilities.max_log_page_bytes,sdk.DEFAULT_MAX_BYTES)});
          call.command=receipt;
          if (['queued','running','waiting','cancel_requested'].includes(receipt.status))
            await new Promise(resolve=>setTimeout(resolve,200));
          else break;
        } while (!signal?.aborted);
        if (signal?.aborted) throw new Error('aborted');
        if (receipt.status==='failed') {
          const released=['released','not_acquired'].includes(receipt.release_state);
          if (released) unresolvedCommands.delete(commandKey);
          call.executionFailure={error:'native_command_failed',failure_category:'execution_failed',stage:'native_execution',
            reason:receipt.failure_reason || '原程序执行已记录失败，请查询该作业的具体回执。',
            repair_action:(receipt.repair_action || '先读取原执行回执，修复已确认的原因后再决定下一步；不要原样重放命令。')
              +(released?'':' 进程退出或资源释放尚未证实，先沿原请求查询，不能视为已经释放。'),
            request_id,job_id:receipt.job_id,query_tool:'read_task_command',
            submission_effect:receipt.submission_effect ?? 'unknown'};
          throw new Error(call.executionFailure.reason);
        }
        if (!['released','not_acquired'].includes(receipt.release_state)) throw new Error('原进程或资源释放尚未证实；请回读原请求，不重放命令');
        unresolvedCommands.delete(commandKey);
        onData(Buffer.from(receipt.output,'utf8'));
        return {exitCode:receipt.exit_code};
      } catch(error) {
        if (error.operation==='submit_task_command' && error.failure?.submission_effect==='not_submitted') unresolvedCommands.delete(commandKey);
        if (signal?.aborted) {
          // Tool-local cancellation can still use the existing pipe. Whole
          // session abort is also handled by the host lifecycle cancel port.
          try {call.command=await request('cancel_task_command',{request_id},new AbortController().signal);}
          catch { /* The host retains the original request and cancels its owned jobs. */ }
        }
        throw error;
      }
    },
  }});
  bash.parameters={...bash.parameters,properties:{...bash.parameters.properties,declared_outputs:{type:'array',items:{
    type:'object',properties:{path:{type:'string'},role:{type:'string'}},required:['path','role'],additionalProperties:false}}}};
  const object=(properties,required)=>({type:'object',properties,required,additionalProperties:false});
  const reference={request_id:{type:'string'},job_id:{type:'string'}};
  const helpers=[
    {name:'open_task_working_context',label:'读取任务工作目录',description:'读取当前任务许可输入、可写目录、原生操作能力与资源状态。input_refs只映射本轮工具读到的真实授权source_ref，以文件名关联；不猜路径、不自动复制全库。',
      parameters:object({input_refs:{type:'array',maxItems:30,items:object({name:{type:'string'},source_ref:{type:'string'}},['name','source_ref'])},
        available_offset:{type:'integer',minimum:0},available_limit:{type:'integer',minimum:1,maximum:100}},[]),
      execute:async()=>{
        const value=calls.getStore().context;
        return {content:[{type:'text',text:JSON.stringify(value)}],details:value};
      }},
    {name:'read_task_command',label:'回读原程序执行',description:'沿原请求或作业身份分页查询真实输出、退出与释放；找不到或结果未知不代表未提交，不重放。',
      parameters:object({...reference,offset:{type:'integer',minimum:0},limit:{type:'integer',minimum:1}},[]),
      execute:async(_id,params)=>{
        const value=await request('read_task_command',{...params,offset:params.offset ?? 0,
          limit:Math.min(params.limit ?? sdk.DEFAULT_MAX_BYTES,calls.getStore().context.capabilities.max_log_page_bytes)});
        return {content:[{type:'text',text:JSON.stringify(value)}],details:value};
      }},
    {name:'cancel_task_command',label:'停止原程序执行',description:'只请求停止原执行并读取实际状态；cancel_requested不代表进程退出或资源已释放。',parameters:object(reference,[]),
      execute:async(_id,params)=>{const value=await request('cancel_task_command',params);return {content:[{type:'text',text:JSON.stringify(value)}],details:value};}},
    {name:'register_task_artifacts',label:'提交产物登记',description:'将原执行生成的文件按实际格式和摘要登记到当前作品。queued/running只是登记作业，须沿原Job用read_task_command回读，拿到实际artifacts后才有候选预览。登记、候选预览、采用与最终成片分别判断；图片可沿已有图层入口采用。',
      parameters:object({execution_job_id:{type:'string'},files:{type:'array',minItems:1,items:object({path:{type:'string'},role:{type:'string'},expected_sha256:{type:'string'}},['path','role','expected_sha256'])}},['execution_job_id','files']),
      execute:async(id,params)=>{
        const call=calls.getStore(),key=JSON.stringify([call.input,params.execution_job_id,params.files]);
        const original=unresolvedRegistrations.get(key),request_id=original ?? identity(id,'artifacts');
        if (!original) requireCommandBudget();
        unresolvedRegistrations.set(key,request_id);call.command={request_id};
        try {
          let value=original?await request('read_task_command',{request_id,offset:0,limit:call.context.capabilities.max_log_page_bytes})
            :await request('register_task_artifacts',{...params,request_id});
          call.command=value;
          while (['queued','running','waiting'].includes(value.status)) {
            await new Promise(resolve=>setTimeout(resolve,200));
            value=await request('read_task_command',{request_id,offset:0,limit:call.context.capabilities.max_log_page_bytes});call.command=value;
          }
          if (value.status==='succeeded' && value.artifacts?.length) unresolvedRegistrations.delete(key);
          return {isError:value.status!=='succeeded' || !value.artifacts?.length,
            content:[{type:'text',text:JSON.stringify(value)}],details:value};
        } catch(error) {
          if (error.operation==='register_task_artifacts' && error.failure?.submission_effect==='not_submitted') unresolvedRegistrations.delete(key);
          throw error;
        }
      }},
  ];
  const labels={read:'读取工作文件',write:'写入工作文件',edit:'修改工作文件',bash:'执行任务程序'};
  const tools=[read,write,edit,bash,...helpers].map(tool=>({...tool,label:labels[tool.name] ?? tool.label,
    description:tool.description+(tool.name==='read'?' 文件从源端分页；offset为原文件行号，byte_offset可继续上一窗口。结果中的窗口和截断不等于完整文件。':
      tool.name==='bash'?' 在当前任务许可目录执行自行组合的命令，不继承模型或账号密钥；Shell使用当前cwd和../inputs、../outputs，/task是文件API虚拟路径；声明输出后登记真实产物，命令成功不代表已采用。':''),
    execute:async(id,params,signal,onUpdate,ctx)=>calls.run({id,key:hash(`${turnId}:${currentInput}:${round}:${id}`),input:currentInput,
      name:tool.name,params,signal:signal ?? new AbortController().signal,sequence:0,files:new Map()},async()=>{
      const call=calls.getStore();
      call.fileKey=JSON.stringify([call.input,tool.name,params.path,params.content,params.edits?.map(edit=>[edit.oldText,edit.newText])]);
      const pendingFile=unresolvedFiles.get(call.fileKey);
      if (pendingFile) call.fileReceipt=pendingFile;
      try {
        call.context=await request('open_task_working_context',tool.name==='open_task_working_context'?params:{});
        if (pendingFile) {
          const receipt=await request('read_task_command',{request_id:pendingFile.request_id,offset:0,
            limit:call.context.capabilities.max_log_page_bytes});
          call.fileReceipt={...pendingFile,...receipt};
          if (receipt.status!=='succeeded' || pendingFile.action==='write') {
            if (receipt.status==='succeeded' || receipt.submission_effect==='not_submitted') unresolvedFiles.delete(call.fileKey);
            return {isError:receipt.status!=='succeeded',content:[{type:'text',text:JSON.stringify(call.fileReceipt)}],
              details:{native_execution:call.fileReceipt}};
          }
          unresolvedFiles.delete(call.fileKey);call.recoveredMkdir=pendingFile.path;
        }
        const result=await tool.execute(id,tool.name==='read'?{path:params.path,offset:1}:params,signal,onUpdate,{...ctx,cwd:'/task/work'});
        // The SDK may catch operations.exec errors. Its generic missing-exit-code
        // message must not overwrite the owner's confirmed failure receipt.
        if (call.executionFailure) throw new Error(call.executionFailure.reason);
        const native=call.command?{request_id:call.command.request_id ?? identity(id,'command'),job_id:call.command.job_id,
          status:call.command.status,process_state:call.command.process_state,release_state:call.command.release_state,truncated:call.command.truncated}:call.fileReceipt;
        const note=call.window?`原文件窗口：${JSON.stringify(call.window)}。窗口外未读取不代表不存在。`:
          native?`原执行回执：${JSON.stringify(native)}`:null;
        const content=note?[...result.content,{type:'text',text:note}]:result.content;
        let structured=result.structuredContent;
        if (call.window && typeof structured==='string') structured+='\n'+note;
        if (call.command && structured && typeof structured==='object') structured={...structured,
          output:structured.output+'\n'+note,truncated:Boolean(call.command.truncated)};
        return {...result,content,details:{...result.details,native_execution:native,read_window:call.window},
          ...(structured===undefined?{}:{structuredContent:structured})};
      } catch(error) {
        const failure=call.executionFailure ?? error.failure ?? {reason:error.message,submission_effect:call.command || call.fileReceipt?'unknown':'not_submitted'};
        const details={failure:error.operation==='read_task_command' && (call.command || call.fileReceipt)
          ?{...failure,submission_effect:'unknown'}:failure,
          native_execution:call.command ?? call.fileReceipt};
        return {isError:true,content:[{type:'text',text:JSON.stringify(details)}],details};
      }
    }),
  }));
  return {tools,beginRound:()=>{round++;},rebind:environment=>{currentInput=environment.AI_VIDEO_DIARY_AGENT_INPUT_ID || turnId;}};
}
