import {CliError} from './errors.mjs';

export function client({base, headers}) {
  return async (path, {method='GET',body,timeout=15000} = {}) => {
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
}
