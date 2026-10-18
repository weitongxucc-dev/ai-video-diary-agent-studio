#!/usr/bin/env node
import {homedir} from 'node:os';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {McpServer, ResourceTemplate} from '@modelcontextprotocol/sdk/server/mcp.js';
import {StdioServerTransport} from '@modelcontextprotocol/sdk/server/stdio.js';
import {z} from 'zod';
import {readConfig, scrub} from './yaohe/configuration.mjs';
import {client} from './yaohe/transport.mjs';
import {dispatch} from './yaohe/commands.mjs';
import {conversationRequest} from './yaohe/conversation-commands.mjs';
import {editRequest} from './yaohe/edit-commands.mjs';
import {CliError} from './yaohe/errors.mjs';

const objectId = z.string().regex(/^[A-Za-z0-9_-]{1,120}$/);
const requestId = z.string().uuid();
const version = z.number().int().nonnegative().safe();
const args = (positional,flags={}) => ({positional,flags});

export function createMcpServer(configPath) {
  const server = new McpServer({name:'yaohe',version:'0.11.0'}, {
    maxToolInputElements:10000,
    instructions:'通过当前绑定成员操作曜核。先读真实任务和能力。明确编辑可直接预检并应用；未知结果先按原请求查询，不换请求ID重试。会话发送前读取并保存草稿，保持版本与目标；断开MCP不会取消已提交制作。',
  });
  // Re-read the private binding each call. No tool accepts credentials, URLs,
  // workspace identity or a local file path from the model.
  async function invoke(operation) {
    let secrets=[];
    try {
      const config=await readConfig(configPath); secrets=config.secrets;
      const result=await operation(client(config));
      const data=scrub({code:result.code ?? 0,state:result.state ?? 'done',...result},secrets);
      return {content:[{type:'text',text:JSON.stringify(data)}],structuredContent:data,
        isError:[1,2,4].includes(data.code)};
    } catch (error) {
      const failure=error instanceof CliError ? error : new CliError('MCP未取得确定业务结果',4);
      const data=scrub({code:failure.code,state:failure.code===4?'unknown':'rejected',error:failure.message,...failure.details},secrets);
      return {isError:true,content:[{type:'text',text:JSON.stringify(data)}],structuredContent:data};
    }
  }
  const register=(name,description,shape,readOnly,operation)=>server.registerTool(name,{
    description,inputSchema:z.object(shape).strict(),
    annotations:{readOnlyHint:readOnly,destructiveHint:!readOnly,openWorldHint:false},
  },input=>invoke(api=>operation(api,input)));
  const command=(api,positional,flags={})=>dispatch(api,args(positional,flags));

  register('capabilities','读取实际能力；指定作品时包含编辑动作schema、可用状态和单位。',{
    task_id:objectId.optional()},true,(api,p)=>command(api,['capabilities','list'],p.task_id?{task:p.task_id}:{}));
  register('task_inspect','读取同一作品的当前文案、版本、素材与输出事实。',{
    task_id:objectId},true,(api,p)=>command(api,['task','inspect',p.task_id]));
  register('material_search','在当前成员可访问的素材中检索。相关性以实际响应为准。',{
    query:z.string().min(1).max(200),limit:z.number().int().min(1).max(100).optional(),
    role:z.enum(['a_roll','b_roll']).optional()},true,(api,p)=>command(api,['material','search'],{
      query:p.query,...(p.limit?{limit:String(p.limit)}:{}),...(p.role?{role:p.role}:{})}));
  register('material_inspect','读取素材和可引用镜头。',{
    asset_id:objectId},true,(api,p)=>command(api,['material','inspect',p.asset_id]));
  register('execution_status','读取原作品制作作业；没有产物不表示制作成功。',{
    task_id:objectId},true,(api,p)=>command(api,['execution','status',p.task_id]));
  register('execution_preview','读取真实制作预检及阻断，不启动制作。',{
    task_id:objectId},true,(api,p)=>command(api,['execution','preview',p.task_id]));
  register('job_inspect','按原作业ID读取结果，不切换到最新作业。',{
    task_id:objectId,job_id:objectId},true,(api,p)=>command(api,['job','inspect',p.task_id,p.job_id]));

  register('conversation_create','显式创建一个会话，不创建视频。响应丢失时不自动重发。',{
    scope_kind:z.enum(['home','material','task']),scope_id:objectId.optional(),title:z.string().max(200).optional()},false,
    (api,p)=>conversationRequest(api,args(['conversation','create']),p));
  register('conversation_inspect','读取会话、草稿和引用版本；不覆盖GUI草稿。',{
    conversation_id:requestId},true,(api,p)=>conversationRequest(api,args(['conversation','inspect',p.conversation_id])));
  register('conversation_draft','按期望版本保存要发送的文字；冲突时重新读取，不覆盖别人修改。',{
    conversation_id:requestId,draft_text:z.string().max(1000),expected_draft_version:version},false,
    (api,{conversation_id,...body})=>conversationRequest(api,args(['conversation','draft',conversation_id]),body));
  register('conversation_turn','发送已经保存的同一草稿，让曜核规划或修改。稳定请求ID用于断线回查；task_id明确绑定作品。',{
    conversation_id:requestId,request_id:requestId,task_id:objectId.optional(),message:z.string().min(2).max(1000),
    expected_draft_version:version,expected_reference_version:version,
    context_stage:z.enum(['topic','script','narration','montage','review','subtitle_cover']).optional(),
    expected_timeline_version_id:objectId.nullable().optional(),expected_timeline_sequence:version.optional(),
    selected_object_refs:z.array(z.record(z.unknown())).max(8).optional(),playhead_ms:version.optional(),
    timeout_seconds:z.number().int().min(1).max(300).optional()},false,
    (api,{conversation_id,request_id,task_id,timeout_seconds,...body})=>conversationRequest(api,
      args(['conversation','turn',conversation_id],{'request-id':request_id,...(task_id?{task:task_id}:{}),
        ...(timeout_seconds?{'timeout-seconds':String(timeout_seconds)}:{})}),body));
  register('conversation_request','按原会话和请求ID查询发送回执；不会重发或用最新回合冒充原结果。',{
    conversation_id:requestId,request_id:requestId},true,(api,p)=>conversationRequest(api,args(['conversation','request',p.conversation_id,p.request_id])));

  register('edit_preview','按能力目录中的动作schema预检精确编辑；使用调用方提供的期望版本。返回原提案和请求回执。',{
    task_id:objectId,request_id:requestId,action:z.record(z.unknown()).refine(p=>typeof p.operation_type==='string'),
    expected_timeline_version_id:objectId.nullable(),expected_timeline_sequence:version},false,
    (api,p)=>editRequest(api,args(['edit','preview',p.task_id],{'request-id':p.request_id}),()=>({
      ...p.action,expected_timeline_version_id:p.expected_timeline_version_id,expected_timeline_sequence:p.expected_timeline_sequence})));
  register('edit_apply','应用已预检且范围明确的编辑；核对提案原版本、输入摘要与稳定请求ID。',{
    task_id:objectId,proposal_id:objectId,request_id:requestId},false,
    (api,p)=>editRequest(api,args(['edit','apply',p.task_id,p.proposal_id],{'request-id':p.request_id})));
  register('edit_request','查询精确编辑原请求的回执。',{
    task_id:objectId,request_id:requestId,phase:z.enum(['preview','apply','script'])},true,
    (api,p)=>command(api,['request','query',p.task_id,p.request_id],{phase:p.phase}));

  server.registerResource('task',new ResourceTemplate('yaohe://tasks/{taskId}',{list:undefined}),{
    description:'受当前绑定成员权限约束的作品事实',mimeType:'application/json'},async(uri,{taskId})=>{
      const checked=objectId.parse(taskId);
      const result=await invoke(api=>command(api,['task','inspect',checked]));
      if(result.isError) throw new Error(result.content[0].text);
      return {contents:[{uri:uri.href,mimeType:'application/json',text:result.content[0].text}]};
    });
  return server;
}

async function main() {
  const argv=process.argv.slice(2);
  if(argv.length && (argv.length!==2 || argv[0]!=='--config')) throw new Error('用法：yaohe-mcp [--config 私有配置路径]');
  const path=argv[1] || resolve(homedir(),'.config/yaohe/config.json');
  await readConfig(path);
  await createMcpServer(path).connect(new StdioServerTransport());
}
if(process.argv[1] && fileURLToPath(import.meta.url)===resolve(process.argv[1])) {
  main().catch(()=>{process.stderr.write('曜核MCP启动失败：请检查私有配置、依赖与命令参数。\n');process.exitCode=1;});
}
