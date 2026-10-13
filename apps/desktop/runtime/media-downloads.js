const fs = require('fs');
const http = require('http');
const { pipeline } = require('stream/promises');

const SUBTITLE_RESPONSE_TIMEOUT_MS = 30000;

async function downloadCreationTaskOutput({ port, state }, taskId, temporaryPath, coverId = null) {
  if (!Number.isInteger(port) || port <= 0 || state !== 'ready') {
    throw new Error('本地视频服务尚未就绪，请等待服务恢复后重试。');
  }
  const requestPath = coverId
    ? `/api/creation-tasks/${encodeURIComponent(taskId)}/covers/${encodeURIComponent(coverId)}/image`
    : `/api/creation-tasks/${encodeURIComponent(taskId)}/output`;
  const mediaType = coverId ? 'image/png' : 'video/mp4';
  const label = coverId ? 'PNG 封面' : 'MP4';
  await new Promise((resolve, reject) => {
    const request = http.get({
      hostname: '127.0.0.1',
      port: port,
      path: requestPath,
      headers: { Accept: mediaType },
    }, (response) => {
      const contentType = String(response.headers['content-type'] || '');
      if (response.statusCode !== 200 || contentType.split(';')[0].trim() !== mediaType) {
        response.resume();
        reject(new Error(`本地服务没有返回可保存的 ${label}（HTTP ${response.statusCode || 0}）。`));
        return;
      }
      const output = fs.createWriteStream(temporaryPath, { flags: 'wx' });
      pipeline(response, output).then(resolve, reject);
    });
    request.on('error', reject);
  });
  const stats = await fs.promises.stat(temporaryPath);
  if (!stats.isFile() || stats.size <= 0) {
    throw new Error('本地服务返回了空的成片文件。');
  }
}

async function downloadSelectedSubtitles({ port, state }, selection, temporaryPath, timeoutMs = SUBTITLE_RESPONSE_TIMEOUT_MS) {
  if (!Number.isInteger(port) || port <= 0 || state !== 'ready') {
    throw new Error('本地视频服务尚未就绪，请等待服务恢复后重试。');
  }
  const params = new URLSearchParams({
    expected_version_id: selection.expectedVersionId,
    mode: selection.mode,
  });
  if (selection.zhTrackId) params.set('zh_track_id', selection.zhTrackId);
  if (selection.enTrackId) params.set('en_track_id', selection.enTrackId);
  const requestPath = `/api/creation-tasks/${encodeURIComponent(selection.taskId)}/output/selected-subtitles?${params}`;
  await new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve();
    };
    // The API can accept the request and then never end the response. Without a
    // bound the save promise hangs forever and the .partial file stays beside
    // the destination the user chose, so the timeout destroys the request and
    // surfaces a retryable error instead.
    const timer = setTimeout(() => {
      request.destroy(new Error('本地字幕服务长时间没有返回，已取消本次保存，请重试。'));
      finish(new Error('本地字幕服务长时间没有返回，已取消本次保存，请重试。'));
    }, timeoutMs);
    const request = http.get({
      hostname: '127.0.0.1',
      port: port,
      path: requestPath,
      headers: { Accept: 'application/x-subrip' },
    }, (response) => {
      const contentType = String(response.headers['content-type'] || '').split(';')[0].trim();
      if (response.statusCode !== 200 || contentType !== 'application/x-subrip') {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () => {
          let reason = '';
          try { reason = JSON.parse(Buffer.concat(chunks).toString('utf8')).detail || ''; } catch {}
          finish(new Error(reason || `本地服务没有返回可保存的字幕（HTTP ${response.statusCode || 0}）。`));
        });
        response.on('error', (error) => finish(error));
        return;
      }
      const output = fs.createWriteStream(temporaryPath, { flags: 'wx' });
      pipeline(response, output).then(() => finish(), (error) => finish(error));
    });
    request.on('error', (error) => finish(error));
  });
  const stats = await fs.promises.stat(temporaryPath);
  if (!stats.isFile() || stats.size <= 0) {
    throw new Error('本地服务返回了空的字幕文件。');
  }
}


module.exports = { downloadCreationTaskOutput, downloadSelectedSubtitles, SUBTITLE_RESPONSE_TIMEOUT_MS };
