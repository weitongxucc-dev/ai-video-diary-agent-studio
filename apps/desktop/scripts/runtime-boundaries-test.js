// Architecture and public-boundary checks. Requiring runtime modules must not
// initialize Electron, access a profile, start children or bind any service.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { EventEmitter } = require('node:events');
const { INPUT_SOURCES } = require('./generate-build-identity');

const desktopRoot = path.resolve(__dirname, '..');
const runtimeRoot = path.join(desktopRoot, 'runtime');
const runtimeFiles = fs.readdirSync(runtimeRoot).filter(name => name.endsWith('.js')).sort();
const mainSource = fs.readFileSync(path.join(desktopRoot, 'main.js'), 'utf8');
for (const symbol of [
  'startApiServer', 'startNextServer', 'waitForHttpService', 'importMaterials',
  'downloadSelectedSubtitles', 'downloadCreationTaskOutput', 'resolveTrustedMaterialDirectory',
  'commitMaterialStaging', 'runtimeLogPath', 'loadBuildIdentity',
]) assert.ok(!new RegExp(`(?:async )?function ${symbol}\\(`).test(mainSource), `${symbol} implementation is retired from assembly`);
for (const state of ['apiProcess', 'nextProcess', 'apiStopping', 'nextStopping', 'lifecycleLogStream', 'serviceStates']) {
  assert.ok(!new RegExp(`(?:const|let) ${state}\\b`).test(mainSource), `${state} is owned only by its runtime module`);
}
const originalLoad = Module._load;
Module._load = function guardedLoad(request, parent, isMain) {
  assert.notEqual(request, 'electron', 'runtime modules receive their native dependencies from assembly');
  assert.notEqual(request, 'electron-updater');
  assert.ok(!request.endsWith('/main') && !request.endsWith('/main.js'), 'runtime must not import assembly');
  if (request === 'fs') return new Proxy(fs, {
    get(target, key) {
      if (typeof target[key] === 'function') return () => { throw new Error(`filesystem access on import: ${key}`); };
      return target[key];
    },
  });
  if (request === 'child_process' || request === 'net' || request === 'http') {
    return new Proxy({}, { get: (_target, key) => () => { throw new Error(`process/network access on import: ${request}.${key}`); } });
  }
  return originalLoad.call(this, request, parent, isMain);
};
try {
  for (const name of runtimeFiles) {
    const modulePath = path.join(runtimeRoot, name);
    assert.ok(Object.keys(require(modulePath)).length > 0, `${name} exposes an explicit module entry`);
  }
} finally {
  Module._load = originalLoad;
  // The import guard must never contaminate later behavioral checks.
  for (const name of runtimeFiles) delete require.cache[path.join(runtimeRoot, name)];
  delete require.cache[path.join(desktopRoot, 'device-identity.js')];
}

const packageConfig = JSON.parse(fs.readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'));
assert.equal(packageConfig.build.appId, 'com.aivideodiary.studio');
assert.ok(packageConfig.build.files.includes('runtime/**/*.js'), 'all runtime modules enter the app package');
const runtimeInput = INPUT_SOURCES.find(input => input.identityPath === 'desktop/runtime');
assert.equal(runtimeInput.relativePath, 'apps/desktop/runtime');
for (const name of runtimeFiles) assert.equal(runtimeInput.include(name), true, `${name} enters candidate inputs`);

const { registerDesktopIpc } = require('../runtime/ipc-handlers');
const { createOutputLogging } = require('../runtime/output-logging');
const { createServiceSupervisor } = require('../runtime/service-supervisor');
const handlers = new Map();
let forwardedIdentity;
let updateSilent;
let sourceSelections = 0;
const identity = { localMemberToken: 'fixture-member', authorization: 'Bearer fixture-access' };
registerDesktopIpc({
  ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
  services: { getApiEndpoint: () => ({ port: 12345 }), getServiceStatus: () => ({ api: { state: 'ready' } }) },
  files: { openFile: () => { sourceSelections += 1; return '/native/chosen'; } },
  media: { importSelectedMaterials: value => { forwardedIdentity = value; } },
  getVersion: () => 'fixture-version', buildIdentity: 'fixture-candidate',
  checkForUpdates: silent => { updateSilent = silent; },
});
assert.deepEqual([...handlers.keys()].sort(), [
  'get-api-port', 'open-directory', 'open-file', 'open-wav-file', 'save-rendered-video',
  'save-cover-image', 'save-subtitle-file', 'import-materials', 'open-path', 'get-version',
  'quit', 'check-for-updates', 'get-service-status', 'get-runtime-info',
].sort());
assert.equal(handlers.get('open-file')({}, '/renderer/ignored'), '/native/chosen');
assert.equal(sourceSelections, 1);
handlers.get('import-materials')({}, identity);
assert.equal(forwardedIdentity, identity, 'IPC forwards the original member/session identity');
handlers.get('check-for-updates')();
assert.equal(updateSilent, false);
assert.deepEqual(handlers.get('get-runtime-info')(), { buildIdentity: 'fixture-candidate', logFileName: 'desktop-runtime.jsonl' });

const stdout = new EventEmitter();
const stderr = new EventEmitter();
const lifecycle = [];
const failures = [];
const guards = createOutputLogging({
  stdout, stderr, handleStartupStderrError: () => {},
  recordLifecycle: (event, fields) => lifecycle.push({ event, ...fields }),
  onMainFailure: (stream, error) => failures.push({ stream, error }),
  onServiceFailure: (service, error) => failures.push({ service, error }),
});
guards.installMainLogStreamGuards();
stdout.emit('error', Object.assign(new Error('closed'), { code: 'EPIPE' }));
assert.equal(failures.length, 0, 'a closed parent pipe does not terminate services');
stderr.emit('error', Object.assign(new Error('I/O'), { code: 'EIO' }));
assert.equal(failures[0].stream, 'stderr');
const childOutput = new EventEmitter();
guards.attachChildOutput('api', childOutput, 'api:err', () => {});
childOutput.emit('error', Object.assign(new Error('closed'), { code: 'EPIPE' }));
childOutput.emit('error', Object.assign(new Error('I/O'), { code: 'EIO' }));
assert.equal(lifecycle.length, 2);
assert.equal(failures[1].service, 'api');

const supervisor = createServiceSupervisor({ recordLifecycle: () => {} });
const snapshot = supervisor.getServiceStatus();
snapshot.api.state = 'ready';
assert.equal(supervisor.getApiEndpoint().state, 'not-started', 'a status reader cannot mutate supervisor state');
console.log(`desktop runtime boundaries: PASS (${runtimeFiles.length} import-safe packaged modules; stable IPC; private service state; log pipe failures)`);
