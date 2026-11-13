import {CliError} from './errors.mjs';
import {checkArguments} from './arguments.mjs';
import {taskPath, taskSnapshot} from './task-binding.mjs';

export async function outputCommand(api,args) {
  const {positional:p,flags:f} = args;
  checkArguments(p,f,3,['file']);
  if (p[0] !== 'output' || p[1] !== 'download' || !f.file) {
    throw new CliError('成片下载需要 output download TASK --file PATH');
  }
  const taskId = p[2];
  const locator = taskPath(taskId);
  const detail = await api(locator);
  const task = taskSnapshot(detail);
  if (!task.output_ready) {
    return {code:3,state:'pending',data:{task_id:taskId,workspace_id:task.workspace_id,
      current_timeline_version_id:task.current_timeline_version_id,
      timeline_sequence:task.timeline_sequence,output_ready:false},output_locator:`${locator}/output`};
  }
  if (!task.current_timeline_version_id) {
    throw new CliError('任务标记成片就绪但缺少当前时间线版本',4,
      {task_id:taskId,output_locator:`${locator}/output`});
  }
  const outputLocator = `${locator}/output?${new URLSearchParams({
    expected_version_id:task.current_timeline_version_id,
  })}`;
  try {
    const downloaded = await api.download(outputLocator,f.file,{expectedVersionId:task.current_timeline_version_id,
      expectedSequence:task.timeline_sequence});
    return {code:0,state:'done',data:{task_id:taskId,workspace_id:task.workspace_id,
      output_ready:true,current_timeline_version_id:task.current_timeline_version_id,
      timeline_sequence:task.timeline_sequence,...downloaded},output_locator:outputLocator};
  } catch (error) {
    throw new CliError(error.message,error.code,{...error.details,output_locator:outputLocator});
  }
}
