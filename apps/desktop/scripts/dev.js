// Use Electron's own CLI lifecycle on both Windows and macOS.
process.env.NODE_ENV = 'development';
process.argv.splice(1, 1, require.resolve('electron/cli'), '.');
require('electron/cli');
