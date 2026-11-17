// Exercise electron-builder's real resource filter, then load the copied Skills.
const assert = require('node:assert/strict');
const { mkdtemp, rm, access } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createRequire } = require('node:module');
const builderRequire = createRequire(require.resolve('electron-builder'));
const { FileMatcher, copyFiles } = builderRequire('app-builder-lib/out/fileMatcher');
const { build } = require('../package.json');

async function main() {
  const root = await mkdtemp(path.join(tmpdir(), 'yaohe-skill-bundle-'));
  try {
    const resource = build.extraResources.find(item => item.to === 'agent_runtime');
    const source = path.resolve(__dirname, '..', resource.from);
    const target = path.join(root, resource.to);
    await copyFiles([new FileMatcher(source, target, value => value, resource.filter)], null, false);
    const { loadSkillManifests } = await import(pathToFileURL(path.join(target, 'skills/manifest.mjs')).href);
    const manifests = await loadSkillManifests(path.join(target, 'skills'));
    for (const name of ['cut-as-code-video-cut', 'openfilm-replicate', 'editing-videos']) {
      assert.ok(manifests.has(name), `${name} must remain discoverable in the installed bundle`);
      assert.equal(manifests.get(name).runtime.runnable, false);
    }
    await assert.rejects(access(path.join(target, 'tests/skill-manifest.test.mjs')));
    console.log('Packaged Skill discovery: PASS');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
