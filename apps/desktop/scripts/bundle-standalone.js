#!/usr/bin/env node
/**
 * Materialise Next.js standalone output for electron-builder packaging.
 *
 * Next.js standalone in a pnpm monorepo creates symlinks like
 *   web/node_modules/next -> ../../../node_modules/.pnpm/next@.../node_modules/next
 *
 * Inside .pnpm, package peer-dependencies are symlinked at the package's
 * node_modules level (e.g. ``.pnpm/next@.../node_modules/styled-jsx``),
 * NOT inside the package directory.  Node.js's standard module resolution
 * requires ``next/node_modules/styled-jsx``, so we must restructure.
 *
 * This script:
 * 1. Copies standalone/apps/web/* to standalone-bundled/apps/web/*
 *    and adds the static/public assets that Next intentionally omits from
 *    standalone output.
 * 2. For each top-level symlinked dep (next, react), follows the pnpm chain
 *    and reconstructs a standard node_modules tree with all transitive deps
 *    materialised as real files.
 *
 * Output: standalone-bundled/apps/web/ with zero symlinks, ready for
 * electron-builder extraResources.
 */

const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const STANDALONE = path.join(REPO_ROOT, 'apps', 'web', '.next', 'standalone');
const WEB_BUILD = path.join(REPO_ROOT, 'apps', 'web', '.next');
const WEB_PUBLIC = path.join(REPO_ROOT, 'apps', 'web', 'public');
const SRC = path.join(STANDALONE, 'apps', 'web');
const DST = path.join(REPO_ROOT, 'apps', 'web', '.next', 'standalone-bundled', 'apps', 'web');

if (!fs.existsSync(SRC)) {
  console.error('standalone output not found at', SRC);
  console.error('Run "pnpm --dir apps/web build" first.');
  process.exit(1);
}

// Remove previous
if (fs.existsSync(DST)) {
  fs.rmSync(DST, { recursive: true, force: true });
}

// ── Utilities ──

function isSymlink(p) {
  try { return fs.lstatSync(p).isSymbolicLink(); } catch (_) { return false; }
}

function realpathSafe(p) {
  try { return fs.realpathSync(p); } catch (_) { return null; }
}

/** Copy a file or directory (not a symlink) to dst recursively. */
function copyTree(src, dst) {
  const st = fs.statSync(src);
  if (st.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      const s = path.join(src, entry);
      const d = path.join(dst, entry);
      if (isSymlink(s)) {
        const real = realpathSafe(s);
        if (real) copyTree(real, d);
      } else {
        copyTree(s, d);
      }
    }
  } else {
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(src, dst);
  }
}

// ── Phase 1: copy everything except node_modules from web source ──

console.log('Materialising standalone deps (dereference + restructure)...');
console.log('  src:', SRC);
console.log('  dst:', DST);

fs.mkdirSync(DST, { recursive: true });
for (const entry of fs.readdirSync(SRC)) {
  if (entry === 'node_modules') continue;
  const s = path.join(SRC, entry);
  const d = path.join(DST, entry);
  copyTree(s, d);
}

// Next's standalone server deliberately omits `.next/static` and `public`.
// They are needed at runtime for stylesheets, JavaScript chunks, and any
// public assets, so materialise them next to server.js before packaging.
const staticSrc = path.join(WEB_BUILD, 'static');
if (fs.existsSync(staticSrc)) {
  copyTree(staticSrc, path.join(DST, '.next', 'static'));
}
if (fs.existsSync(WEB_PUBLIC)) {
  copyTree(WEB_PUBLIC, path.join(DST, 'public'));
}

// ── Phase 2: materialise top-level node_modules entries ──

const SRC_NM = path.join(SRC, 'node_modules');
const DST_NM = path.join(DST, 'node_modules');
fs.mkdirSync(DST_NM, { recursive: true });

const pnpmRoot = path.join(STANDALONE, 'node_modules', '.pnpm');
if (!fs.existsSync(pnpmRoot)) {
  console.error('ERROR: .pnpm store not found at', pnpmRoot);
  process.exit(1);
}

/**
 * Given a symlink path like
 *   standalone/apps/web/node_modules/next  (symlink)
 *     → ../../../node_modules/.pnpm/next@X/node_modules/next
 *
 * 1. Resolve the symlink to the real package dir in .pnpm
 * 2. Copy the package to DST_NM/<name> as a real directory
 * 3. Look at .pnpm/<pkg>/node_modules/ for peer-dependency symlinks
 * 4. Materialise those inside DST_NM/<name>/node_modules/
 */
function materialiseDep(entryName) {
  const symlinkPath = path.join(SRC_NM, entryName);
  if (!isSymlink(symlinkPath)) {
    // Not a symlink — copy as-is
    copyTree(symlinkPath, path.join(DST_NM, entryName));
    return;
  }

  const realPkg = realpathSafe(symlinkPath);
  if (!realPkg) {
    console.error('  WARNING: broken symlink for', entryName, '— skipping');
    return;
  }

  // Copy the package itself
  const dstPkg = path.join(DST_NM, entryName);
  copyTree(realPkg, dstPkg);

  // The pnpm package dir is something like
  //   .pnpm/next@15.5.18.../node_modules/next/
  // Its peer deps are at
  //   .pnpm/next@15.5.18.../node_modules/  (siblings of `next/`)
  const pnpmPkgNodeModules = path.dirname(realPkg); // parent = the pnpm node_modules for this package

  if (!fs.existsSync(pnpmPkgNodeModules)) return;

  /**
   * Materialise pnpm peer-dependency entries into dstBase.
   * Handles both flat symlinks (styled-jsx -> real) and scoped
   * real-directory containers (@swc/helpers where @swc/ is a dir
   * containing a symlink `helpers`).
   */
  function materialisePeerDeps(srcDir, dstBase, skipName) {
    for (const dep of fs.readdirSync(srcDir)) {
      if (dep === skipName) continue;

      const depPath = path.join(srcDir, dep);
      const depDst = path.join(dstBase, dep);

      if (isSymlink(depPath)) {
        const depReal = realpathSafe(depPath);
        if (depReal && !fs.existsSync(depDst)) {
          copyTree(depReal, depDst);
        }
      } else if (fs.statSync(depPath).isDirectory()) {
        // Scoped-package container: `@swc/` contains `helpers` (symlink)
        fs.mkdirSync(depDst, { recursive: true });
        materialisePeerDeps(depPath, depDst, null);
      }
    }
  }

  const dstPkgNM = path.join(dstPkg, 'node_modules');
  materialisePeerDeps(pnpmPkgNodeModules, dstPkgNM, entryName);
}

const webPackage = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'apps', 'web', 'package.json'), 'utf8'));
for (const entry of Object.keys(webPackage.dependencies || {})) {
  materialiseDep(entry);
}

// ── Sanity check ──

const nextPkgJson = path.join(DST_NM, 'next', 'package.json');
if (!fs.existsSync(nextPkgJson)) {
  console.error('FAILED: next/package.json not found at', nextPkgJson);
  process.exit(1);
}

const reactPkgJson = path.join(DST_NM, 'react', 'package.json');
if (!fs.existsSync(reactPkgJson)) {
  console.error('FAILED: react/package.json not found at', reactPkgJson);
  process.exit(1);
}

// Check that next can find styled-jsx and @swc/helpers
const styledJsxIn = path.join(DST_NM, 'next', 'node_modules', 'styled-jsx', 'package.json');
if (!fs.existsSync(styledJsxIn)) {
  console.error('FAILED: styled-jsx not materialised inside next/node_modules at', styledJsxIn);
  process.exit(1);
}
const swcHelpersIn = path.join(DST_NM, 'next', 'node_modules', '@swc', 'helpers', 'package.json');
if (!fs.existsSync(swcHelpersIn)) {
  console.error('FAILED: @swc/helpers not materialised inside next/node_modules at', swcHelpersIn);
  process.exit(1);
}

// Check zero symlinks in output
const symlinksRemaining = (() => {
  let count = 0;
  function walk(dir) {
    try {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isSymbolicLink()) count++;
        else if (entry.isDirectory()) walk(p);
      }
    } catch (_) {}
  }
  walk(DST);
  return count;
})();

console.log('  symlinks remaining in output:', symlinksRemaining);
if (symlinksRemaining > 0) {
  console.error('FAILED: symlinks still present after materialisation');
  process.exit(1);
}

console.log('  OK — all deps materialised, 0 symlinks.');
