import {CliError} from './errors.mjs';
import {id, number, checkArguments} from './arguments.mjs';
import {taskPath, requestPath} from './task-binding.mjs';
import {outcome, knownReceipt} from './receipts.mjs';
import {setTimeout as delay} from 'node:timers/promises';

export async function jobCommand(api,args) {
  const {positional:p,flags:f} = args;
  const [group,command,task,object] = p;
  const check = (length,allowed) => checkArguments(p,f,length,allowed);
  if (group === 'request' && command === 'query') {
    check(4,['phase']); const phase = f.phase || 'preview';
    if (!['preview','apply','script'].includes(phase)) throw new CliError('phase无效');
    const path = requestPath(task,object,phase);
    try { return knownReceipt(await api(path),path); }
    catch (error) { throw new CliError(error.message,error.code,{...error.details,request_locator:path}); }
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
  throw new CliError('命令未实现或参数不匹配，请查看 --help');
}
