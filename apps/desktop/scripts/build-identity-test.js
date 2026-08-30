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
    write(root, 'apps/desktop/preload.js', 'preload-v1');
    write(root, 'apps/desktop/package.json', '{"build":{"files":["main.js"]}}');
    write(root, 'apps/desktop/assets/runtime.json', '{"mode":"local"}');
    write(root, 'apps/desktop/dist/api-server', 'api-v1');
    write(root, 'apps/api/model_workers/voice.py', 'worker-v1');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/server.js', 'web-v1');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/BUILD_ID', 'build');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/app-path-routes-manifest.json', 'routes');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/server/app/production/page_client-reference-manifest.js', 'references');
    write(root, 'apps/web/.next/standalone-bundled/apps/web/.next/server/app/production/page.js', 'page');

    const pnpmElectronTarget = path.join(root, 'apps/desktop/.pnpm/electron@33/node_modules/electron');
    fs.mkdirSync(pnpmElectronTarget, { recursive: true });
    fs.writeFileSync(path.join(pnpmElectronTarget, 'package.json'), '{"name":"electron"}');
    fs.mkdirSync(path.join(root, 'apps/desktop/node_modules'), { recursive: true });
    fs.symlinkSync('../.pnpm/electron@33/node_modules/electron', path.join(root, 'apps/desktop/node_modules/electron'));

    const first = generate(root);
    const firstMetadata = fs.readFileSync(path.join(root, 'apps/desktop/build-identity.generated.json'), 'utf8');
    const repeated = generate(root);
    assert.equal(repeated.candidateInputDigest, first.candidateInputDigest, 'same packaged inputs must have one identity');
    assert.equal(fs.readFileSync(path.join(root, 'apps/desktop/build-identity.generated.json'), 'utf8'), firstMetadata, 'metadata must be deterministic');
    assert.equal(new Set(first.inputs.map((input) => input.path)).size, first.inputs.length, 'input paths must be unique');
    for (const inputPath of ['desktop/main.js', 'desktop/preload.js', 'desktop/package.json', 'desktop/assets/runtime.json']) {
      assert.equal(first.inputs.some((input) => input.path === inputPath), true, `${inputPath} must be auditable`);
    }
    for (const inputPath of [
      'web/.next/BUILD_ID',
      'web/.next/app-path-routes-manifest.json',
      'web/.next/server/app/production/page_client-reference-manifest.js',
      'web/.next/server/app/production/page.js',
    ]) assert.equal(first.inputs.some((input) => input.path === inputPath), true, `${inputPath} must be covered`);
    assert.equal(isStrictlyOrdered(first.inputs, compareIdentityPaths), true, 'fixture inputs must use strict code-unit order');
    assert.equal(first.inputs.some((input) => input.path.includes('node_modules')), false, 'excluded pnpm dependencies must not enter the identity manifest');

    for (const relativePath of [
      'apps/desktop/main.js',
      'apps/desktop/preload.js',
      'apps/desktop/package.json',
      'apps/desktop/assets/runtime.json',
    ]) assertChangesDigest(root, first, relativePath);

    write(root, 'apps/web/.next/standalone-bundled/apps/web/server.js', 'web-v2');
    const changedIncluded = generate(root);
    assert.notEqual(changedIncluded.candidateInputDigest, first.candidateInputDigest, 'an included packaged input must change identity');

    write(root, '.env', 'SECRET=must-not-be-hashed');
    write(root, 'apps/desktop/dist/old-output/ignored.txt', 'generated output');
    const changedExcluded = generate(root);
    assert.equal(changedExcluded.candidateInputDigest, changedIncluded.candidateInputDigest, 'credentials and output directories must not change identity');

    write(root, 'external-asset.txt', 'must-not-follow');
    fs.symlinkSync('../../../external-asset.txt', path.join(root, 'apps/desktop/assets/external-link.txt'));
    assert.throws(
      () => generate(root),
      /Packaged identity input must not contain a symlink: .*assets\/external-link\.txt/,
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
