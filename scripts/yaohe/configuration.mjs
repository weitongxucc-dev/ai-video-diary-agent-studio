import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {boundedJson} from './files.mjs';
import {CliError} from './errors.mjs';

export async function readConfig(path) {
  let file;
  try {
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > 65536) throw new CliError('配置须是有界普通文件');
    if (process.platform !== 'win32' && ((stat.mode & 0o7777) !== 0o600 || stat.uid !== process.getuid())) {
      throw new CliError('配置须由当前用户拥有且权限为0600');
    }
    let config;
    try { config = await boundedJson(file); }
    catch { throw new CliError('配置 JSON 无效'); }
    const fields = new Set(['api_base','local_member_token','authorization','cookie']);
    if (!config || Array.isArray(config) || typeof config !== 'object' || Object.keys(config).some(k => !fields.has(k))) {
      throw new CliError('配置仅支持 API 地址与既有身份字段');
    }
    let base;
    try { base = new URL(config.api_base); } catch { throw new CliError('配置 API 地址无效'); }
    if (!['http:','https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== '/') {
      throw new CliError('API 地址须是不含凭据、查询或路径的 HTTP(S) origin');
    }
    const headers = {};
    for (const [key, header] of [['local_member_token','X-Local-Member-Token'],['authorization','Authorization'],['cookie','Cookie']]) {
      if (config[key] !== undefined) {
        if (typeof config[key] !== 'string' || !config[key].trim() || /[\r\n]/.test(config[key])) throw new CliError('身份配置无效');
        headers[header] = config[key];
      }
    }
    if (!Object.keys(headers).length) throw new CliError('配置缺少既有成员或会话身份');
    const secrets = Object.values(headers);
    if (config.authorization) secrets.push(config.authorization.replace(/^Bearer\s+/i,''));
    if (config.cookie) secrets.push(...config.cookie.split(';').map(v => v.slice(v.indexOf('=')+1).trim()).filter(Boolean));
    return {base: base.origin, headers, secrets};
  } catch (error) {
    if (error instanceof CliError) throw error;
    throw new CliError('无法安全读取私有配置文件（不存在、符号链接或无权限）');
  } finally { await file?.close(); }
}

export function scrub(value, secrets) {
  if (typeof value === 'string') {
    for (const secret of secrets.filter(Boolean).sort((a,b) => b.length-a.length)) value = value.split(secret).join('[redacted]');
    return value;
  }
  if (Array.isArray(value)) return value.map(v => scrub(v,secrets));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [scrub(k,secrets),scrub(v,secrets)]));
  return value;
}
