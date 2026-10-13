#!/usr/bin/env node
import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {homedir} from 'node:os';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const HELP = `曜核 CLI — Node >=22，普通业务 API 消费者
  capabilities list [--task TASK]
  capabilities describe OPERATION --task TASK
  task inspect TASK
  material inspect ASSET
  material search --query TEXT [--role a_roll|b_roll] [--limit 20]
  tool capabilities|list
  tool describe TOOL [--version N]
  tool save TASK --file tool.json --request-id UUID
  tool request UUID
  tool update TOOL --file update.json
  tool revoke TOOL --expected-tool-version N
  tool preview TASK TOOL --file invocation.json --request-id UUID
  script capabilities TASK
  script sources TASK
  script submit TASK --file script.json --request-id UUID
  edit preview TASK --file edit.json --request-id UUID
  edit apply TASK PROPOSAL --request-id UUID
  request query TASK UUID [--phase preview|apply|script]
  job inspect|wait|cancel TASK JOB [--timeout-seconds 30]
  timeline undo|redo TASK [--expected-version VERSION --expected-sequence N]
全局：--config FILE（默认 ~/.config/yaohe/config.json，POSIX mode 0600）
预览版本可写入 edit.json；缺少时只读一次当前任务绑定。
音频/画面动作也用 edit preview/apply，字段以 capabilities describe 的共同API schema为准：EditAudioTimeline提交audio_edit(action/target_id/values)，AdjustVisualEffects提交clip_ids/effects。values/effects只写非空指定patch，遗漏保持；音频淡入淡出毫秒、画面秒、gain_db绝对dB。solo持久化、音量关键点写入和独立音频变速未开放。采用保存草稿，不自动出片。
脚本仅消费API的Python/Pillow单PNG来源预览；不自动应用或出片。配方命令复用普通API；不继承旧任务授权，不自动应用。扩展加载和导出尚未接入。
退出：0 已完成；1 API/终态失败；2 参数/配置；3 尚未完成；4 结果未知。
`;

class CliError extends Error {
  constructor(message, code = 2, details = {}) { super(message); this.code = code; this.details = details; }
}

function parse(args) {
  const flags = {}, positional = [];
  const allowed = new Set(['config','task','file','request-id','phase','query','role','limit',
    'timeout-seconds','expected-version','expected-sequence','version','expected-tool-version']);
  for (let i = 0; i < args.length; i++) {
    const value = args[i];
    if (value === '--help' || value === '-h') return {help: true};
    if (!value.startsWith('--')) { positional.push(value); continue; }
    const name = value.slice(2);
    if (!allowed.has(name) || name in flags || !args[i+1] || args[i+1].startsWith('--')) {
      throw new CliError('未知、重复或缺少值的选项；不接受凭据参数');
    }
    flags[name] = args[++i];
  }
  return {flags, positional};
}

function id(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,120}$/.test(value)) throw new CliError('需要明确有效的对象 ID');
  return encodeURIComponent(value);
}
function uuid(value) {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    throw new CliError('需要稳定的 UUID request-id');
  }
  return value.toLowerCase();
}
function number(value, fallback, max) {
  const n = value === undefined ? fallback : Number(value);
  if (!Number.isFinite(n) || n <= 0 || n > max) throw new CliError('数值选项必须是有界正数');
  return n;
}

async function boundedJson(file, limit = 65536) {
  const buffer = Buffer.alloc(limit + 1);
  let size = 0;
  while (size < buffer.length) {
    const {bytesRead} = await file.read(buffer,size,buffer.length-size,size);
    if (!bytesRead) break;
    size += bytesRead;
  }
  if (size > limit) throw new CliError('JSON文件超过读取上限');
  return JSON.parse(buffer.subarray(0,size).toString('utf8'));
}

export async function readConfig(path) {
  let file;
  try {
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > 65536) throw new CliError('配置须是有界普通文件');
    if (process.platform !== 'win32' && ((stat.mode & 0o7777) !== 0o600 || stat.uid !== process.getuid())) {
      throw new CliError('配置须由当前用户拥有且权限为0600');
    }
    let config;
    try { config = await boundedJson(file); }
    catch { throw new CliError('配置 JSON 无效'); }
    const fields = new Set(['api_base','local_member_token','authorization','cookie']);
    if (!config || Array.isArray(config) || typeof config !== 'object' || Object.keys(config).some(k => !fields.has(k))) {
      throw new CliError('配置仅支持 API 地址与既有身份字段');
    }
    let base;
    try { base = new URL(config.api_base); } catch { throw new CliError('配置 API 地址无效'); }
    if (!['http:','https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== '/') {
      throw new CliError('API 地址须是不含凭据、查询或路径的 HTTP(S) origin');
    }
    const headers = {};
    for (const [key, header] of [['local_member_token','X-Local-Member-Token'],['authorization','Authorization'],['cookie','Cookie']]) {
      if (config[key] !== undefined) {
        if (typeof config[key] !== 'string' || !config[key].trim() || /[\r\n]/.test(config[key])) throw new CliError('身份配置无效');
        headers[header] = config[key];
      }
    }
    if (!Object.keys(headers).length) throw new CliError('配置缺少既有成员或会话身份');
    const secrets = Object.values(headers);
    if (config.authorization) secrets.push(config.authorization.replace(/^Bearer\s+/i,''));
    if (config.cookie) secrets.push(...config.cookie.split(';').map(v => v.slice(v.indexOf('=')+1).trim()).filter(Boolean));
    return {base: base.origin, headers, secrets};
  } catch (error) {
    if (error instanceof CliError) throw error;
    throw new CliError('无法安全读取私有配置文件（不存在、符号链接或无权限）');
  } finally { await file?.close(); }
}

function scrub(value, secrets) {
  if (typeof value === 'string') {
    for (const secret of secrets.filter(Boolean).sort((a,b) => b.length-a.length)) value = value.split(secret).join('[redacted]');
    return value;
  }
  if (Array.isArray(value)) return value.map(v => scrub(v,secrets));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [scrub(k,secrets),scrub(v,secrets)]));
  return value;
}

export function outcome(data) {
  if (data?.status === 'recorded') {
    if (data.result_status_code >= 400) return {code:1,state:'rejected'};
    if (!data.result) return {code:4,state:'unknown'};
    return outcome(data.result);
  }
  if (['unknown','result_unknown'].includes(data?.status)) return {code:4,state:'unknown'};
  if (['queued','running','interrupted','stopping'].includes(data?.status)) return {code:3,state:'pending'};
  if (['failed','cancelled','stale','rejected'].includes(data?.status)) return {code:1,state:'failed'};
  return {code:0,state:'done'};
}

function client(config) {
  return async (path, {method='GET',body,timeout=15000} = {}) => {
    let response;
    try {
      response = await fetch(new URL(path,config.base), {method, headers:{...config.headers,'Accept':'application/json',
        ...(body === undefined ? {} : {'Content-Type':'application/json'})},
        body:body === undefined ? undefined : JSON.stringify(body), redirect:'manual', signal:AbortSignal.timeout(Math.max(1,Math.ceil(timeout)))});
    } catch { throw new CliError('业务 HTTP 请求未取得确定结果',4); }
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      throw new CliError('业务 HTTP 重定向已拒绝，未向新地址发送身份',4,{http_status:response.status});
    }
    const reader = response.body?.getReader();
    let bytes = 0, chunks = [];
    if (reader) {
      try {
        for (;;) {
          const {done,value} = await reader.read();
          if (done) break;
          bytes += value.length;
          if (bytes > 1048576) { await reader.cancel(); throw new CliError('业务响应超过读取上限',4); }
          chunks.push(Buffer.from(value));
        }
      } catch (error) { if (error instanceof CliError) throw error; throw new CliError('业务响应未完整读取',4); }
    }
    let data;
    try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch { throw new CliError('业务响应不是完整 JSON',4,{http_status:response.status}); }
    if (!response.ok) throw new CliError('业务 API 拒绝或未返回确定结果',response.status >= 500 ? 4 : 1,
      {http_status:response.status,data});
    return data;
  };
}

const taskPath = task => `/api/creation-tasks/${id(task)}`;
const requestPath = (task,request,phase) => `${taskPath(task)}/assistant/edit-requests/${uuid(request)}?phase=${phase}`;

async function receipt(api,path) {
  try { return await api(path); }
  catch (error) { if (error.details?.http_status === 404) return null; throw error; }
}
function knownReceipt(value,path) {
  if (!['recorded','unknown'].includes(value?.status)) throw new CliError('请求回执无法核验',4,{request_locator:path});
  return {...outcome(value),data:value,request_locator:path,replayed:true,submitted:false};
}
async function submit(api,path,body,locator,kind) {
  try {
    const data = await api(path,{method:'POST',body});
    if (kind === 'preview' && !data?.proposal?.id && !data?.job_id) throw new CliError('预览响应缺少提案或作业回执',4);
    if (kind === 'script' && (!data?.job_id || data.result_kind !== 'graphic_source')) throw new CliError('程序响应缺少真实图形来源作业回执',4);
    if (kind === 'apply' && !data?.operation?.id && !data?.job_id) throw new CliError('应用响应缺少操作或作业回执',4);
    return {...outcome(data),data,request_locator:locator,submitted:true};
  } catch (error) {
    if (error.code !== 4) throw new CliError(error.message,error.code,{...error.details,request_locator:locator});
    // At most one read-back. An absent/unknown receipt never authorizes a rePOST.
    let queryState = 'not_recorded';
    try { const previous = await receipt(api,locator); if (previous) return {...knownReceipt(previous,locator),submitted:true,recovered:true}; }
    catch { queryState = 'query_unavailable'; }
    throw new CliError('提交结果未知；仅查询原请求，不自动重发',4,{...error.details,request_locator:locator,query_state:queryState});
  }
}

async function version(api,task,source) {
  const a = 'expected_timeline_version_id', b = 'expected_timeline_sequence';
  if ((a in source) !== (b in source)) throw new CliError('期望版本与序号必须一起提供');
  if (a in source) return source;
  const current = (await api(taskPath(task))).task;
  if (!current || !Object.hasOwn(current,'current_timeline_version_id') || !Number.isInteger(current.timeline_sequence)) {
    throw new CliError('任务响应缺少可绑定时间线版本',4);
  }
  return {...source,[a]:current.current_timeline_version_id,[b]:current.timeline_sequence};
}

async function dispatch(api,args) {
  const {positional:p,flags:f} = args;
  const [group,command,task,object] = p;
  const check = (length,allowed) => {
    if (p.length !== length || Object.keys(f).some(k => k !== 'config' && !allowed.includes(k))) throw new CliError('命令参数不匹配，请查看 --help');
  };
  if (group === 'capabilities') {
    if (command === 'list') { check(2,['task']); return {data:await api(f.task ? `${taskPath(f.task)}/assistant/edit-capabilities` : '/api/assistant/capabilities')}; }
    if (command === 'describe') {
      check(3,['task']); if (!f.task) throw new CliError('describe 需要明确 --task');
      const caps = await api(`${taskPath(f.task)}/assistant/edit-capabilities`);
      const operation = caps.operations?.find(item => item.operation_type === task);
      if (!operation) throw new CliError('当前任务能力目录没有该动作',1);
      return {data:{task_id:f.task,operation,schema:caps.schema,units:caps.units}};
    }
  }
  if (group === 'task' && command === 'inspect') { check(3,[]); return {data:await api(taskPath(task))}; }
  if (group === 'material') {
    if (command === 'inspect') { check(3,[]); return {data:await api(`/api/production/materials/${id(task)}`)}; }
    if (command === 'search') {
      check(2,['query','role','limit']); if (!f.query) throw new CliError('素材搜索需要 --query');
      if (f.role && !['a_roll','b_roll'].includes(f.role)) throw new CliError('素材用途无效');
      const limit = number(f.limit,20,100); if (!Number.isInteger(limit)) throw new CliError('limit须为整数');
      const query = new URLSearchParams({q:f.query,limit:String(limit),...(f.role ? {usage_role:f.role} : {})});
      return {data:await api('/api/production/materials/search?'+query)};
    }
  }
  if (group === 'tool') {
    if (command === 'capabilities') {check(2,[]);return {data:await api('/api/media-script-tools/capabilities')};}
    if (command === 'list') {check(2,[]);return {data:await api('/api/media-script-tools')};}
    if (command === 'describe') {
      check(3,['version']);
      const query=f.version ? `?version=${number(f.version,1,Number.MAX_SAFE_INTEGER)}` : '';
      return {data:await api(`/api/media-script-tools/${id(task)}${query}`)};
    }
    if (command === 'request') {
      check(3,[]);const locator=`/api/media-script-tools/requests/${uuid(task)}`;
      try {return {data:await api(locator),request_locator:locator,submitted:false};}
      catch(error){throw new CliError(error.message,error.code,{...error.details,request_locator:locator});}
    }
    if (command === 'revoke') {
      check(3,['expected-tool-version']);
      if (!f['expected-tool-version']) throw new CliError('停用需要明确expected-tool-version');
      return {data:await api(`/api/media-script-tools/${id(task)}/revoke`,{method:'POST',
        body:{expected_version:number(f['expected-tool-version'],1,Number.MAX_SAFE_INTEGER)}})};
    }
    if (['save','update','preview'].includes(command)) {
      check(command==='preview'?4:3,command==='update'?['file']:['file','request-id']);
      if (!f.file) throw new CliError('配方动作需要 --file JSON');
      let value,file;
      try {file=await open(f.file,constants.O_RDONLY|constants.O_NONBLOCK);
        const stat=await file.stat();if(!stat.isFile()||stat.size>262144)throw new CliError('配方JSON超过读取上限');
        value=await boundedJson(file,262144);
      } catch{throw new CliError('配方动作文件须是有界完整JSON');}finally{await file?.close();}
      if(!value||Array.isArray(value)||typeof value!=='object'||
          ['request_id','assistant_turn_id','workspace_id','member_id','task_id','runtime_paths','script_source','requested_outputs'].some(key=>Object.hasOwn(value,key)))
        throw new CliError('配方动作仅使用API元数据/参数/输入字段，不提供身份、程序代码或输出槽');
      if(command==='update')return {data:await api(`/api/media-script-tools/${id(task)}`,{method:'PATCH',body:value})};
      const requestId=uuid(f['request-id']);
      if(command==='save'){
        const locator=`/api/media-script-tools/requests/${requestId}`;
        const previous=await receipt(api,locator);
        if(previous&&!previous.tool_id)return {code:4,state:'unknown',data:previous,request_locator:locator,submitted_input:value,submitted:false};
        try{return {data:await api(`${taskPath(task)}/media-script-tools`,{method:'POST',body:{...value,request_id:requestId}}),request_locator:locator,submitted:true};}
        catch(error){
          if(error.code!==4)throw new CliError(error.message,error.code,{...error.details,request_locator:locator});
          let original=null;
          try{original=await receipt(api,locator);}catch{/* Query failure stays unknown; never rePOST. */}
          throw new CliError('配方保存结果未知；仅保留原UUID查询，不用旧元数据冒充本次成功',4,
            {request_locator:locator,submitted_input:value,original_result:original});
        }
      }
      const locator=requestPath(task,requestId,'script');
      const previous=await receipt(api,locator);
      if(previous?.status==='unknown')return {...knownReceipt(previous,locator),submitted_input:value};
      let body;
      if(previous&&!Object.hasOwn(value,'expected_timeline_version_id')&&!Object.hasOwn(value,'expected_timeline_sequence')){
        const original=previous.submitted_input;
        if(!original||!Object.hasOwn(original,'expected_timeline_version_id')||!Number.isSafeInteger(original.expected_timeline_sequence))
          throw new CliError('原调用版本不可核验，不换绑当前任务',4,{request_locator:locator});
        body={...value,expected_timeline_version_id:original.expected_timeline_version_id,expected_timeline_sequence:original.expected_timeline_sequence};
      }else body=await version(api,task,value);
      return submit(api,`${taskPath(task)}/media-script-tools/${id(object)}/preview`,{...body,request_id:requestId},locator,'script');
    }
  }
  if (group === 'script') {
    if (command === 'capabilities') {
      check(3,[]);
      return {data:await api(`${taskPath(task)}/assistant/media-script-capabilities`)};
    }
    if (command === 'sources') {
      check(3,[]);
      return {data:await api(`${taskPath(task)}/graphics/sources`)};
    }
    if (command === 'submit') {
      check(3,['file','request-id']);
      if (!f.file) throw new CliError('程序提交需要 --file JSON');
      const requestId = uuid(f['request-id']), locator = requestPath(task,requestId,'script');
      let source, file;
      try {
        file = await open(f.file,constants.O_RDONLY | constants.O_NONBLOCK);
        const stat = await file.stat();
        if (!stat.isFile() || stat.size > 262144) throw new CliError('程序文件须是有界普通JSON文件');
        source = await boundedJson(file,262144);
      } catch { throw new CliError('程序文件须是有界完整JSON'); }
      finally { await file?.close(); }
      if (!source || Array.isArray(source) || typeof source !== 'object' || typeof source.script_source !== 'string'
          || ['assistant_turn_id','request_id','workspace_id','member_id','task_id','runtime_paths'].some(key => Object.hasOwn(source,key)))
        throw new CliError('程序文件只包含API声明的代码、参数、输入/输出和期望版本，不包含身份或运行时路径');
      const previous = await receipt(api,locator);
      if (previous?.status === 'unknown') return {...knownReceipt(previous,locator),submitted_input:source};
      if (previous && previous.status !== 'recorded') throw new CliError('程序请求回执无法核验',4,{request_locator:locator});
      let body;
      if (previous && !Object.hasOwn(source,'expected_timeline_version_id') && !Object.hasOwn(source,'expected_timeline_sequence')) {
        const original = previous.submitted_input;
        if (!original || !Object.hasOwn(original,'expected_timeline_version_id') || !Number.isSafeInteger(original.expected_timeline_sequence))
          throw new CliError('原程序提交版本不可核验，不能换绑新版本',4,{request_locator:locator});
        body = {...source,expected_timeline_version_id:original.expected_timeline_version_id,
          expected_timeline_sequence:original.expected_timeline_sequence};
      } else body = await version(api,task,source);
      return submit(api,`${taskPath(task)}/media-scripts/preview`,{...body,request_id:requestId},locator,'script');
    }
  }
  if (group === 'request' && command === 'query') {
    check(4,['phase']); const phase = f.phase || 'preview';
    if (!['preview','apply','script'].includes(phase)) throw new CliError('phase无效');
    const path = requestPath(task,object,phase);
    try { return knownReceipt(await api(path),path); }
    catch (error) { throw new CliError(error.message,error.code,{...error.details,request_locator:path}); }
  }
  if (group === 'edit' && ['preview','apply'].includes(command)) {
    check(command === 'preview' ? 3 : 4,command === 'preview' ? ['file','request-id'] : ['request-id']);
    const requestId = uuid(f['request-id']), locator = requestPath(task,requestId,command);
    const previous = await receipt(api,locator);
    if (previous?.status === 'unknown') return knownReceipt(previous,locator);
    if (previous && previous.status !== 'recorded') throw new CliError('请求回执无法核验',4,{request_locator:locator});
    if (command === 'preview') {
      if (!f.file) throw new CliError('预览需要 --file');
      let action, file;
      try {
        file = await open(f.file,constants.O_RDONLY | constants.O_NONBLOCK);
        const stat = await file.stat();
        if (!stat.isFile() || stat.size > 65536) throw new CliError('编辑文件须是有界普通文件');
        action = await boundedJson(file);
      }
      catch { throw new CliError('编辑文件须是有界完整 JSON'); }
      finally { await file?.close(); }
      if (!action || Array.isArray(action) || typeof action !== 'object' || typeof action.operation_type !== 'string'
          || ['assistant_turn_id','request_id','workspace_id','member_id','task_id'].some(k => k in action)) throw new CliError('编辑文件须仅包含实际动作与期望版本，不包含身份');
      let body;
      if (previous && !Object.hasOwn(action,'expected_timeline_version_id') && !Object.hasOwn(action,'expected_timeline_sequence')) {
        const original = previous.submitted_input;
        if (!original || !Object.hasOwn(original,'expected_timeline_version_id') || !Number.isSafeInteger(original.expected_timeline_sequence)) {
          throw new CliError('已记录请求尚未提供原提交版本投影；不能改绑当前版本',4,{request_locator:locator});
        }
        body = {...action,expected_timeline_version_id:original.expected_timeline_version_id,
          expected_timeline_sequence:original.expected_timeline_sequence};
      } else body = await version(api,task,action);
      return submit(api,`${taskPath(task)}/edits/exact/preview`,{...body,request_id:requestId},locator,'preview');
    }
    if (previous) {
      const original = previous.submitted_input;
      if (!original || !Object.hasOwn(original,'base_version_id') || !/^[0-9a-f]{64}$/.test(original.input_digest)) {
        throw new CliError('已记录应用请求尚未提供原提交绑定投影；不能遮蔽本次提案差异',4,{request_locator:locator});
      }
      const originalRequest = original.request;
      if (!originalRequest || !Object.hasOwn(originalRequest,'request_id')) throw new CliError('原应用请求绑定不完整',4,{request_locator:locator});
      return submit(api,`${taskPath(task)}/edits/${id(object)}/confirm`,{request_id:requestId,
        expected_base_version_id:Object.hasOwn(originalRequest,'expected_base_version_id') ? originalRequest.expected_base_version_id : original.base_version_id,
        expected_input_digest:originalRequest.expected_input_digest ?? original.input_digest},locator,'apply');
    }
    const proposal = await api(`${taskPath(task)}/edits/${id(object)}/result`);
    if (proposal.proposal_id !== object || !Object.hasOwn(proposal,'base_version_id') || !/^[0-9a-f]{64}$/.test(proposal.input_digest)) throw new CliError('原提案绑定无法核验',4,{request_locator:locator});
    if (!proposal.result && proposal.status !== 'proposed') return {code:4,state:'unknown',data:proposal,submitted:false,request_locator:locator};
    return submit(api,`${taskPath(task)}/edits/${id(object)}/confirm`,{request_id:requestId,
      expected_base_version_id:proposal.base_version_id,expected_input_digest:proposal.input_digest},locator,'apply');
  }
  if (group === 'job' && ['inspect','wait','cancel'].includes(command)) {
    check(4,command === 'wait' ? ['timeout-seconds'] : []);
    const path = `${taskPath(task)}/media-jobs/${id(object)}`;
    if (command === 'cancel') {
      try { const data = await api(path+'/cancel',{method:'POST'}); return {
        ...(data?.status === 'cancelled' ? {code:0,state:'done'} : outcome(data)),data,job_locator:path}; }
      catch (error) { throw new CliError(error.message,error.code,{...error.details,job_locator:path}); }
    }
    const timeout = number(f['timeout-seconds'],30,300)*1000, end = Date.now()+timeout;
    let last;
    for (;;) {
      if (last && Date.now() >= end) return last;
      let data;
      try { data = await api(path,{timeout:command === 'wait' ? Math.min(15000,Math.max(1,end-Date.now())) : 15000}); }
      catch (error) { throw new CliError(error.message,error.code,{...error.details,job_locator:path}); }
      if (data?.job_id !== object || !['queued','running','succeeded','failed','cancelled','interrupted','stale'].includes(data.status)) {
        throw new CliError('作业响应无法核验',4,{job_locator:path});
      }
      if (data.status === 'succeeded' && data.result == null) throw new CliError('作业尚无可核验结果',4,{job_locator:path});
      const result = {...outcome(data),data,job_locator:path};
      if (command !== 'wait' || result.code !== 3 || Date.now() >= end) return result;
      last = result;
      await delay(Math.min(1000,Math.max(0,end-Date.now())));
    }
  }
  if (group === 'timeline' && ['undo','redo'].includes(command)) {
    check(3,['expected-version','expected-sequence']); let body = {};
    if ('expected-version' in f || 'expected-sequence' in f) {
      if (!('expected-version' in f) || !/^[0-9]+$/.test(f['expected-sequence'] || '')) throw new CliError('需要版本与非负整数序号');
      const sequence = Number(f['expected-sequence']);
      if (!Number.isSafeInteger(sequence)) throw new CliError('序号超过可准确表示范围');
      body = {expected_timeline_version_id:f['expected-version'],expected_timeline_sequence:sequence};
    }
    body = await version(api,task,body);
    try { const data = await api(`${taskPath(task)}/timeline/${command}`,{method:'POST',body}); return {...outcome(data),data}; }
    catch (error) { throw new CliError(error.message,error.code,{...error.details,task_locator:taskPath(task)}); }
  }
  throw new CliError('命令未实现或参数不匹配，请查看 --help');
}

export async function runCli(argv,{stdout=process.stdout} = {}) {
  let secrets = [];
  try {
    if (Number(process.versions.node.split('.')[0]) < 22) throw new CliError('需要Node.js22或更高版本');
    const args = parse(argv);
    if (args.help) { stdout.write(HELP); return 0; }
    const config = await readConfig(args.flags.config || resolve(homedir(),'.config/yaohe/config.json'));
    secrets = config.secrets;
    const result = await dispatch(client(config),args);
    const code = result.code ?? 0;
    stdout.write(JSON.stringify(scrub({ok:code === 0,state:result.state || 'done',...result,code},secrets))+'\n');
    return code;
  } catch (error) {
    const value = error instanceof CliError ? error : new CliError('CLI执行未取得确定结果',4);
    stdout.write(JSON.stringify(scrub({ok:false,state:value.code === 4 ? 'unknown' : 'rejected',code:value.code,error:value.message,...value.details},secrets))+'\n');
    return value.code;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  process.exitCode = await runCli(process.argv.slice(2));
}
