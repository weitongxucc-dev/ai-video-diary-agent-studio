import {CliError} from './errors.mjs';
import {randomUUID} from 'node:crypto';
import {createWriteStream} from 'node:fs';
import {lstat, rename, rm} from 'node:fs/promises';
import {resolve} from 'node:path';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';

async function readJsonBody(response) {
  let text = '';
  try { text = await response.text(); } catch { return undefined; }
  if (text.length > 1048576) return undefined;
  try { return JSON.parse(text); } catch { return undefined; }
}

function httpError(response, data) {
  return new CliError('业务 API 拒绝或未返回确定结果', response.status >= 500 ? 4 : 1,
    {http_status:response.status, ...(data === undefined ? {} : {data})});
}

async function downloadToFile(base, headers, path, destination, {
  timeout=120000, expectedVersionId, expectedSequence,
} = {}) {
  const target = resolve(destination);
  let existing;
  try { existing = await lstat(target); }
  catch (error) { if (error.code !== 'ENOENT') throw new CliError('成片目标文件无法读取',2); }
  if (existing && (existing.isDirectory() || existing.isSymbolicLink())) {
    throw new CliError('成片目标必须是普通文件路径',2);
  }
  const url = new URL(path, base);
  let response;
  try {
    response = await fetch(url, {method:'GET', headers:{...headers,Accept:'video/mp4'},
      redirect:'manual', signal:AbortSignal.timeout(Math.max(1,Math.ceil(timeout)))});
  } catch { throw new CliError('成片下载未取得确定结果',4,{output_locator:path}); }
  if (response.status >= 300 && response.status < 400) {
    await response.body?.cancel();
    throw new CliError('业务 HTTP 重定向已拒绝，未向新地址发送身份',4,
      {http_status:response.status,output_locator:path});
  }
  if (!response.ok) {
    const data = await readJsonBody(response);
    const error = httpError(response,data);
    error.details.output_locator = path;
    throw error;
  }
  const mediaType = (response.headers.get('content-type') || '').toLowerCase();
  if (mediaType.split(';',1)[0].trim() !== 'video/mp4') {
    await response.body?.cancel();
    throw new CliError('主机返回的内容不是 MP4 成片',4,{http_status:response.status,output_locator:path});
  }
  const versionId = response.headers.get('x-timeline-version-id');
  const sequenceHeader = response.headers.get('x-timeline-sequence');
  const sequence = sequenceHeader === null ? NaN : Number(sequenceHeader);
  if (typeof expectedVersionId !== 'string' || !expectedVersionId || versionId !== expectedVersionId
      || !Number.isInteger(expectedSequence) || !Number.isInteger(sequence)
      || sequence !== expectedSequence) {
    await response.body?.cancel();
    throw new CliError('成片响应的时间线版本与任务快照不一致',4,
      {http_status:response.status,output_locator:path,
        expected_version_id:expectedVersionId,expected_sequence:expectedSequence,
        response_version_id:versionId,response_sequence:sequenceHeader});
  }
  if (!response.body) throw new CliError('成片响应没有可读取内容',4,{output_locator:path});
  const temporary = `${target}.part-${randomUUID()}`;
  let bytes = 0;
  try {
    const stream = Readable.fromWeb(response.body);
    const counting = stream.map(chunk => { bytes += chunk.length; return chunk; });
    await pipeline(counting, createWriteStream(temporary, {flags:'wx'}));
    const length = response.headers.get('content-length');
    if (length !== null && (!/^\d+$/.test(length) || Number(length) !== bytes)) {
      throw new CliError('成片响应未完整读取',4,{output_locator:path,bytes});
    }
    await rename(temporary,target);
  } catch (error) {
    await rm(temporary,{force:true});
    if (error instanceof CliError) throw error;
    throw new CliError('成片文件写入未完成',4,{output_locator:path});
  }
  return {path:target,bytes,version_id:versionId,sequence};
}

export function client({base, headers}) {
  const request = async (path, {method='GET',body,timeout=15000} = {}) => {
    let response;
    try {
      response = await fetch(new URL(path,base), {method, headers:{...headers,'Accept':'application/json',
        ...(body === undefined ? {} : {'Content-Type':'application/json'})},
        body:body === undefined ? undefined : JSON.stringify(body), redirect:'manual', signal:AbortSignal.timeout(Math.max(1,Math.ceil(timeout)))});
    } catch { throw new CliError('业务 HTTP 请求未取得确定结果',4); }
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      throw new CliError('业务 HTTP 重定向已拒绝，未向新地址发送身份',4,{http_status:response.status});
    }
    const reader = response.body?.getReader();
    let bytes = 0, chunks = [];
    if (reader) {
      try {
        for (;;) {
          const {done,value} = await reader.read();
          if (done) break;
          bytes += value.length;
          if (bytes > 1048576) { await reader.cancel(); throw new CliError('业务响应超过读取上限',4); }
          chunks.push(Buffer.from(value));
        }
      } catch (error) { if (error instanceof CliError) throw error; throw new CliError('业务响应未完整读取',4); }
    }
    let data;
    try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch { throw new CliError('业务响应不是完整 JSON',4,{http_status:response.status}); }
    if (!response.ok) throw new CliError('业务 API 拒绝或未返回确定结果',response.status >= 500 ? 4 : 1,
      {http_status:response.status,data});
    return data;
  };
  request.download = (path,destination,options) => downloadToFile(base,headers,path,destination,options);
  return request;
}
