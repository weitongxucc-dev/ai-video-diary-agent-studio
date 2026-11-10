const path = require('node:path');
const { spawnSync } = require('node:child_process');

const desktop = path.resolve(__dirname, '..');
const web = path.resolve(desktop, '../web');
const env = { ...process.env, NEXT_PUBLIC_AUTH_MODE: process.env.NEXT_PUBLIC_AUTH_MODE || 'local' };
const steps = [
  [path.join(__dirname, 'write-distribution-config.js'), desktop],
  [require.resolve('next/dist/bin/next', { paths: [web] }), web, 'build'],
  [path.join(__dirname, 'bundle-standalone.js'), desktop],
];

for (const [script, cwd, ...args] of steps) {
  const preload = process.platform === 'win32' && cwd === web
    ? ['--require', path.join(__dirname, 'windows-standalone-links.js')]
    : [];
  const result = spawnSync(process.execPath, [...preload, script, ...args], { cwd, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
