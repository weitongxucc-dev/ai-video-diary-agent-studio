// Exact desktop import section, real isolated filesystem and loopback HTTP.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { pipeline } = require('node:stream/promises');

async function main() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wil126-copy-'));
  const sources = path.join(root, 'sources');
  const materials = path.join(root, 'materials');
  fs.mkdirSync(sources);
  fs.mkdirSync(materials);
  const source = path.join(sources, 'clip.mp4');
  const bytes = Buffer.from('complete copied payload');
  fs.writeFileSync(source, bytes);
  fs.writeFileSync(path.join(materials, 'clip.mp4'), 'existing file');
  const originalStat = fs.statSync(source, { bigint: true });
  const binding = {
    bound_to_current_workspace: true, binding_available: true,
    directory_state: 'ready', bound_directory: materials, configured_directory: materials,
  };
  const confirmations = [];
  const receivedIdentities = [];
  let status = 200;
  const server = http.createServer((request, reply) => {
    receivedIdentities.push({ member: request.headers['x-local-member-token'], authorization: request.headers.authorization });
    if (request.method === 'GET' && request.url === '/api/production/materials/intake') {
      reply.writeHead(200, { 'content-type': 'application/json' });
      reply.end(JSON.stringify(binding));
      return;
    }
    if (request.method !== 'POST' || request.url !== '/api/production/materials/intake/copy-complete') {
      reply.writeHead(404);
      reply.end();
      return;
    }
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      confirmations.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      reply.writeHead(status, { 'content-type': 'application/json' });
      reply.end(JSON.stringify({ confirmed: status === 200 }));
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const sourceCode = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
    const start = sourceCode.indexOf('// ── Material import');
    const end = sourceCode.indexOf('// ── IPC Handlers', start);
    assert.ok(start >= 0 && end > start);
    const context = { fs, http, path, crypto, pipeline, Buffer, setTimeout, clearTimeout,
      MATERIAL_IMPORT_EXTENSIONS: new Set(['.mp4', '.mov', '.m4v']),
      MATERIAL_STAGING_SEGMENTS: ['_已处理', '.import-staging'],
      MATERIAL_NAME_ATTEMPTS: 200, MATERIAL_BINDING_TIMEOUT_MS: 8000,
      recordLifecycle: () => {},
    };
    vm.runInNewContext(sourceCode.slice(start, end) + '\nglobalThis.runImport = importMaterials;', context);
    let importHandler;
    context.ipcMain = { handle: (_name, callback) => { importHandler = callback; } };
    context.apiPort = server.address().port;
    context.serviceStates = { api: { state: 'ready' } };
    context.pickMaterialSources = async () => [source];
    const handlerStart = sourceCode.indexOf("ipcMain.handle('import-materials'");
    const handlerEnd = sourceCode.indexOf("ipcMain.handle('", handlerStart + 10);
    assert.ok(handlerStart >= 0 && handlerEnd > handlerStart);
    vm.runInNewContext(sourceCode.slice(handlerStart, handlerEnd), context);
    let bridge;
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'preload.js'), 'utf8'), {
      process: { platform: 'darwin' },
      require: () => ({
        contextBridge: { exposeInMainWorld: (_name, api) => { bridge = api; } },
        ipcRenderer: { invoke: (name, identity) => {
          assert.equal(name, 'import-materials');
          return importHandler({}, identity);
        } },
      }),
    });
    const options = { port: server.address().port, pickSources: async () => [source],
      identity: { localMemberToken: 'isolated-member-token', authorization: 'Bearer isolated-access-token' },
    };
    const first = await bridge.importMaterials(options.identity);
    assert.equal(first.copied.length, 1);
    assert.equal(first.failures.length, 0);
    assert.ok(receivedIdentities.length >= 4);
    for (const identity of receivedIdentities) {
      assert.equal(identity.member, options.identity.localMemberToken);
      assert.equal(identity.authorization, options.identity.authorization);
    }
    const confirmation = confirmations[0];
    assert.equal(confirmation.relative_path, 'clip (1).mp4');
    assert.equal(confirmation.confirmation_source, 'app_import');
    const copiedPath = path.join(materials, confirmation.relative_path);
    assert.deepEqual(fs.readFileSync(copiedPath), bytes);
    const copiedStat = fs.statSync(copiedPath, { bigint: true });
    assert.deepEqual(confirmation.expected_revision, {
      size_bytes: copiedStat.size.toString(), mtime_ns: copiedStat.mtimeNs.toString(),
      ctime_ns: copiedStat.ctimeNs.toString(), inode: copiedStat.ino.toString(),
    });
    console.log('PASS current credentials on binding/revalidation/confirmation; committed exact revision and collision name');

    status = 409;
    const second = await bridge.importMaterials(options.identity);
    assert.equal(second.copied.length, 0);
    assert.equal(second.failures.length, 1);
    assert.match(second.failures[0].error, /文件已复制.*接收确认未保存.*HTTP 409/);
    assert.deepEqual(fs.readFileSync(path.join(materials, confirmations[1].relative_path)), bytes);
    assert.deepEqual(fs.readFileSync(source), bytes);
    const after = fs.statSync(source, { bigint: true });
    for (const field of ['size', 'mtimeNs', 'ctimeNs', 'ino']) assert.equal(after[field], originalStat[field]);
    assert.equal(fs.readFileSync(path.join(materials, 'clip.mp4'), 'utf8'), 'existing file');
    console.log('PASS rejected confirmation preserves copy and original, reports no intake success');
  } finally {
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
