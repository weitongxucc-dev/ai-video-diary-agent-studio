// Public renderer channels remain stable; native modules own validation and I/O.
function registerDesktopIpc({ ipcMain, services, files, media, buildIdentity, getVersion, quit, checkForUpdates, companion, getWindow, webOrigin }) {
  if (companion) {
    for (const [channel, action] of Object.entries({
      'companion-status': () => companion.status(),
      'companion-configure': (input) => companion.configure(input),
      'companion-disable': () => companion.disable(),
      'companion-invitation': (input) => companion.invitation(input),
    })) {
      ipcMain.handle(channel, (event, input) => {
        if (event.sender !== getWindow()?.webContents || event.senderFrame !== event.sender.mainFrame
            || new URL(event.senderFrame.url).origin !== webOrigin) throw new Error('手机连接仅可从当前曜核工作台操作。');
        return action(input);
      });
    }
  }
  ipcMain.handle('get-api-port', () => services.getApiEndpoint().port);
  ipcMain.handle('open-directory', () => files.openDirectory());
  ipcMain.handle('open-file', () => files.openFile());
  ipcMain.handle('open-wav-file', () => files.openWavFile());
  const rejectedSave = () => ({ saved: false, error: '保存来源已失效，请返回当前应用窗口后重试。' });
  ipcMain.handle('save-rendered-video', (event, taskId, expectedVersionId) => {
    if (!media.checkSaveSender(event)) return rejectedSave();
    return media.saveRenderedVideo(taskId, expectedVersionId);
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
