const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { downloadCreationTaskOutput: defaultDownloadOutput, downloadSelectedSubtitles: defaultDownloadSubtitles, SUBTITLE_RESPONSE_TIMEOUT_MS } = require('./media-downloads');

function createNativeMedia({ showSaveDialog, getWindow, webOrigin, getMediaDirectory, getApiEndpoint, importMaterials, pickMaterialSources, recordLifecycle, downloadCreationTaskOutput = defaultDownloadOutput, downloadSelectedSubtitles = defaultDownloadSubtitles }) {
  const saveSourceError = '保存来源已失效，请返回当前应用窗口后重试。';

  function trustedFrameIdentity(frame) {
    // Electron frames can detach between native property reads during navigation.
    try {
      if (!frame || frame.isDestroyed() || frame.detached
          || frame.origin !== webOrigin || new URL(frame.url).origin !== webOrigin) return null;
      const identity = { processId: frame.processId, routingId: frame.routingId, frameTreeNodeId: frame.frameTreeNodeId };
      return Object.values(identity).every(Number.isInteger) ? identity : null;
    } catch {
      return null;
    }
  }

  function sameFrame(left, right) {
    return left.processId === right.processId && left.routingId === right.routingId
      && left.frameTreeNodeId === right.frameTreeNodeId;
  }

  function currentSaveWindow() {
    const window = getWindow();
    if (!window || window.isDestroyed()) return null;
    const contents = window.webContents;
    if (contents.isDestroyed()) return null;
    const frame = trustedFrameIdentity(contents.mainFrame);
    return frame ? { window, contents, frame } : null;
  }

  function checkSaveSender(event) {
    if (!event) return false;
    const current = currentSaveWindow();
    const senderFrame = trustedFrameIdentity(event.senderFrame);
    return Boolean(current && senderFrame && event.sender === current.contents
      && sameFrame(senderFrame, current.frame));
  }

  function captureSaveContext() {
    const initial = currentSaveWindow();
    if (!initial) return null;
    const session = initial.contents.session;
    let navigated = false;
    const onNavigation = (details) => {
      if (details.isMainFrame && !details.isSameDocument) navigated = true;
    };
    initial.contents.on('did-start-navigation', onNavigation);
    function assertCurrent() {
      const current = currentSaveWindow();
      if (navigated || !current || current.window !== initial.window
          || current.contents !== initial.contents || !sameFrame(current.frame, initial.frame)) {
        throw new Error(saveSourceError);
      }
    }
    return {
      assertCurrent,
      fetchPath(requestPath, { accept, signal }) {
        assertCurrent();
        const { port, state } = getApiEndpoint();
        if (!Number.isInteger(port) || port <= 0 || state !== 'ready') {
          throw new Error('本地视频服务尚未就绪，请等待服务恢复后重试。');
        }
        const url = new URL(requestPath, webOrigin);
        if (url.origin !== webOrigin) throw new Error(saveSourceError);
        return session.fetch(url.href, {
          method: 'GET', credentials: 'include', redirect: 'error', cache: 'no-store',
          headers: { Accept: accept }, ...(signal ? { signal } : {}),
        });
      },
      dispose() { initial.contents.removeListener('did-start-navigation', onNavigation); },
    };
  }

  async function saveRenderedVideo(taskId) {
    if (typeof taskId !== 'string' || !/^task_[a-zA-Z0-9_-]{1,80}$/.test(taskId)) {
      return { saved: false, error: '创作任务标识无效。' };
    }
    const context = captureSaveContext();
    if (!context) return { saved: false, error: saveSourceError };
    try {
      const result = await showSaveDialog({
        title: '保存初版成片',
        defaultPath: path.join(getMediaDirectory('videos'), `${taskId}.mp4`),
        filters: [{ name: 'MP4 视频', extensions: ['mp4'] }],
      });
      if (result.canceled || !result.filePath) return { saved: false, canceled: true };
      const temporaryPath = `${result.filePath}.partial-${crypto.randomUUID()}`;
      try {
        context.assertCurrent();
        await downloadCreationTaskOutput(context, taskId, temporaryPath);
        context.assertCurrent();
        await fs.promises.rename(temporaryPath, result.filePath);
        recordLifecycle('rendered-video-saved', { taskId, fileName: path.basename(result.filePath) });
        return { saved: true, fileName: path.basename(result.filePath) };
      } catch (error) {
        await fs.promises.unlink(temporaryPath).catch(() => undefined);
        recordLifecycle('rendered-video-save-failed', {
          taskId,
          errorCode: error.code || error.name || 'unknown',
        });
        return { saved: false, error: error.message || '成片保存失败。' };
      }
    } finally { context.dispose(); }
  }

  async function saveCoverImage(taskId, coverId) {
    if (typeof taskId !== 'string' || !/^task_[a-zA-Z0-9_-]{1,80}$/.test(taskId)
        || typeof coverId !== 'string' || !/^cover_[a-zA-Z0-9_-]{1,80}$/.test(coverId)) {
      return { saved: false, error: '创作任务或封面标识无效。' };
    }
    const context = captureSaveContext();
    if (!context) return { saved: false, error: saveSourceError };
    try {
      const result = await showSaveDialog({
        title: '保存封面',
        defaultPath: path.join(getMediaDirectory('pictures'), `${taskId}-cover.png`),
        filters: [{ name: 'PNG 图片', extensions: ['png'] }],
      });
      if (result.canceled || !result.filePath) return { saved: false, canceled: true };
      const temporaryPath = `${result.filePath}.partial-${crypto.randomUUID()}`;
      try {
        context.assertCurrent();
        await downloadCreationTaskOutput(context, taskId, temporaryPath, coverId);
        context.assertCurrent();
        await fs.promises.rename(temporaryPath, result.filePath);
        recordLifecycle('cover-image-saved', { taskId, coverId, fileName: path.basename(result.filePath) });
        return { saved: true, fileName: path.basename(result.filePath) };
      } catch (error) {
        await fs.promises.unlink(temporaryPath).catch(() => undefined);
        recordLifecycle('cover-image-save-failed', { taskId, coverId, errorCode: error.code || error.name || 'unknown' });
        return { saved: false, error: error.message || '封面保存失败。' };
      }
    } finally { context.dispose(); }
  }

  async function saveSubtitleFile(selection) {
    const idPattern = /^[a-zA-Z0-9_-]{1,120}$/;
    const taskPattern = /^task_[a-zA-Z0-9_-]{1,80}$/;
    if (!selection || typeof selection !== 'object'
        || !taskPattern.test(selection.taskId)
        || typeof selection.expectedVersionId !== 'string' || !idPattern.test(selection.expectedVersionId)
        || !['zh', 'en', 'zh-en'].includes(selection.mode)
        || (selection.mode !== 'en' && (typeof selection.zhTrackId !== 'string' || !idPattern.test(selection.zhTrackId)))
        || (selection.mode !== 'zh' && (typeof selection.enTrackId !== 'string' || !idPattern.test(selection.enTrackId)))
        || (selection.mode === 'zh' && selection.enTrackId)
        || (selection.mode === 'en' && selection.zhTrackId)) {
      return { saved: false, error: '字幕保存选择无效，请重新选择语言和当前字幕轨。' };
    }
    const suffix = { zh: 'zh', en: 'en', 'zh-en': 'zh-en' }[selection.mode];
    const context = captureSaveContext();
    if (!context) return { saved: false, error: saveSourceError };
    try {
      const result = await showSaveDialog({
        title: '保存字幕',
        defaultPath: path.join(getMediaDirectory('videos'), `${selection.taskId}-${suffix}.srt`),
        filters: [{ name: 'SRT 字幕', extensions: ['srt'] }],
      });
      if (result.canceled || !result.filePath) return { saved: false, canceled: true };
      const temporaryPath = `${result.filePath}.partial-${crypto.randomUUID()}`;
      try {
        context.assertCurrent();
        await downloadSelectedSubtitles(context, selection, temporaryPath, SUBTITLE_RESPONSE_TIMEOUT_MS);
        context.assertCurrent();
        await fs.promises.rename(temporaryPath, result.filePath);
        recordLifecycle('subtitle-file-saved', { taskId: selection.taskId, mode: selection.mode, fileName: path.basename(result.filePath) });
        return { saved: true, fileName: path.basename(result.filePath) };
      } catch (error) {
        await fs.promises.unlink(temporaryPath).catch(() => undefined);
        recordLifecycle('subtitle-file-save-failed', { taskId: selection.taskId, mode: selection.mode, errorCode: error.code || error.name || 'unknown' });
        return { saved: false, error: error.message || '字幕保存失败。' };
      }
    } finally { context.dispose(); }
  }

  async function importSelectedMaterials(identity = {}) {
    const { port: apiPort, state } = getApiEndpoint();
    if (!Number.isInteger(apiPort) || apiPort <= 0 || state !== 'ready') {
      return { canceled: false, copied: [], skipped: [], failures: [], error: '本地视频服务尚未就绪，请等待服务恢复后重试。' };
    }
    try {
      const result = await importMaterials({ port: apiPort, pickSources: pickMaterialSources, identity });
      recordLifecycle('material-import-finished', {
        copied: result.copied.length,
        skipped: result.skipped.length,
        failed: result.failures.length,
        canceled: result.canceled,
        cleanupPending: Array.isArray(result.cleanup) ? result.cleanup.length : 0,
      });
      if (Array.isArray(result.cleanup) && result.cleanup.length > 0) {
        // A run where every file failed leaves cleanup residue too, and that
        // residue is still worth telling the user about — it is not hidden just
        // because nothing was imported. What the wording must not do is claim
        // the import succeeded, which would contradict the failure list beside
        // it, so it states only the residue and how to deal with it.
        result.warning = '部分临时文件未能自动清理，残留文件对素材接入不可见，可稍后重试导入或联系支持。';
      }
      return result;
    } catch (error) {
      recordLifecycle('material-import-failed', { errorCode: error.code || error.name || 'unknown' });
      return {
        canceled: false,
        copied: [],
        skipped: [],
        failures: [],
        error: error.message || '导入失败，源文件保持不变。',
      };
    }
  }

  return { saveRenderedVideo, saveCoverImage, saveSubtitleFile, checkSaveSender, importSelectedMaterials };
}

module.exports = { createNativeMedia };
