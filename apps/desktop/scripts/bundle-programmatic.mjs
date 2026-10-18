#!/usr/bin/env node
import {build} from 'esbuild';
import {mkdir, chmod} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

// CLI and MCP remain thin clients of the same running desktop API. Bundle
// protocol dependencies so installed clients never resolve the developer's repo.
const root = fileURLToPath(new URL('../../../', import.meta.url));
const outdir = resolve(root, 'apps/desktop/dist/programmatic');
await mkdir(outdir, {recursive:true});
await build({
  absWorkingDir:root,
  entryPoints:{yaohe:'scripts/yaohe.mjs', 'yaohe-mcp':'scripts/yaohe-mcp.mjs'},
  outdir, outExtension:{'.js':'.mjs'}, bundle:true,
  platform:'node', format:'esm', target:'node22',
  // CJS transitive packages can require Node builtins from the ESM bundle.
  banner:{js:"import {createRequire as bundleCreateRequire} from 'node:module'; const require = bundleCreateRequire(import.meta.url);"},
  legalComments:'eof', logLevel:'warning',
});
for (const entry of ['yaohe.mjs', 'yaohe-mcp.mjs']) {
  await chmod(resolve(outdir, entry), 0o755);
}
