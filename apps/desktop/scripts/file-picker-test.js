'use strict';
// Pure IPC contract fixture: never require Electron or start the desktop app.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const main = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');
const preload = fs.readFileSync(path.join(__dirname, '../preload.js'), 'utf8');
async function run() {
  for (const [channel, property] of [['open-directory', 'openDirectory'], ['open-file', 'openFile'], ['open-wav-file', 'openFile']]) {
    const start = main.indexOf(`  ipcMain.handle('${channel}',`);
    assert(start >= 0);
    const end = main.indexOf('\n  ipcMain.handle(', start + 1);
    let handler;
    let options;
    let result = { canceled: false, filePaths: ['/fixture/chosen'] };
    vm.runInNewContext(main.slice(start, end), {
      ipcMain: { handle: (name, fn) => { assert.equal(name, channel); handler = fn; } },
      mainWindow: {}, dialog: { showOpenDialog: async (_window, opts) => { options = opts; return result; } },
    });
    assert.equal(await handler({}, '/renderer/must-not-be-used'), '/fixture/chosen');
    assert.deepEqual(Array.from(options.properties), [property]);
    assert.equal(options.defaultPath, undefined);
    result = { canceled: true, filePaths: ['/fixture/ignored'] };
    assert.equal(await handler(), null);
    result = { canceled: false, filePaths: [] };
    assert.equal(await handler(), null);
  }
  let bridge;
  const calls = [];
  vm.runInNewContext(preload, { process: { platform: 'fixture' }, require: () => ({
    contextBridge: { exposeInMainWorld: (_name, api) => { bridge = api; } },
    ipcRenderer: { invoke: (...args) => { calls.push(args); return Promise.resolve(null); } },
  }) });
  await bridge.openFile('/renderer/must-not-be-used');
  assert.deepEqual(calls, [['open-file']]);
  console.log('PASS file/directory/WAV native IPC contracts, cancellation and path-free preload');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
