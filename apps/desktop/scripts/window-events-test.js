const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createDesktopWindow } = require('../runtime/desktop-window');

const ipcMain = new EventEmitter();
const sent = [];
const external = [];
let currentWindow;
class BrowserWindow extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.webContents = new EventEmitter();
    this.webContents.send = (...args) => sent.push(args);
    this.webContents.setWindowOpenHandler = handler => { this.openExternal = handler; };
  }
  loadURL(url) {
    assert.equal(currentWindow, this, 'the application owns the window before navigation starts');
    this.url = url;
  }
}
const options = {
  BrowserWindow, ipcMain, preloadPath: '/fixture/preload.js', webOrigin: 'http://localhost:32101', isDev: false,
  shell: { openExternal: url => external.push(url) },
  onCreated: window => { currentWindow = window; },
  onClosed: window => { if (currentWindow === window) currentWindow = null; },
};
const first = createDesktopWindow(options);
assert.equal(first.options.webPreferences.contextIsolation, true);
assert.equal(first.options.webPreferences.nodeIntegration, false);
assert.equal(first.url, options.webOrigin);
let prevented = 0;
const escape = () => first.webContents.emit('before-input-event', { preventDefault: () => { prevented += 1; } }, { type: 'keyDown', key: 'Escape' });
ipcMain.emit('set-card-media-escape', { sender: {} }, 'foreign-card');
escape();
assert.equal(prevented, 0, 'a foreign renderer cannot arm Escape');
ipcMain.emit('set-card-media-escape', { sender: first.webContents }, 'current-card');
escape();
assert.equal(prevented, 1);
assert.deepEqual(sent, [['card-media-escape', 'current-card']]);
first.webContents.emit('enter-html-full-screen');
escape();
assert.equal(prevented, 1, 'native fullscreen keeps its normal Escape');
ipcMain.emit('set-card-media-escape', { sender: first.webContents }, 'current-card');
ipcMain.emit('set-card-media-escape', { sender: first.webContents }, null);
escape();
assert.equal(prevented, 1, 'focus/reload disarms the card');
assert.deepEqual(first.openExternal({ url: 'https://fixture.invalid' }), { action: 'deny' });
assert.deepEqual(external, ['https://fixture.invalid']);
first.emit('closed');
assert.equal(currentWindow, null);
assert.equal(ipcMain.listenerCount('set-card-media-escape'), 0);
const second = createDesktopWindow(options);
ipcMain.emit('set-card-media-escape', { sender: first.webContents }, 'stale-card');
second.webContents.emit('before-input-event', { preventDefault: () => { prevented += 1; } }, { type: 'keyDown', key: 'Escape' });
assert.equal(prevented, 1, 'a closed window cannot arm its replacement');
second.emit('closed');
assert.equal(ipcMain.listenerCount('set-card-media-escape'), 0);
console.log('desktop window events: PASS (ownership before navigation; sender/focus/fullscreen/closed-window Escape boundaries)');
