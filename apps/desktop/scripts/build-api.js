const path = require('node:path');
const { spawnSync } = require('node:child_process');

const desktop = path.resolve(__dirname, '..');
const api = path.resolve(desktop, '../api');
const python = process.platform === 'win32'
  ? path.join(api, '.venv', 'Scripts', 'python.exe')
  : path.join(api, '.venv', 'bin', 'python');
const result = spawnSync(python, [
  '-m', 'PyInstaller', 'pyinstaller.spec',
  '--distpath', path.join(desktop, 'dist'),
  '--workpath', path.join(desktop, 'build-api'), '--clean', '--noconfirm',
], { cwd: api, env: process.env, stdio: 'inherit' });
if (result.error) throw result.error;
process.exitCode = result.status || (result.signal ? 1 : 0);
