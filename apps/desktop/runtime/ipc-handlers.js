// Public renderer channels remain stable; native modules own validation and I/O.
function registerDesktopIpc({ ipcMain, services, files, media, buildIdentity, getVersion, quit, checkForUpdates }) {
  ipcMain.handle('get-api-port', () => services.getApiEndpoint().port);
  ipcMain.handle('open-directory', () => files.openDirectory());
  ipcMain.handle('open-file', () => files.openFile());
  ipcMain.handle('open-wav-file', () => files.openWavFile());
  const rejectedSave = () => ({ saved: false, error: '保存来源已失效，请返回当前应用窗口后重试。' });
  ipcMain.handle('save-rendered-video', (event, taskId) => {
    if (!media.checkSaveSender(event)) return rejectedSave();
    return media.saveRenderedVideo(taskId);
  });
  ipcMain.handle('save-cover-image', (event, taskId, coverId) => {
    if (!media.checkSaveSender(event)) return rejectedSave();
    return media.saveCoverImage(taskId, coverId);
  });
  ipcMain.handle('save-subtitle-file', (event, selection) => {
    if (!media.checkSaveSender(event)) return rejectedSave();
    return media.saveSubtitleFile(selection);
  });
  // The identity is forwarded; renderer paths never choose import source/destination.
  ipcMain.handle('import-materials', (_event, identity = {}) => media.importSelectedMaterials(identity));
  ipcMain.handle('open-path', (_event, filePath) => files.openPath(filePath));
  ipcMain.handle('get-version', () => getVersion());
  ipcMain.handle('quit', () => quit());
  ipcMain.handle('check-for-updates', () => { checkForUpdates(false); });
  ipcMain.handle('get-service-status', () => ({ ...services.getServiceStatus(), buildIdentity }));
  ipcMain.handle('get-runtime-info', () => ({ buildIdentity, logFileName: 'desktop-runtime.jsonl' }));
}

module.exports = { registerDesktopIpc };
