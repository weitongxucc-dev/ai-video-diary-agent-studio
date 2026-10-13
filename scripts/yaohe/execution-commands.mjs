import {CliError} from './errors.mjs';
import {id, number, checkArguments} from './arguments.mjs';
import {taskPath} from './task-binding.mjs';
import {outcome} from './receipts.mjs';
import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {boundedJson} from './files.mjs';
import {setTimeout as delay} from 'node:timers/promises';

export function executionResult(data,task,{jobId,digest,allowNotStarted=false,requireVideoReady=true} = {}) {
  const locator = `${taskPath(task)}/execution`, job = data?.job;
  if (data?.task_id !== task) throw new CliError('制作响应不属于原任务',4,{execution_locator:locator});
  if (job === null && allowNotStarted) return {code:0,state:'not_started',data,execution_locator:locator,job_id:null};
  if (!job || typeof job.id !== 'string' || !job.id || job.task_id !== task || job.kind !== 'creation_task_execution'
      || !/^[0-9a-f]{64}$/.test(job.input_digest) || !['queued','running','succeeded','failed','cancelled','interrupted'].includes(job.status)) {
    throw new CliError('制作响应缺少可核验的原作业',4,{execution_locator:locator,...(jobId ? {job_id:jobId} : {})});
  }
  if ((jobId && job.id !== jobId) || (digest && job.input_digest !== digest)) {
    throw new CliError('任务已切换制作作业或输入；不改绑原等待',4,
      {execution_locator:locator,...(jobId ? {job_id:jobId} : {}),observed_job_id:job.id});
  }
  if (requireVideoReady && job.status === 'succeeded' && data.video_ready !== true) {
    throw new CliError('制作作业已结束，但当前成片尚不可核验',4,{execution_locator:locator,job_id:job.id});
  }
  return {...outcome(job),data,execution_locator:locator,job_id:job.id};
}

export async function submitExecution(api,task,command,body,{jobId,digest,previousJobId} = {}) {
  const locator = `${taskPath(task)}/execution`;
  try {
    const data = await api(`${locator}/${command}`,{method:'POST',body});
    const result = executionResult(data,task,{digest,...(command === 'resume' ? {jobId} : {})});
    return {...result,submitted:true,...(previousJobId ? {previous_job_id:previousJobId,reused_job:result.job_id === previousJobId} : {})};
  } catch (error) {
    if (error.code !== 4) throw new CliError(error.message,error.code,{...error.details,execution_locator:locator,
      ...(jobId ? {job_id:jobId} : {})});
    let original, queryState = 'query_unavailable';
    try {
      original = executionResult(await api(locator),task,{jobId,digest});
      queryState = 'original_input_observed';
    } catch (queryError) {
      if (queryError.details?.observed_job_id) queryState = 'different_job';
    }
    // This API has no per-submission receipt. A current job read cannot prove a lost POST succeeded.
    throw new CliError('制作提交结果未知；仅回读原任务作业，不自动重发',4,{...error.details,
      execution_locator:locator,...(jobId ? {job_id:jobId} : {}),query_state:queryState,
      ...(original ? {observed_execution:original.data,observed_job_id:original.job_id} : {})});
  }
}

export async function executionCommand(api,args) {
  const {positional:p,flags:f} = args;
  const [group,command,task,object] = p;
  const check = (length,allowed) => checkArguments(p,f,length,allowed);
  if (group === 'execution') {
    const locator = `${taskPath(task)}/execution`;
    if (command === 'preview') { check(3,[]); return {data:await api(locator+'/preview'),execution_locator:locator}; }
    if (command === 'status') {
      check(3,[]); return executionResult(await api(locator),task,{allowNotStarted:true});
    }
    if (command === 'start') {
      check(3,['file']); if (!f.file) throw new CliError('开始制作需要明确 --file JSON');
      let source,file;
      try {
        file = await open(f.file,constants.O_RDONLY | constants.O_NONBLOCK);
        const stat = await file.stat();
        if (!stat.isFile() || stat.size > 65536) throw new CliError('制作文件须是有界普通文件');
        source = await boundedJson(file);
      } catch { throw new CliError('制作文件须是有界完整 JSON'); }
      finally { await file?.close(); }
      if (!source || Array.isArray(source) || typeof source !== 'object'
          || Object.keys(source).some(key => !['script_id','input_digest','requirements_confirmed'].includes(key))
          || typeof source.script_id !== 'string' || !source.script_id.trim()
          || typeof source.input_digest !== 'string' || !/^[0-9a-f]{64}$/.test(source.input_digest)
          || source.requirements_confirmed !== true) {
        throw new CliError('制作文件须明确实际script_id、input_digest和requirements_confirmed:true，不自动确认或补值');
      }
      const previous = executionResult(await api(locator),task,{allowNotStarted:true,requireVideoReady:false});
      return submitExecution(api,task,'start',source,{digest:source.input_digest,previousJobId:previous.job_id,
        ...(previous.data.job?.input_digest === source.input_digest ? {jobId:previous.job_id} : {})});
    }
    if (command === 'resume') {
      check(3,[]);
      const previous = executionResult(await api(locator),task,{requireVideoReady:false});
      return submitExecution(api,task,'resume',undefined,{jobId:previous.job_id,previousJobId:previous.job_id,
        digest:previous.data.job.input_digest});
    }
    if (command === 'wait') {
      check(4,['timeout-seconds']); id(object);
      const end = Date.now()+number(f['timeout-seconds'],30,300)*1000;
      let last;
      for (;;) {
        if (last && Date.now() >= end) return last;
        let data;
        try { data = await api(locator,{timeout:Math.min(15000,Math.max(1,end-Date.now()))}); }
        catch (error) { throw new CliError(error.message,error.code,{...error.details,execution_locator:locator,job_id:object}); }
        const result = executionResult(data,task,{jobId:object});
        if (result.code !== 3 || Date.now() >= end) return result;
        last = result;
        await delay(Math.min(1000,Math.max(0,end-Date.now())));
      }
    }
  }
  throw new CliError('命令未实现或参数不匹配，请查看 --help');
}
