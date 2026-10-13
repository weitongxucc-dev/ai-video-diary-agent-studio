// Actual nested packaged-resource layout, Node module resolver, and optional
// installed Pi/Jiti extension loader. No account settings or model session.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readdir, mkdir, copyFile, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const execute = promisify(execFile);
const sourceRoot = fileURLToPath(new URL('../', import.meta.url));

async function resourceTree(t) {
  const root = await mkdtemp(join(tmpdir(), 'yaohe-r6-resources-'));
  const tools = join(root, 'Resources', 'agent-tools');
  await mkdir(tools, {recursive: true});
  // This is the same **/*.ts filter declared by electron-builder. Directory
  // structure is preserved; the test never flattens imports into one file.
  async function copyTree(source, target) {
    for (const entry of await readdir(source, {withFileTypes: true})) {
      if (entry.isDirectory()) {
        await mkdir(join(target, entry.name), {recursive: true});
        await copyTree(join(source, entry.name), join(target, entry.name));
      } else if (entry.isFile() && entry.name.endsWith('.ts')) {
        await copyFile(join(source, entry.name), join(target, entry.name));
      }
    }
  }
  await copyTree(sourceRoot, tools);
  t.after(() => rm(root, {recursive: true, force: true}));
  return {root, extension: join(tools, 'diary_tools.ts'), environment: {
    PATH: process.env.PATH, HOME: root, TMPDIR: root, PI_CODING_AGENT_DIR: join(root, 'agent'), PI_OFFLINE: '1',
  }};
}

test('copied extension dynamically imports its nested modules and performs authenticated HTTP reads', async t => {
  const tree = await resourceTree(t), calls = [];
  const server = createServer((request, response) => {
    calls.push({path: request.url, method: request.method, token: request.headers['x-local-member-token']});
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({id: 'synthetic-profile', revision: 2, common_expressions: [], banned_words: [], style_preferences: {tone: 'plain'}}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const {stdout} = await execute(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e',
    'const {readCreatorMemoryFacts}=await import(process.argv[1]); console.log(JSON.stringify(await readCreatorMemoryFacts()));',
    pathToFileURL(tree.extension).href], {cwd: tree.root, env: {...tree.environment,
    AI_VIDEO_DIARY_AGENT_API_BASE: `http://127.0.0.1:${server.address().port}`,
    AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN: 'synthetic-member'}, timeout: 10000});
  assert.deepEqual(JSON.parse(stdout).style_preferences, {tone: 'plain'});
  assert.deepEqual(calls, [{path: '/api/assistant/memory', method: 'GET', token: 'synthetic-member'}]);
});

test('installed Pi loader registers the copied extension without a model session', {skip: !process.env.PI_TEST_LOADER_MODULE}, async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath, ['--input-type=module', '-e',
    `globalThis.fetch=async()=>{throw new Error('network forbidden during extension loading')};
const {loadExtensions}=await import(process.argv[1]);
const loaded=await loadExtensions([process.argv[2]],process.cwd());
console.log(JSON.stringify({errors:loaded.errors,paths:loaded.extensions.map(e=>e.resolvedPath),tools:loaded.extensions.flatMap(e=>[...e.tools.keys()])}));`,
    process.env.PI_TEST_LOADER_MODULE, tree.extension], {cwd: tree.root,
    env: {...tree.environment, AI_VIDEO_DIARY_AGENT_TASK_ID: 'task_synthetic'}, timeout: 15000});
  const result = JSON.parse(stdout);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.paths, [tree.extension]);
  assert.ok(result.tools.includes('read_current_task') && result.tools.includes('read_typed_edit_request'));
  assert.ok(result.tools.includes('read_selected_material') && result.tools.includes('read_media_script_capabilities'));
});
