import {execFileSync} from 'node:child_process';
import {realpathSync} from 'node:fs';
import {dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

// Resolve from the selected CLI installation, including npm shims on Windows.
export const installedPiSdk = () => {
  const locator = process.platform === 'win32' ? 'where.exe' : 'which';
  const executable = execFileSync(locator, ['pi'], {encoding:'utf8'}).trim().split(/\r?\n/)[0];
  const moduleUrl = execFileSync(process.execPath, ['--input-type=module', '-e',
    "console.log(import.meta.resolve('@earendil-works/pi-coding-agent'))"],
    {cwd:dirname(realpathSync(executable)), encoding:'utf8'}).trim();
  return fileURLToPath(moduleUrl);
};
