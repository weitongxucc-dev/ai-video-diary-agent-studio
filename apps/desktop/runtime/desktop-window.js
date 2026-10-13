function createDesktopWindow({ BrowserWindow, ipcMain, shell, preloadPath, webOrigin, isDev, onCreated, onClosed }) {
  const isMac = process.platform === 'darwin';
  const window = new BrowserWindow({
    width: 1360,
    height: 840,
    minWidth: 1024,
    minHeight: 700,
    title: '曜核1.0',
    backgroundColor: '#f7f7f5',
    show: true,
    ...(isMac ? {
      titleBarStyle: 'hiddenInset',
      trafficLightPosition: { x: 14, y: 15 },
      vibrancy: 'sidebar',
      visualEffectState: 'active',
    } : {}),
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  onCreated(window);
  let cardMediaEscape = null;
  const setCardMediaEscape = (event, cardId) => {
    if (event.sender === window.webContents) cardMediaEscape = typeof cardId === 'string' ? cardId : null;
  };
  ipcMain.on('set-card-media-escape', setCardMediaEscape);
  window.webContents.on('enter-html-full-screen', () => { cardMediaEscape = null; });
  window.webContents.on('before-input-event', (event, input) => {
    // Native media controls consume Escape before the page's DOM listener.
    // Only an armed object card takes it; fullscreen keeps its normal Escape.
    if (input.type === 'keyDown' && input.key === 'Escape' && cardMediaEscape) {
      event.preventDefault();
      window.webContents.send('card-media-escape', cardMediaEscape);
    }
  });

  window.on('closed', () => {
    ipcMain.removeListener('set-card-media-escape', setCardMediaEscape);
    onClosed(window);
  });

  if (isDev) {
    window.loadURL(webOrigin);
    window.webContents.openDevTools({ mode: 'detach' });
  } else {
    window.loadURL(webOrigin);
  }

  // Open external links in browser
  window.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  return window;
}

module.exports = { createDesktopWindow };
