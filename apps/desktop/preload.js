const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // App info
  platform: process.platform,
  isElectron: true,

  // API server info
  getApiPort: () => ipcRenderer.invoke('get-api-port'),
  getServiceStatus: () => ipcRenderer.invoke('get-service-status'),

  // File system
  openDirectory: () => ipcRenderer.invoke('open-directory'),
  openWavFile: () => ipcRenderer.invoke('open-wav-file'),
  openPath: (filePath) => ipcRenderer.invoke('open-path', filePath),
  saveRenderedVideo: (taskId) => ipcRenderer.invoke('save-rendered-video', taskId),
  saveCoverImage: (taskId, coverId) => ipcRenderer.invoke('save-cover-image', taskId, coverId),

  // Help
  onOpenHelp: (callback) => ipcRenderer.on('open-help', callback),

  // App lifecycle
  getVersion: () => ipcRenderer.invoke('get-version'),
  quit: () => ipcRenderer.invoke('quit'),
});
