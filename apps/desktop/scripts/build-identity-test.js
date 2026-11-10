#!/usr/bin/env node

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { compareIdentityPaths, generateBuildIdentity } = require('./generate-build-identity');

function write(root, relativePath, content) {
  const filePath = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}

function generate(root) {
  const outputPath = path.join(root, 'apps', 'desktop', 'build-identity.generated.json');
  return generateBuildIdentity({ repositoryRoot: root, outputPath });
}

function assertChangesDigest(root, first, relativePath) {
  const filePath = path.join(root, relativePath);
  const original = fs.readFileSync(filePath, 'utf8');
  fs.writeFileSync(filePath, `${original}-changed`);
  assert.notEqual(
    generate(root).candidateInputDigest,
    first.candidateInputDigest,
    `${relativePath} must change identity`,
  );
  fs.writeFileSync(filePath, original);
}

function isStrictlyOrdered(inputs, compare) {
  return inputs.every((input, index) => index === 0 || compare(inputs[index - 1].path, input.path) < 0);
}

function main() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-video-diary-build-identity-'));
  try {
    write(root, 'apps/desktop/main.js', 'main-v1');
    write(root, 'apps/desktop/device-identity.js', 'device-identity-v1');
    write(root, 'apps/desktop/runtime/supervisor.js', 'runtime-v1');
    write(root, 'apps/desktop/preload.js', 'preload-v1');
    write(root, 'apps/desktop/package.json', '{"build":{"files":["main.js"]}}');
    write(root, 'apps/desktop/assets/runtime.json', '{"mode":"local"}');
    write(root, `apps/desktop/dist/api-server${process.platform === 'win32' ? '/api-server.exe' : ''}`, 'api-v1');
    if (process.platform === 'win32') write(root, 'apps/desktop/dist/api-server/_internal/python311.dll', 'python-v1');
    if (process.platform === 'win32') write(root, 'apps/desktop/electron-builder.windows.cjs', 'windows-preview-config-v1');
    write(root, 'apps/desktop/dist/programmatic/yaohe.mjs', 'cli-v1');
    write(root, 'apps/desktop/dist/programmatic/yaohe-mcp.mjs', 'mcp-v1');
    write(root, 'apps/api/model_workers/voice.py', 'worker-v1');
    write(root, 'apps/api/agent_tools/diary_tools.ts', 'agent-tools-v1');
    write(root, 'apps/api/agent_runtime/host.mjs', 'sdk-bridge-v1');
    write(root, 'apps/api/agent_runtime/skills/local-edit/SKILL.md', 'edit-skill-v1');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/server.js', 'web-v1');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/BUILD_ID', 'build');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/app-path-routes-manifest.json', 'routes');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/server/app/production/page_client-reference-manifest.js', 'references');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/server/app/production/page.js', 'page');

    const pnpmElectronTarget = path.join(root, 'apps/desktop/.pnpm/electron@33/node_modules/electron');
    fs.mkdirSync(pnpmElectronTarget, { recursive: true });
    fs.writeFileSync(path.join(pnpmElectronTarget, 'package.json'), '{"name":"electron"}');
    fs.mkdirSync(path.join(root, 'apps/desktop/node_modules'), { recursive: true });
    fs.symlinkSync(pnpmElectronTarget, path.join(root, 'apps/desktop/node_modules/electron'), process.platform === 'win32' ? 'junction' : 'dir');

    const first = generate(root);
    const firstMetadata = fs.readFileSync(path.join(root, 'apps/desktop/build-identity.generated.json'), 'utf8');
    const repeated = generate(root);
    assert.equal(repeated.candidateInputDigest, first.candidateInputDigest, 'same packaged inputs must have one identity');
    assert.equal(fs.readFileSync(path.join(root, 'apps/desktop/build-identity.generated.json'), 'utf8'), firstMetadata, 'metadata must be deterministic');
    assert.equal(new Set(first.inputs.map((input) => input.path)).size, first.inputs.length, 'input paths must be unique');
    for (const inputPath of ['desktop/main.js', 'desktop/device-identity.js', 'desktop/preload.js', 'desktop/runtime/supervisor.js', 'desktop/package.json', 'desktop/assets/runtime.json']) {
      assert.equal(first.inputs.some((input) => input.path === inputPath), true, `${inputPath} must be auditable`);
    }
    for (const inputPath of [
      'agent-tools/diary_tools.ts',
      'programmatic/yaohe.mjs',
      'programmatic/yaohe-mcp.mjs',
      'agent_runtime/host.mjs',
      'agent_runtime/skills/local-edit/SKILL.md',
      'web/.next/BUILD_ID',
      'web/.next/app-path-routes-manifest.json',
      'web/.next/server/app/production/page_client-reference-manifest.js',
      'web/.next/server/app/production/page.js',
    ]) assert.equal(first.inputs.some((input) => input.path === inputPath), true, `${inputPath} must be covered`);
    assert.equal(isStrictlyOrdered(first.inputs, compareIdentityPaths), true, 'fixture inputs must use strict code-unit order');
    assert.equal(first.inputs.some((input) => input.path.includes('node_modules')), false, 'excluded pnpm dependencies must not enter the identity manifest');

    for (const relativePath of [
      'apps/desktop/main.js',
      'apps/desktop/dist/programmatic/yaohe.mjs',
      'apps/desktop/dist/programmatic/yaohe-mcp.mjs',
      'apps/desktop/device-identity.js',
      'apps/desktop/preload.js',
      'apps/desktop/runtime/supervisor.js',
      'apps/desktop/package.json',
      `apps/desktop/dist/api-server${process.platform === 'win32' ? '/api-server.exe' : ''}`,
      'apps/desktop/assets/runtime.json',
      'apps/api/agent_tools/diary_tools.ts',
      'apps/api/agent_runtime/host.mjs',
      'apps/api/agent_runtime/skills/local-edit/SKILL.md',
    ]) assertChangesDigest(root, first, relativePath);
    if (process.platform === 'win32') assertChangesDigest(root, first, 'apps/desktop/electron-builder.windows.cjs');
    if (process.platform === 'win32') assertChangesDigest(root, first, 'apps/desktop/dist/api-server/_internal/python311.dll');

    write(root, 'apps/web/.next/standalone-bundled/apps/web/server.js', 'web-v2');
    const changedIncluded = generate(root);
    assert.notEqual(changedIncluded.candidateInputDigest, first.candidateInputDigest, 'an included packaged input must change identity');

    write(root, 'apps/desktop/runtime/untracked.js', 'additional-runtime-v1');
    assert.notEqual(generate(root).candidateInputDigest, changedIncluded.candidateInputDigest, 'a new packaged runtime module must change identity');
    fs.unlinkSync(path.join(root, 'apps/desktop/runtime/untracked.js'));

    write(root, '.env', 'SECRET=must-not-be-hashed');
    write(root, 'apps/api/agent_runtime/tests/phase-deadlines.test.mjs', 'synthetic SDK fixture');
    write(root, 'apps/desktop/dist/old-output/ignored.txt', 'generated output');
    const changedExcluded = generate(root);
    assert.equal(changedExcluded.candidateInputDigest, changedIncluded.candidateInputDigest, 'credentials and output directories must not change identity');

    write(root, 'external-asset.txt', 'must-not-follow');
    if (process.platform === 'win32') {
      fs.mkdirSync(path.join(root, 'external-assets'));
      write(root, 'external-assets/payload.txt', 'must-not-follow');
      fs.symlinkSync(path.join(root, 'external-assets'), path.join(root, 'apps/desktop/assets/external-link.txt'), 'junction');
    } else {
      fs.symlinkSync('../../../external-asset.txt', path.join(root, 'apps/desktop/assets/external-link.txt'));
    }
    assert.throws(
      () => generate(root),
      /Packaged identity input must not contain a symlink: .*assets[\\/]external-link\.txt/,
      'a symlink inside a declared packaged input must fail closed',
    );

    console.log(`same-input digest: ${first.candidateInputDigest}`);
    console.log(`included-change digest: ${changedIncluded.candidateInputDigest}`);
    console.log('desktop build identity fixture: PASS');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main();
