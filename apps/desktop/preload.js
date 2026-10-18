const { contextBridge, ipcRenderer } = require('electron');
// A navigation/reload must not retain the previous document's focused card.
ipcRenderer.send('set-card-media-escape', null);

contextBridge.exposeInMainWorld('electronAPI', {
  // App info
  platform: process.platform,
  isElectron: true,

  // API server info
  getApiPort: () => ipcRenderer.invoke('get-api-port'),
  getServiceStatus: () => ipcRenderer.invoke('get-service-status'),

  companionStatus: () => ipcRenderer.invoke('companion-status'),
  configureCompanion: (input) => ipcRenderer.invoke('companion-configure', input),
  disableCompanion: () => ipcRenderer.invoke('companion-disable'),
  companionInvitation: (input) => ipcRenderer.invoke('companion-invitation', input),

  // File system
  openDirectory: () => ipcRenderer.invoke('open-directory'),
  openFile: () => ipcRenderer.invoke('open-file'),
  openWavFile: () => ipcRenderer.invoke('open-wav-file'),
  // No path argument: the shell resolves the destination from the controlled
  // intake binding, so the renderer cannot name a write target.
  importMaterials: (identity) => ipcRenderer.invoke('import-materials', identity),
  openPath: (filePath) => ipcRenderer.invoke('open-path', filePath),
  saveRenderedVideo: (taskId) => ipcRenderer.invoke('save-rendered-video', taskId),
  saveCoverImage: (taskId, coverId) => ipcRenderer.invoke('save-cover-image', taskId, coverId),
  saveSubtitleFile: (selection) => ipcRenderer.invoke('save-subtitle-file', selection),

  // Help
  onOpenHelp: (callback) => ipcRenderer.on('open-help', callback),
  setCardMediaEscape: (cardId) => ipcRenderer.send('set-card-media-escape', cardId),
  onCardMediaEscape: (callback) => {
    const listener = (_event, cardId) => callback(cardId);
    ipcRenderer.on('card-media-escape', listener);
    return () => ipcRenderer.removeListener('card-media-escape', listener);
  },

  // App lifecycle
  getVersion: () => ipcRenderer.invoke('get-version'),
  quit: () => ipcRenderer.invoke('quit'),
});
