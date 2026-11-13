import {conversationCommand} from './conversation-commands.mjs';
import {CliError} from './errors.mjs';
import {readCommand} from './read-commands.mjs';
import {scriptCommand} from './script-commands.mjs';
import {executionCommand} from './execution-commands.mjs';
import {editCommand} from './edit-commands.mjs';
import {jobCommand} from './job-commands.mjs';
import {outputCommand} from './output-commands.mjs';

export async function dispatch(api,args) {
  const [group,command] = args.positional;
  if (group === 'conversation') return conversationCommand(api,args);
  if (['capabilities','task','material'].includes(group)) return readCommand(api,args);
  if (['tool','script'].includes(group)) return scriptCommand(api,args);
  if (group === 'execution') return executionCommand(api,args);
  if (group === 'output' && command === 'download') return outputCommand(api,args);
  if ((group === 'edit' && ['preview','apply'].includes(command))
      || (group === 'timeline' && ['undo','redo'].includes(command))) return editCommand(api,args);
  if ((group === 'request' && command === 'query')
      || (group === 'job' && ['inspect','wait','cancel'].includes(command))) return jobCommand(api,args);
  throw new CliError('命令未实现或参数不匹配，请查看 --help');
}
