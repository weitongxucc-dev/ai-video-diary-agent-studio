#!/usr/bin/env node
import {homedir} from 'node:os';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {HELP} from './yaohe/usage.mjs';
import {CliError} from './yaohe/errors.mjs';
import {parse} from './yaohe/arguments.mjs';
import {readConfig, scrub} from './yaohe/configuration.mjs';
import {client} from './yaohe/transport.mjs';
import {dispatch} from './yaohe/commands.mjs';

export {readConfig} from './yaohe/configuration.mjs';
export {outcome} from './yaohe/receipts.mjs';

export async function runCli(argv,{stdout=process.stdout} = {}) {
  let secrets = [];
  try {
    if (Number(process.versions.node.split('.')[0]) < 22) throw new CliError('需要Node.js22或更高版本');
    const args = parse(argv);
    if (args.help) { stdout.write(HELP); return 0; }
    const config = await readConfig(args.flags.config || resolve(homedir(),'.config/yaohe/config.json'));
    secrets = config.secrets;
    const result = await dispatch(client({base:config.base,headers:config.headers}),args);
    const code = result.code ?? 0;
    stdout.write(JSON.stringify(scrub({ok:code === 0,state:result.state || 'done',...result,code},secrets))+'\n');
    return code;
  } catch (error) {
    const value = error instanceof CliError ? error : new CliError('CLI执行未取得确定结果',4);
    stdout.write(JSON.stringify(scrub({ok:false,state:value.code === 4 ? 'unknown' : 'rejected',code:value.code,error:value.message,...value.details},secrets))+'\n');
    return value.code;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  process.exitCode = await runCli(process.argv.slice(2));
}
