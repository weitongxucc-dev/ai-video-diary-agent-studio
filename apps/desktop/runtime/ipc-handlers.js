// Public renderer channels remain stable; native modules own validation and I/O.
function registerDesktopIpc({ ipcMain, services, files, media, buildIdentity, getVersion, quit, checkForUpdates }) {
  ipcMain.handle('get-api-port', () => services.getApiEndpoint().port);
  ipcMain.handle('open-directory', () => files.openDirectory());
  ipcMain.handle('open-file', () => files.openFile());
  ipcMain.handle('open-wav-file', () => files.openWavFile());
  ipcMain.handle('save-rendered-video', (_event, taskId) => media.saveRenderedVideo(taskId));
  ipcMain.handle('save-cover-image', (_event, taskId, coverId) => media.saveCoverImage(taskId, coverId));
  ipcMain.handle('save-subtitle-file', (_event, selection) => media.saveSubtitleFile(selection));
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
