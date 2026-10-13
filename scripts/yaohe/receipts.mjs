import {CliError} from './errors.mjs';

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

export async function receipt(api,path) {
  try { return await api(path); }
  catch (error) { if (error.details?.http_status === 404) return null; throw error; }
}
export function knownReceipt(value,path) {
  if (!['recorded','unknown'].includes(value?.status)) throw new CliError('请求回执无法核验',4,{request_locator:path});
  return {...outcome(value),data:value,request_locator:path,replayed:true,submitted:false};
}
export async function submit(api,path,body,locator,kind) {
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
