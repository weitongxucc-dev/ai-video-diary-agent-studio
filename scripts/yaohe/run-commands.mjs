import {CliError} from './errors.mjs';
import {checkArguments, id} from './arguments.mjs';
import {taskPath, taskSnapshot} from './task-binding.mjs';

const TRACE_FIELDS = new Set([
  'turn_id','conversation_id','status','sequence','created_at','updated_at',
  'events','event_count','tool_counts','compaction_count','provider_usage',
  'usage_status','usage_source','associations','business_status','evidence_candidates',
  'started_at','ended_at','duration_ms','failure_class','retry_count',
]);
const USAGE_STATUSES = new Set(['known','estimated','unknown']);
const TRACE_STATUSES = new Set(['queued','running','completed','failed','stopped','unknown']);
const FAILURE_FIELDS = new Set(['error','code','stage','submission_effect']);
const TRACE_EVENT_TYPES = new Set([
  'turn_start','tool_start','tool_end','context_compaction','compaction_start',
  'compaction_end','retry','nested_tool',
]);

function string(value, field) {
  if (typeof value !== 'string' || !value) throw new CliError(`运行追踪响应缺少${field}`,4);
  return value;
}

function nonNegativeInteger(value, field) {
  if (!Number.isSafeInteger(value) || value < 0) throw new CliError(`运行追踪响应的${field}无法核验`,4);
  return value;
}

function usage(value) {
  if (value === undefined || value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new CliError('运行追踪用量无法核验',4);
  const result = {};
  for (const key of ['input','output','cacheRead','cacheWrite','totalTokens']) {
    if (value[key] !== undefined) result[key] = nonNegativeInteger(value[key],`provider_usage.${key}`);
  }
  return Object.keys(result).length ? result : null;
}

function associations(value) {
  if (!Array.isArray(value)) return [];
  return value.filter(item => item && typeof item === 'object'
    && ['conversation','turn','tool_call','business_request','job','output','operation','task','input'].includes(item.kind)
    && typeof item.ref === 'string' && item.ref).map(item => ({kind:item.kind,ref:item.ref}));
}

function failure(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const result = {};
  for (const key of FAILURE_FIELDS) {
    if (value[key] !== undefined) {
      if (typeof value[key] !== 'string' && typeof value[key] !== 'number') throw new CliError('运行追踪失败原因无法核验',4);
      result[key] = value[key];
    }
  }
  return Object.keys(result).length ? result : undefined;
}

function event(value) {
  if (!value || typeof value !== 'object' || !TRACE_EVENT_TYPES.has(value.type)) {
    throw new CliError('运行追踪事件无法核验',4);
  }
  const result = {type:value.type};
  for (const key of ['tool_id','label','status','trigger','failure_origin','tool_call_ref','tool_name','failure_class','at']) {
    if (typeof value[key] === 'string') result[key] = value[key];
  }
  if (value.step !== undefined && value.step !== null) result.step = nonNegativeInteger(value.step,'event.step');
  if (value.duration_ms !== undefined && value.duration_ms !== null) result.duration_ms = nonNegativeInteger(value.duration_ms,'event.duration_ms');
  if (value.type === 'tool_end' && value.ok !== undefined) {
    if (typeof value.ok !== 'boolean') throw new CliError('运行追踪工具结果无法核验',4);
    result.ok = value.ok;
    const detail = failure(value.failure);
    if (detail) result.failure = detail;
  }
  const refs = associations(value.associations);
  if (refs.length) result.associations = refs;
  return result;
}

function projectTrace(value, requestedTurn) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(key => !TRACE_FIELDS.has(key))) {
    throw new CliError('运行追踪响应无法核验',4);
  }
  const turnId = string(value.turn_id,'turn_id');
  if (turnId !== requestedTurn) throw new CliError('运行追踪回合与请求不一致',4);
  const status = string(value.status,'status');
  if (!TRACE_STATUSES.has(status)) throw new CliError('运行追踪状态无法核验',4);
  const usageStatus = string(value.usage_status,'usage_status');
  if (!USAGE_STATUSES.has(usageStatus)) throw new CliError('运行追踪用量状态无法核验',4);
  const providerUsage = usage(value.provider_usage);
  if (usageStatus === 'known' && !providerUsage) throw new CliError('运行追踪标记已知用量但没有实测数据',4);
  const events = value.events === undefined ? [] : value.events;
  if (!Array.isArray(events) || events.length > 200) throw new CliError('运行追踪事件数量无法核验',4);
  const toolCounts = value.tool_counts === undefined ? {} : value.tool_counts;
  if (!toolCounts || typeof toolCounts !== 'object' || Array.isArray(toolCounts)
      || Object.entries(toolCounts).some(([key,count]) => typeof key !== 'string' || !Number.isSafeInteger(count) || count < 0)) {
    throw new CliError('运行追踪工具计数无法核验',4);
  }
  const projected = {
    turn_id:turnId,
    conversation_id:string(value.conversation_id,'conversation_id'),
    status,
    sequence:nonNegativeInteger(value.sequence,'sequence'),
    events:events.map(event),
    event_count:nonNegativeInteger(value.event_count,'event_count'),
    tool_counts:{...toolCounts},
    compaction_count:nonNegativeInteger(value.compaction_count,'compaction_count'),
    provider_usage:providerUsage,
    usage_status:usageStatus,
    usage_source:value.usage_source === null || value.usage_source === undefined ? null : string(value.usage_source,'usage_source'),
    associations:associations(value.associations),
    business_status:value.business_status === null || value.business_status === undefined ? null : string(value.business_status,'business_status'),
    evidence_candidates:Array.isArray(value.evidence_candidates) ? value.evidence_candidates.map(candidate => ({
      reviewer_kind:string(candidate?.reviewer_kind,'evidence reviewer kind'),
      candidate_ref:string(candidate?.candidate_ref,'evidence candidate'),
      status:string(candidate?.status,'evidence status'),
      source_refs:Array.isArray(candidate?.source_refs) ? candidate.source_refs.filter(ref => typeof ref === 'string') : [],
    })) : [],
    started_at:value.started_at === null || value.started_at === undefined ? null : string(value.started_at,'started_at'),
    ended_at:value.ended_at === null || value.ended_at === undefined ? null : string(value.ended_at,'ended_at'),
    duration_ms:value.duration_ms === null || value.duration_ms === undefined ? null : nonNegativeInteger(value.duration_ms,'duration_ms'),
    failure_class:value.failure_class === null || value.failure_class === undefined ? null : string(value.failure_class,'failure_class'),
    retry_count:nonNegativeInteger(value.retry_count,'retry_count'),
  };
  return projected;
}

function traceOutcome(status) {
  if (status === 'completed') return {code:0,state:'done'};
  if (['queued','running'].includes(status)) return {code:3,state:'pending'};
  if (['failed','stopped'].includes(status)) return {code:1,state:'failed'};
  return {code:4,state:'unknown'};
}

export async function runCommand(api,args) {
  const {positional:p,flags:f} = args;
  checkArguments(p,f,3,['task']);
  if (p[0] !== 'run' || p[1] !== 'trace') throw new CliError('运行命令未实现，请查看 --help');
  const turn = id(p[2]);
  let task;
  if (f.task) task = taskSnapshot(await api(taskPath(f.task)));
  const locator = `/api/assistant/turns/${turn}/trace`;
  let data;
  try { data = projectTrace(await api(locator),p[2]); }
  catch (error) { throw new CliError(error.message,error.code,{...error.details,trace_locator:locator}); }
  const taskSnapshotData = task ? {
    task_id:f.task,workspace_id:task.workspace_id,
    current_timeline_version_id:task.current_timeline_version_id,
    timeline_sequence:task.timeline_sequence,output_ready:task.output_ready,
  } : undefined;
  return {...traceOutcome(data.status),data:taskSnapshotData ? {...data,task_snapshot:taskSnapshotData} : data,
    trace_locator:locator};
}
