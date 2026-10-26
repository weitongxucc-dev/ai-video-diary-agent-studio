const fs = require('fs');
const { Readable } = require('stream');
const { pipeline } = require('stream/promises');

const SUBTITLE_RESPONSE_TIMEOUT_MS = 30000;

async function downloadCreationTaskOutput(context, taskId, temporaryPath, coverId = null, expectedVersionId) {
  const requestPath = coverId
    ? `/api/creation-tasks/${encodeURIComponent(taskId)}/covers/${encodeURIComponent(coverId)}/image`
    : `/api/creation-tasks/${encodeURIComponent(taskId)}/output?${new URLSearchParams({ expected_version_id: expectedVersionId })}`;
  const mediaType = coverId ? 'image/png' : 'video/mp4';
  const label = coverId ? 'PNG 封面' : 'MP4';
  const response = await context.fetchPath(requestPath, { accept: mediaType });
  const contentType = String(response.headers.get('content-type') || '').split(';')[0].trim();
  if (response.status !== 200 || contentType !== mediaType) {
    let reason = '';
    if (response.status === 409 && contentType === 'application/json') {
      try {
        const detail = (await response.json()).detail;
        if (typeof detail === 'string') reason = detail;
      } catch {}
    }
    // Discard the rejected body without replacing the status/MIME error.
    if (response.body) await response.body.cancel().catch(() => undefined);
    throw new Error(reason || `本地服务没有返回可保存的 ${label}（HTTP ${response.status || 0}）。`);
  }
  if (!response.body) throw new Error('本地服务返回了空的成片文件。');
  const output = fs.createWriteStream(temporaryPath, { flags: 'wx' });
  await pipeline(Readable.fromWeb(response.body), output);
  const stats = await fs.promises.stat(temporaryPath);
  if (!stats.isFile() || stats.size <= 0) {
    throw new Error('本地服务返回了空的成片文件。');
  }
}

async function downloadSelectedSubtitles(context, selection, temporaryPath, timeoutMs = SUBTITLE_RESPONSE_TIMEOUT_MS) {
  const params = new URLSearchParams({
    expected_version_id: selection.expectedVersionId,
    mode: selection.mode,
  });
  if (selection.zhTrackId) params.set('zh_track_id', selection.zhTrackId);
  if (selection.enTrackId) params.set('en_track_id', selection.enTrackId);
  const requestPath = `/api/creation-tasks/${encodeURIComponent(selection.taskId)}/output/selected-subtitles?${params}`;
  const timeoutError = new Error('本地字幕服务长时间没有返回，已取消本次保存，请重试。');
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(timeoutError);
  }, timeoutMs);
  try {
    const response = await context.fetchPath(requestPath, {
      accept: 'application/x-subrip', signal: controller.signal,
    });
    const contentType = String(response.headers.get('content-type') || '').split(';')[0].trim();
    if (response.status !== 200 || contentType !== 'application/x-subrip') {
      let reason = '';
      try { reason = (await response.json()).detail || ''; } catch {}
      throw new Error(reason || `本地服务没有返回可保存的字幕（HTTP ${response.status || 0}）。`);
    }
    if (!response.body) throw new Error('本地服务返回了空的字幕文件。');
    const output = fs.createWriteStream(temporaryPath, { flags: 'wx' });
    await pipeline(Readable.fromWeb(response.body), output, { signal: controller.signal });
  } catch (error) {
    throw timedOut ? timeoutError : error;
  } finally {
    clearTimeout(timer);
  }
  const stats = await fs.promises.stat(temporaryPath);
  if (!stats.isFile() || stats.size <= 0) {
    throw new Error('本地服务返回了空的字幕文件。');
  }
}

module.exports = { downloadCreationTaskOutput, downloadSelectedSubtitles, SUBTITLE_RESPONSE_TIMEOUT_MS };
