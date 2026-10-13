import {CliError} from './errors.mjs';

export async function boundedJson(file, limit = 65536) {
  const buffer = Buffer.alloc(limit + 1);
  let size = 0;
  while (size < buffer.length) {
    const {bytesRead} = await file.read(buffer,size,buffer.length-size,size);
    if (!bytesRead) break;
    size += bytesRead;
  }
  if (size > limit) throw new CliError('JSON文件超过读取上限');
  return JSON.parse(buffer.subarray(0,size).toString('utf8'));
}
