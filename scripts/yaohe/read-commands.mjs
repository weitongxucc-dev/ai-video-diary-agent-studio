import {CliError} from './errors.mjs';
import {id, number, checkArguments} from './arguments.mjs';
import {taskPath} from './task-binding.mjs';

export async function readCommand(api,args) {
  const {positional:p,flags:f} = args;
  const [group,command,task,object] = p;
  const check = (length,allowed) => checkArguments(p,f,length,allowed);
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
  throw new CliError('命令未实现或参数不匹配，请查看 --help');
}
