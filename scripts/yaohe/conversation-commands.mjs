import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {CliError} from './errors.mjs';
import {uuid, id, number, checkArguments} from './arguments.mjs';
import {boundedJson} from './files.mjs';

async function payloadFile(path) {
  if (!path) throw new CliError('会话写入需要 --file JSON');
  let file;
  try {
    file = await open(path, constants.O_RDONLY | constants.O_NONBLOCK);
    const stat = await file.stat();
    if (!stat.isFile()) throw new CliError('会话输入须是普通 JSON 文件');
    const value = await boundedJson(file);
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new CliError('会话输入须是 JSON 对象');
    return value;
  } catch (error) {
    if (error instanceof CliError) throw error;
    throw new CliError('无法读取有界完整的会话 JSON');
  } finally { await file?.close(); }
}

function only(value, fields) {
  if (Object.keys(value).some(key => !fields.includes(key))) {
    throw new CliError('会话文件包含不支持字段；身份来自私有配置，目标与请求身份来自命令');
  }
}
function turnOutcome(status) {
  if (status === 'completed') return {code:0,state:'done'};
  if (['running','queued'].includes(status)) return {code:3,state:'pending'};
  if (['failed','stopped'].includes(status)) return {code:1,state:'failed'};
  return {code:4,state:'unknown'};
}

export async function conversationCommand(api, args) {
  const needsBody = ['create','draft','turn'].includes(args.positional[1]);
  return conversationRequest(api,args,needsBody ? await payloadFile(args.flags.file) : undefined);
}

// CLI files and MCP structured arguments share the same identity/version rules.
export async function conversationRequest(api, {positional:p,flags:f}, input) {
  const [,command,conversation,request] = p;
  const check = (length, fields) => checkArguments(p,f,length,fields);
  if (command === 'create') {
    check(2,['file']);
    const body = input;
    only(body,['scope_kind','scope_id','title']);
    // Creation is explicit. A lost response is unknown, never an automatic retry.
    return {data:await api('/api/assistant/conversations',{method:'POST',body})};
  }
  const path = `/api/assistant/conversations/${uuid(conversation)}`;
  if (command === 'inspect') {
    check(3,[]); return {data:await api(path)};
  }
  if (command === 'request') {
    check(4,[]);
    const locator = `${path}/requests/${uuid(request)}`;
    return {data:await api(locator),request_locator:locator,submitted:false};
  }
  if (command === 'draft') {
    check(3,['file']);
    const body = input;
    only(body,['draft_text','expected_draft_version']);
    if (typeof body.draft_text !== 'string' || !Number.isSafeInteger(body.expected_draft_version) || body.expected_draft_version < 0) {
      throw new CliError('草稿需要draft_text和明确的非负expected_draft_version');
    }
    return {data:await api(path,{method:'PATCH',body})};
  }
  if (command === 'turn') {
    check(3,['file','request-id','task','timeout-seconds']);
    const requestId = uuid(f['request-id']);
    const body = input;
    only(body,['message','expected_draft_version','expected_reference_version',
      ...(f.task ? ['context_stage','expected_timeline_version_id','expected_timeline_sequence','selected_object_refs','playhead_ms'] : [])]);
    if (typeof body.message !== 'string' || !Number.isSafeInteger(body.expected_draft_version) || body.expected_draft_version < 0
      || !Number.isSafeInteger(body.expected_reference_version) || body.expected_reference_version < 0) {
      throw new CliError('发送需要message、已保存草稿版本和引用版本；先inspect和draft，不自动覆盖草稿');
    }
    const endpoint = f.task ? `/api/creation-tasks/${id(f.task)}/assistant/turn` : '/api/assistant/plan/turn';
    const locator = `${path}/requests/${requestId}`;
    try {
      const data = await api(endpoint,{method:'POST',body:{...body,conversation_id:conversation,client_request_id:requestId},
        timeout:number(f['timeout-seconds'],120,300)*1000});
      return {...turnOutcome(data.turn_status),data,request_locator:locator};
    } catch (error) {
      // Do not infer this request's result from the conversation's latest turn.
      // The authoritative receipt is queried explicitly, without resubmission.
      throw new CliError(error.message,error.code,{...error.details,request_locator:locator,
        conversation_id:conversation,client_request_id:requestId});
    }
  }
  throw new CliError('会话命令未实现，请查看 --help');
}
