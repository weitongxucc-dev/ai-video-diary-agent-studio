import {CliError} from './errors.mjs';
import {id, uuid, checkArguments} from './arguments.mjs';
import {taskPath, requestPath, version} from './task-binding.mjs';
import {outcome, receipt, knownReceipt, submit} from './receipts.mjs';
import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {boundedJson} from './files.mjs';

export async function editCommand(api,args) {
  const {positional:p,flags:f} = args;
  const [group,command,task,object] = p;
  const check = (length,allowed) => checkArguments(p,f,length,allowed);
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
