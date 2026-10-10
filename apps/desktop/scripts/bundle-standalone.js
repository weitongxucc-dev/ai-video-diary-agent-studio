#!/usr/bin/env node
/**
 * Materialise the runtime dependency graph emitted by Next.js standalone.
 * The traced standalone node_modules tree is authoritative: package.json may
 * also contain build/test dependencies that are intentionally not traced.
 */
const fs = require('fs');
const path = require('path');

function isSymlink(target) {
  try {
    return fs.lstatSync(target).isSymbolicLink();
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}

function resolveSymlink(target) {
  try {
    return fs.realpathSync(target);
  } catch (error) {
    if (error.code === 'ENOENT') {
      throw new Error(`Broken standalone dependency link: ${target}`);
    }
    throw error;
  }
}

function copyTree(source, destination) {
  const stat = fs.statSync(source);
  if (stat.isDirectory()) {
    fs.mkdirSync(destination, { recursive: true });
    for (const name of fs.readdirSync(source)) {
      const sourceChild = path.join(source, name);
      const destinationChild = path.join(destination, name);
      if (isSymlink(sourceChild)) {
        copyTree(resolveSymlink(sourceChild), destinationChild);
      } else {
        copyTree(sourceChild, destinationChild);
      }
    }
    return;
  }
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
}

function packageInstallRoot(packagePath) {
  const parts = path.resolve(packagePath).split(path.sep);
  const marker = parts.lastIndexOf('node_modules');
  if (marker < 0) throw new Error(`Cannot find node_modules ancestor for ${packagePath}`);
  return path.join(path.sep, ...parts.slice(1, marker + 1));
}

function materialisePackage(source, destination, activePackages) {
  const realSource = isSymlink(source) ? resolveSymlink(source) : fs.realpathSync(source);
  const stat = fs.statSync(realSource);
  if (!stat.isDirectory()) {
    throw new Error(`Standalone dependency is not a directory: ${source}`);
  }
  copyTree(realSource, destination);

  if (activePackages.has(realSource)) return;
  const nextActive = new Set(activePackages);
  nextActive.add(realSource);
  const installRoot = packageInstallRoot(realSource);
  materialiseInstalledEntries(installRoot, path.join(destination, 'node_modules'), realSource, nextActive);
}

function materialiseInstalledEntries(sourceRoot, destinationRoot, packageToSkip, activePackages) {
  if (!fs.existsSync(sourceRoot)) return;
  for (const name of fs.readdirSync(sourceRoot).sort()) {
    if (name === '.bin') continue;
    const source = path.join(sourceRoot, name);
    const destination = path.join(destinationRoot, name);
    const stat = fs.lstatSync(source);

    if (stat.isSymbolicLink()) {
      const realSource = resolveSymlink(source);
      if (realSource === packageToSkip) continue;
      if (!fs.existsSync(destination)) {
        materialisePackage(realSource, destination, activePackages);
      }
    } else if (stat.isDirectory()) {
      if (fs.existsSync(path.join(source, 'package.json'))) {
        if (fs.realpathSync(source) === packageToSkip) continue;
        if (!fs.existsSync(destination)) materialisePackage(source, destination, activePackages);
      } else {
        materialiseInstalledEntries(source, destination, packageToSkip, activePackages);
      }
    }
  }
}

function countSymlinks(root) {
  let count = 0;
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const child = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) count += 1;
      else if (entry.isDirectory()) walk(child);
    }
  }
  walk(root);
  return count;
}

function materialiseStandalone({ standaloneRoot, webBuild, webPublic, destination }) {
  const sourceWeb = path.join(standaloneRoot, 'apps', 'web');
  const sourceNodeModules = path.join(sourceWeb, 'node_modules');
  if (!fs.existsSync(sourceWeb)) {
    throw new Error(`Next standalone output not found at ${sourceWeb}`);
  }
  if (!fs.existsSync(sourceNodeModules)) {
    throw new Error(`Next standalone traced node_modules not found at ${sourceNodeModules}`);
  }

  const tracedEntries = fs.readdirSync(sourceNodeModules).filter((name) => name !== '.bin');
  for (const required of ['next', 'react']) {
    if (!tracedEntries.includes(required)) {
      throw new Error(`Required traced runtime dependency is missing: ${required}`);
    }
  }

  fs.rmSync(destination, { recursive: true, force: true });
  fs.mkdirSync(destination, { recursive: true });
  for (const name of fs.readdirSync(sourceWeb)) {
    if (name === 'node_modules') continue;
    copyTree(path.join(sourceWeb, name), path.join(destination, name));
  }

  const staticSource = path.join(webBuild, 'static');
  if (fs.existsSync(staticSource)) copyTree(staticSource, path.join(destination, '.next', 'static'));
  if (fs.existsSync(webPublic)) copyTree(webPublic, path.join(destination, 'public'));

  const destinationNodeModules = path.join(destination, 'node_modules');
  fs.mkdirSync(destinationNodeModules, { recursive: true });
  for (const name of tracedEntries.sort()) {
    materialisePackage(path.join(sourceNodeModules, name), path.join(destinationNodeModules, name), new Set());
  }

  for (const required of ['next', 'react']) {
    if (!fs.existsSync(path.join(destinationNodeModules, required, 'package.json'))) {
      throw new Error(`Required runtime dependency was not materialised: ${required}`);
    }
  }
  for (const required of [
    ['next', 'node_modules', 'styled-jsx', 'package.json'],
    ['next', 'node_modules', '@swc', 'helpers', 'package.json'],
  ]) {
    const packageFile = path.join(destinationNodeModules, ...required);
    if (!fs.existsSync(packageFile)) {
      throw new Error(`Required transitive runtime dependency was not materialised: ${packageFile}`);
    }
  }

  const symlinks = countSymlinks(destination);
  if (symlinks !== 0) throw new Error(`Standalone output contains ${symlinks} symbolic link(s)`);
  return { tracedEntries: tracedEntries.sort(), symlinks };
}

function main() {
  const repoRoot = path.resolve(__dirname, '..', '..', '..');
  const standaloneRoot = path.join(repoRoot, 'apps', 'web', '.next', 'standalone');
  const webBuild = path.join(repoRoot, 'apps', 'web', '.next');
  const webPublic = path.join(repoRoot, 'apps', 'web', 'public');
  const destination = path.join(webBuild, 'standalone-bundled', 'apps', 'web');
  const result = materialiseStandalone({ standaloneRoot, webBuild, webPublic, destination });
  console.log('Materialised standalone runtime dependencies:', result.tracedEntries.join(', '));
  console.log('  output:', destination);
  console.log('  symlinks remaining:', result.symlinks);
  console.log('  OK — standalone tree is materialised.');
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(`FAILED: ${error.message}`);
    process.exitCode = 1;
  }
}

module.exports = { materialiseStandalone };
