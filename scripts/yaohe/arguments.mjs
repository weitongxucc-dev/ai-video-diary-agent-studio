import {CliError} from './errors.mjs';

export function parse(args) {
  const flags = {}, positional = [];
  const allowed = new Set(['config','task','file','request-id','phase','query','role','limit',
    'timeout-seconds','expected-version','expected-sequence','version','expected-tool-version']);
  for (let i = 0; i < args.length; i++) {
    const value = args[i];
    if (value === '--help' || value === '-h') return {help: true};
    if (!value.startsWith('--')) { positional.push(value); continue; }
    const name = value.slice(2);
    if (!allowed.has(name) || name in flags || !args[i+1] || args[i+1].startsWith('--')) {
      throw new CliError('未知、重复或缺少值的选项；不接受凭据参数');
    }
    flags[name] = args[++i];
  }
  return {flags, positional};
}

export function id(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,120}$/.test(value)) throw new CliError('需要明确有效的对象 ID');
  return encodeURIComponent(value);
}
export function uuid(value) {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    throw new CliError('需要稳定的 UUID request-id');
  }
  return value.toLowerCase();
}
export function number(value, fallback, max) {
  const n = value === undefined ? fallback : Number(value);
  if (!Number.isFinite(n) || n <= 0 || n > max) throw new CliError('数值选项必须是有界正数');
  return n;
}

export function checkArguments(positional, flags, length, allowed) {
  if (positional.length !== length || Object.keys(flags).some(k => k !== 'config' && !allowed.includes(k))) {
    throw new CliError('命令参数不匹配，请查看 --help');
  }
}
