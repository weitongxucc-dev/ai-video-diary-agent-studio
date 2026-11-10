const fs = require('node:fs');
const path = require('node:path');

// Next's traced pnpm directories use symlink() without a Windows link type.
// Only its owned standalone output is adapted; no system setting is changed.
if (process.platform === 'win32') {
  const root = path.resolve(__dirname, '../../web/.next/standalone');
  const symlink = fs.promises.symlink.bind(fs.promises);
  fs.promises.symlink = async (target, destination, type) => {
    const relative = path.relative(root, path.resolve(destination));
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      return symlink(target, destination, type);
    }
    const source = path.resolve(path.dirname(destination), target);
    const info = await fs.promises.stat(source);
    if (info.isDirectory()) return symlink(source, destination, 'junction');
    return fs.promises.copyFile(source, destination, fs.constants.COPYFILE_EXCL);
  };
}
