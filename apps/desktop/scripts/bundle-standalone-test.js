#!/usr/bin/env node
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const { materialiseStandalone } = require('./bundle-standalone');

function makePackage(storeRoot, storeName, packageName) {
  const installRoot = path.join(storeRoot, `${storeName}@1`, 'node_modules');
  const packagePath = path.join(installRoot, ...packageName.split('/'));
  fs.mkdirSync(packagePath, { recursive: true });
  fs.writeFileSync(path.join(packagePath, 'package.json'), JSON.stringify({ name: packageName, main: 'index.js' }));
  fs.writeFileSync(path.join(packagePath, 'index.js'), `module.exports = ${JSON.stringify(packageName)};`);
  return { installRoot, packagePath };
}

function linkPackage(parentNodeModules, packageName, packagePath) {
  const link = path.join(parentNodeModules, ...packageName.split('/'));
  fs.mkdirSync(path.dirname(link), { recursive: true });
  fs.symlinkSync(packagePath, link, process.platform === 'win32' ? 'junction' : 'dir');
}

function fixture(root, { omit } = {}) {
  const standaloneRoot = path.join(root, 'standalone');
  const sourceWeb = path.join(standaloneRoot, 'apps', 'web');
  const tracedNodeModules = path.join(sourceWeb, 'node_modules');
  const storeRoot = path.join(standaloneRoot, 'node_modules', '.pnpm');
  const webBuild = path.join(root, 'web-build');
  const webPublic = path.join(root, 'web-public');
  const destination = path.join(root, 'owned-output', 'apps', 'web');
  fs.mkdirSync(tracedNodeModules, { recursive: true });
  fs.mkdirSync(path.join(sourceWeb, '.next'), { recursive: true });
  fs.mkdirSync(storeRoot, { recursive: true });
  fs.mkdirSync(path.join(webBuild, 'static'), { recursive: true });
  fs.mkdirSync(webPublic, { recursive: true });
  fs.writeFileSync(path.join(sourceWeb, 'server.js'), 'module.exports = true;');
  fs.writeFileSync(path.join(sourceWeb, 'package.json'), JSON.stringify({
    dependencies: { next: '*', react: '*', '@supabase/ssr': 'intentionally-untraced' },
  }));
  fs.writeFileSync(path.join(webBuild, 'static', 'chunk.js'), 'static');
  fs.writeFileSync(path.join(webPublic, 'asset.svg'), '<svg/>');

  const next = makePackage(storeRoot, 'next', 'next');
  const react = makePackage(storeRoot, 'react', 'react');
  const styled = makePackage(storeRoot, 'styled-jsx', 'styled-jsx');
  const helpers = makePackage(storeRoot, 'swc-helpers', '@swc/helpers');
  const css = makePackage(storeRoot, 'css', 'css');
  for (const [name, value] of [['next', next], ['react', react]]) {
    if (name !== omit) linkPackage(tracedNodeModules, name, value.packagePath);
  }
  linkPackage(next.installRoot, 'styled-jsx', styled.packagePath);
  linkPackage(next.installRoot, '@swc/helpers', helpers.packagePath);
  linkPackage(styled.installRoot, 'css', css.packagePath);

  return { standaloneRoot, webBuild, webPublic, destination, tracedNodeModules, storeRoot, nextInstallRoot: next.installRoot };
}

function assertNoSymlinks(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const child = path.join(root, entry.name);
    assert.equal(entry.isSymbolicLink(), false, `unexpected symlink: ${child}`);
    if (entry.isDirectory()) assertNoSymlinks(child);
  }
}

const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'standalone-bundle-test-'));
try {
  const paths = fixture(path.join(temporaryRoot, 'trace-subset'));
  const result = materialiseStandalone(paths);
  assert.deepEqual(result.tracedEntries, ['next', 'react']);
  assert.equal(result.symlinks, 0);
  assertNoSymlinks(paths.destination);
  assert.equal(fs.existsSync(path.join(paths.destination, '.next', 'static', 'chunk.js')), true);
  assert.equal(fs.existsSync(path.join(paths.destination, 'public', 'asset.svg')), true);
  const fromServer = createRequire(path.join(paths.destination, 'server.js'));
  assert.equal(fromServer.resolve('next'), fs.realpathSync(path.join(paths.destination, 'node_modules', 'next', 'index.js')));
  assert.equal(fromServer.resolve('react'), fs.realpathSync(path.join(paths.destination, 'node_modules', 'react', 'index.js')));
  const fromNext = createRequire(path.join(paths.destination, 'node_modules', 'next', 'index.js'));
  assert.equal(fromNext.resolve('styled-jsx'), fs.realpathSync(path.join(paths.destination, 'node_modules', 'next', 'node_modules', 'styled-jsx', 'index.js')));
  assert.equal(fromNext.resolve('@swc/helpers'), fs.realpathSync(path.join(paths.destination, 'node_modules', 'next', 'node_modules', '@swc', 'helpers', 'index.js')));
  const fromStyled = createRequire(path.join(paths.destination, 'node_modules', 'next', 'node_modules', 'styled-jsx', 'index.js'));
  assert.equal(fromStyled.resolve('css'), fs.realpathSync(path.join(paths.destination, 'node_modules', 'next', 'node_modules', 'styled-jsx', 'node_modules', 'css', 'index.js')));

  for (const missing of ['next', 'react']) {
    const missingPaths = fixture(path.join(temporaryRoot, `missing-${missing}`), { omit: missing });
    assert.throws(() => materialiseStandalone(missingPaths), new RegExp(`Required traced runtime dependency is missing: ${missing}`));
  }

  const brokenPaths = fixture(path.join(temporaryRoot, 'broken-link'));
  fs.symlinkSync(path.join(brokenPaths.storeRoot, 'not-present'), path.join(brokenPaths.nextInstallRoot, 'broken-peer'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => materialiseStandalone(brokenPaths), /Broken standalone dependency link/);
  console.log('PASS: traced subset, transitive module resolution, required roots, broken links, static/public, zero symlinks');
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
}
