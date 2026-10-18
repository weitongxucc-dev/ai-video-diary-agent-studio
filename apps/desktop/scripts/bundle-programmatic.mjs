#!/usr/bin/env node
import {build} from 'esbuild';
import {mkdir, chmod, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve, dirname} from 'node:path';

// CLI and MCP remain thin clients of the same running desktop API. Bundle
// protocol dependencies so installed clients never resolve the developer's repo.
const root = fileURLToPath(new URL('../../../', import.meta.url));
const outdir = resolve(root, 'apps/desktop/dist/programmatic');
await mkdir(outdir, {recursive:true});
const result = await build({
  absWorkingDir:root,
  entryPoints:{yaohe:'scripts/yaohe.mjs', 'yaohe-mcp':'scripts/yaohe-mcp.mjs'},
  outdir, outExtension:{'.js':'.mjs'}, bundle:true,
  platform:'node', format:'esm', target:'node22',
  // CJS transitive packages can require Node builtins from the ESM bundle.
  banner:{js:"import {createRequire as bundleCreateRequire} from 'node:module'; const require = bundleCreateRequire(import.meta.url);"},
  legalComments:'eof', logLevel:'warning', metafile:true,
});
for (const entry of ['yaohe.mjs', 'yaohe-mcp.mjs']) {
  await chmod(resolve(outdir, entry), 0o755);
}
const packages = new Set();
for (const input of Object.keys(result.metafile.inputs).filter(path => path.includes('node_modules/'))) {
  let directory = dirname(resolve(root, input));
  while (directory.includes('node_modules')) {
    try {
      const manifest = JSON.parse(await readFile(resolve(directory, 'package.json'), 'utf8'));
      if (manifest.name) { packages.add(directory); break; }
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    directory = dirname(directory);
  }
}
const notices = [];
for (const directory of [...packages].sort()) {
  const manifest = JSON.parse(await readFile(resolve(directory, 'package.json'), 'utf8'));
  let license;
  for (const name of ['LICENSE', 'LICENSE.txt', 'LICENSE.md', 'license']) {
    try { license = await readFile(resolve(directory, name), 'utf8'); break; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  if (!license) throw new Error(`Missing bundled dependency license: ${manifest.name}`);
  notices.push(`${manifest.name}@${manifest.version}\n${license}`);
}
await writeFile(resolve(outdir, 'THIRD-PARTY-NOTICES.txt'), notices.join('\n\n'));
