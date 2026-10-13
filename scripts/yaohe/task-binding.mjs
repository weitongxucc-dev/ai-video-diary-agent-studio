import {CliError} from './errors.mjs';
import {id, uuid} from './arguments.mjs';

export const taskPath = task => `/api/creation-tasks/${id(task)}`;
export const requestPath = (task,request,phase) => `${taskPath(task)}/assistant/edit-requests/${uuid(request)}?phase=${phase}`;

export async function version(api,task,source) {
  const a = 'expected_timeline_version_id', b = 'expected_timeline_sequence';
  if ((a in source) !== (b in source)) throw new CliError('期望版本与序号必须一起提供');
  if (a in source) return source;
  const current = (await api(taskPath(task))).task;
  if (!current || !Object.hasOwn(current,'current_timeline_version_id') || !Number.isInteger(current.timeline_sequence)) {
    throw new CliError('任务响应缺少可绑定时间线版本',4);
  }
  return {...source,[a]:current.current_timeline_version_id,[b]:current.timeline_sequence};
}
